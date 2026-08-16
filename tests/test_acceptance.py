from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from atlas_morning.assign import assign_units, select_pack
from atlas_morning.config import load_config, save_aliases, sender_is_relevant
from atlas_morning.extract import extract_entries
from atlas_morning.filter import (
    build_reporting_units,
    filter_relevant_messages,
    is_plausible_equipment_id,
)
from atlas_morning.models import Message, ReportingUnit
from atlas_morning.intervals import (
    Interval,
    find_intervals,
    reported_work_interval,
    suspicious_numeric_interval,
)
from atlas_morning.load import load_messages
from atlas_morning.pack import build_pack, render_pack
from atlas_morning.reconcile import apply_corrections, flag_entries

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "export_excerpt.txt"
CONFIG_PATH = ROOT / "config" / "v1.json"


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.messages = load_messages(FIXTURE)

    def pack(self, day: date, aliases=None, corrections=None):
        return build_pack(
            self.messages,
            self.config,
            day,
            aliases=aliases,
            corrections=corrections,
        )

    def senders_in(self, pack) -> set[str]:
        return {unit.message.sender for unit in pack.units}

    def items(self, pack) -> list[str]:
        return [entry.item for entry in pack.entries]

    def test_01_night_after_midnight_same_operational_day(self):
        pack = self.pack(date(2026, 5, 5))
        jurie = [
            unit
            for unit in pack.units
            if unit.message.sender == "Jurie Venter"
            and unit.message.timestamp.hour == 3
        ]
        self.assertTrue(jurie)
        self.assertEqual(jurie[0].operational_day, date(2026, 5, 5))
        self.assertEqual(jurie[0].shift, "night")

    def test_02_day_report_after_day_shift(self):
        pack = self.pack(date(2026, 5, 5))
        lyle = [
            unit
            for unit in pack.units
            if unit.message.sender == "Lyle" and unit.message.timestamp.hour == 15
        ]
        self.assertTrue(lyle)
        self.assertEqual(lyle[0].operational_day, date(2026, 5, 5))
        self.assertEqual(lyle[0].shift, "day")

    def test_03_next_operational_day_excluded(self):
        pack = self.pack(date(2026, 5, 5))
        later = [
            unit
            for unit in pack.units
            if unit.message.timestamp.date() == date(2026, 5, 27)
        ]
        self.assertEqual(later, [])

    def test_04_late_post_does_not_change_day(self):
        pack = self.pack(date(2026, 5, 25))
        late = [
            unit
            for unit in pack.units
            if unit.message.timestamp.hour == 6 and unit.message.timestamp.minute == 30
        ]
        self.assertTrue(late)
        self.assertEqual(late[0].operational_day, date(2026, 5, 25))
        self.assertTrue(late[0].late)
        self.assertTrue(any("Late-posted" in flag for flag in pack.flags))
        next_day = self.pack(date(2026, 5, 26))
        self.assertFalse(
            any(
                unit.message.timestamp.hour == 6 and unit.message.timestamp.minute == 30
                for unit in next_day.units
            )
        )

    def test_05_meeting_is_not_the_boundary(self):
        pack = self.pack(date(2026, 5, 25))
        after_meeting = [
            unit
            for unit in pack.units
            if unit.message.timestamp.hour == 5 and unit.message.timestamp.minute == 40
        ]
        self.assertTrue(after_meeting)
        self.assertEqual(after_meeting[0].operational_day, date(2026, 5, 25))

    def test_05a_missing_night_report_flagged(self):
        pack = self.pack(date(2026, 5, 29))
        self.assertTrue(any(unit.shift == "day" for unit in pack.units))
        self.assertFalse(any(unit.shift == "night" for unit in pack.units))
        self.assertTrue(any("night-shift report missing" in flag.lower() for flag in pack.flags))

    def test_06_other_departments_dropped(self):
        pack = self.pack(date(2026, 1, 19))
        senders = self.senders_in(pack)
        self.assertNotIn("Francois Coetzee", senders)
        self.assertFalse(any(s.startswith("+27") for s in senders))
        for entry in pack.entries:
            self.assertNotIn("Standdowns", entry.work_finding)

    def test_07_no_tmm_prefix_filter(self):
        pack = self.pack(date(2026, 1, 8))
        blob = " ".join(self.items(pack)).lower()
        self.assertTrue(any("adr" in item.lower() or "sec" in item.lower() for item in self.items(pack)))
        self.assertTrue(pack.entries)

    def test_08_fanie_tenure(self):
        from datetime import datetime

        fanie_day = datetime(2026, 1, 8, 15, 37)
        jurie_early = datetime(2026, 4, 20, 15, 0)
        overlap = datetime(2026, 4, 29, 12, 0)
        after = datetime(2026, 5, 5, 15, 0)
        self.assertTrue(sender_is_relevant("Fanie Lombard", fanie_day, self.config))
        self.assertFalse(sender_is_relevant("Jurie Venter", fanie_day, self.config))
        self.assertFalse(sender_is_relevant("Jurie Venter", jurie_early, self.config))
        self.assertTrue(sender_is_relevant("Fanie Lombard", overlap, self.config))
        self.assertTrue(sender_is_relevant("Jurie Venter", overlap, self.config))
        self.assertFalse(sender_is_relevant("Fanie Lombard", after, self.config))
        self.assertTrue(sender_is_relevant("Jurie Venter", after, self.config))
        jan = self.pack(date(2026, 1, 8))
        self.assertIn("Fanie Lombard", self.senders_in(jan))
        self.assertNotIn("Jurie Venter", self.senders_in(jan))
        may = self.pack(date(2026, 5, 5))
        self.assertIn("Jurie Venter", self.senders_in(may))

    def test_09_no_invented_clocks(self):
        pack = self.pack(date(2026, 1, 6))
        sos = [entry for entry in pack.entries if "sos" in entry.period_raw.lower()]
        self.assertTrue(sos)
        for entry in sos:
            self.assertEqual(entry.reported_work_interval, "")
            self.assertNotIn("06:00", entry.period_raw)
            self.assertNotIn("14:00", entry.period_raw)

    def test_10_numeric_duration_not_downtime(self):
        pack = self.pack(date(2026, 5, 28))
        hose = [
            entry
            for entry in pack.entries
            if "20h40" in entry.period_raw.replace(" ", "")
            or "20h40" in entry.period_raw
        ]
        self.assertTrue(hose)
        self.assertEqual(hose[0].reported_work_interval, "1 h 05 min")
        rendered = render_pack(pack)
        self.assertIn("reported work/activity interval", rendered)
        self.assertNotIn("1 h 05 min downtime", rendered.lower())

    def test_11_still_busy_no_duration(self):
        intervals = find_intervals("09:30-Still busy")
        self.assertTrue(intervals)
        self.assertEqual(reported_work_interval(intervals[0]), "")
        self.assertEqual(intervals[0].end_kind, "still_busy")

    def test_12_no_operator_not_operational(self):
        pack = self.pack(date(2026, 5, 25))
        hits = [
            entry
            for entry in pack.entries
            if any("no operator" in v.lower() for v in entry.verbatim_exceptions)
            or "no operator" in entry.follow_up.lower()
            or "no operator" in entry.work_finding.lower()
        ]
        self.assertTrue(hits)
        for entry in hits:
            self.assertNotEqual(entry.last_reported_state, "Reported operational")
            self.assertEqual(entry.last_reported_state, "Not tested")

    def test_13_media_marker(self):
        pack = self.pack(date(2026, 5, 28))
        media = [entry for entry in pack.entries if entry.media_present]
        self.assertTrue(media)
        self.assertTrue(any("Media" in flag for entry in media for flag in entry.flags))

    def test_14_user_stream_absent_not_reassigned(self):
        jaco = [m for m in self.messages if m.sender == "Jaco Fouché"]
        self.assertTrue(jaco)
        for day in (date(2026, 1, 19), date(2026, 1, 20), date(2026, 1, 6)):
            pack = self.pack(day)
            self.assertNotIn("Jaco Fouché", self.senders_in(pack))
        # still in loaded source
        pack = self.pack(date(2026, 1, 20))
        self.assertTrue(any(m.sender == "Jaco Fouché" for m in pack.loaded_messages))

    def test_14a_unclear_sender_not_guessed(self):
        pack = self.pack(date(2026, 1, 12))
        self.assertFalse(any(entry.item.upper().startswith("SST99") for entry in pack.entries))
        self.assertTrue(
            any("uncertain" in flag.lower() and "J Venter" in flag for flag in pack.flags)
            or "J Venter" not in self.senders_in(pack)
        )

    def test_15_same_item_two_jobs_not_merged(self):
        pack = self.pack(date(2026, 7, 2))
        l91 = [entry for entry in pack.entries if entry.item_key.endswith("91") or "L91" in entry.item.upper()]
        self.assertGreaterEqual(len(l91), 2)

    def test_16_l91_style_flag_not_merge(self):
        pack = self.pack(date(2026, 5, 27))
        l91 = [entry for entry in pack.entries if "L91" in entry.item.upper()]
        self.assertEqual(len(l91), 2)
        self.assertTrue(
            any("Possible continuation" in flag for entry in l91 for flag in entry.flags)
        )

    def test_17_overlap_not_summed(self):
        pack = self.pack(date(2026, 5, 27))
        stc = [entry for entry in pack.entries if "STC14" in entry.item.upper()]
        self.assertEqual(len(stc), 2)
        rendered = render_pack(pack)
        self.assertNotIn("6 h", rendered)
        self.assertTrue(any(entry.overlap_noted for entry in stc))
        self.assertNotIn("downtime total", rendered.lower())

    def test_18_explicit_continue_one_entry(self):
        pack = self.pack(date(2026, 1, 8))
        cont = [
            entry
            for entry in pack.entries
            if "cont to assemble" in entry.work_finding.lower()
            or "cont to assemble" in entry.what_happened.lower()
        ]
        self.assertTrue(cont)
        self.assertEqual(len(cont), 1)

    def test_19_conflict_both_visible(self):
        pack = self.pack(date(2026, 5, 28))
        rlh = [entry for entry in pack.entries if "RLH5" in entry.item.upper()]
        self.assertGreaterEqual(len(rlh), 2)
        states = {entry.last_reported_state for entry in rlh}
        self.assertGreater(len(states), 1)
        self.assertTrue(any("Conflicting" in flag for entry in rlh for flag in entry.flags))

    def test_20_reported_not_live(self):
        pack = self.pack(date(2026, 1, 12))
        running = [
            entry
            for entry in pack.entries
            if "running" in entry.work_finding.lower() or entry.last_reported_state.startswith("Reported")
        ]
        self.assertTrue(running)
        for entry in pack.entries:
            self.assertNotIn("currently running", entry.last_reported_state.lower())

    def test_21_interval_not_called_downtime(self):
        interval = find_intervals("20h40 - 21h45")[0]
        self.assertEqual(reported_work_interval(interval), "1 h 05 min")
        pack = self.pack(date(2026, 5, 28))
        text = render_pack(pack)
        self.assertIn("reported work/activity interval", text)

    def test_22_technical_meaning_survives(self):
        pack = self.pack(date(2026, 1, 12))
        hose = [
            entry
            for entry in pack.entries
            if "tappet" in entry.work_finding.lower() or "tappet" in entry.what_happened.lower()
        ]
        self.assertTrue(hose)
        self.assertIn("gasket", hose[0].work_finding.lower())

    def test_23_alias_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.json"
            save_aliases(path, {"item_aliases": {"STC9": "STC09", "STC09": "STC09"}})
            aliases = json.loads(path.read_text(encoding="utf-8"))
            pack = self.pack(date(2026, 5, 5), aliases=aliases)
            stc = [entry for entry in pack.entries if "STC" in entry.item.upper() and "9" in entry.item]
            if stc:
                keys = {entry.item_key for entry in stc}
                self.assertEqual(len(keys), 1)

    def test_24_interpretation_correction_is_local(self):
        pack = self.pack(date(2026, 5, 27))
        flagged = any(
            "Possible continuation" in flag
            for entry in pack.entries
            if "L91" in entry.item.upper()
            for flag in entry.flags
        )
        self.assertTrue(flagged)
        corrected = apply_corrections(
            pack.entries,
            {"dismiss_continuity_items": ["L91"]},
        )
        self.assertFalse(
            any(
                "Possible continuation" in flag
                for entry in corrected
                if "L91" in entry.item.upper()
                for flag in entry.flags
            )
        )
        again = self.pack(date(2026, 5, 27))
        self.assertTrue(
            any(
                "Possible continuation" in flag
                for entry in again.entries
                if "L91" in entry.item.upper()
                for flag in entry.flags
            )
        )

    def test_25_unmodified_supervisor_format(self):
        raw = FIXTURE.read_text(encoding="utf-8")
        self.assertIn("Dialy Report", raw)
        self.assertIn("Tmm night shift", raw)

    def test_26_ordinary_row_unflagged(self):
        pack = self.pack(date(2026, 1, 12))
        ordinary = [
            entry
            for entry in pack.entries
            if entry.item.upper().startswith("SST20")
            or entry.item.upper().startswith("SST20")
        ]
        # Sst20 08h30-12h30 replace tappet Running — single item, complete
        sst20 = [entry for entry in pack.entries if "SST20" in entry.item.upper() or "Sst20" in entry.item]
        self.assertTrue(sst20)
        self.assertFalse(sst20[0].flags)

    def test_user_messages_not_deleted_from_load(self):
        self.assertTrue(any(m.sender == "Jaco Fouché" for m in self.messages))
        self.assertTrue(any("file attached" in (m.text.lower() + " ".join(m.media_refs).lower()) or m.media_refs for m in self.messages if m.sender == "Jaco Fouché"))


class May5BehaviourFixes(unittest.TestCase):
    """Regressions from the first real 2026-05-05 pack review."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.messages = load_messages(FIXTURE)
        cls.pack = build_pack(cls.messages, cls.config, date(2026, 5, 5))
        cls.rendered = render_pack(cls.pack)

    def test_arb4_duration_withheld_not_rewritten(self):
        arb = [e for e in self.pack.entries if e.item.upper().replace(" ", "") == "ARB4"]
        self.assertTrue(arb)
        entry = arb[0]
        self.assertIn("10:30", entry.period_raw)
        self.assertIn("00:10", entry.period_raw)
        self.assertEqual(entry.reported_work_interval, "")
        self.assertTrue(entry.interval_ambiguous)
        self.assertNotIn("13 h 40 min", self.rendered)
        self.assertNotIn("22:30", entry.period_raw)
        self.assertTrue(any("Ambiguous night-shift times" in f for f in entry.flags))

    def test_tyre_work_not_attached_to_sst15(self):
        sst15 = [e for e in self.pack.entries if "SST15" in e.item.upper()]
        self.assertTrue(sst15)
        self.assertNotIn("L103", sst15[0].work_finding)
        self.assertNotIn("tubeless", sst15[0].work_finding.lower())
        tyre = [
            e
            for e in self.pack.entries
            if "L103" in e.item.upper() or "tubeless" in e.work_finding.lower()
        ]
        self.assertTrue(tyre)
        self.assertIn("L97", tyre[0].item.upper() + tyre[0].work_finding.upper())

    def test_sst21_same_machine_is_not_continuity(self):
        sst21 = [e for e in self.pack.entries if "SST21" in e.item.upper()]
        self.assertEqual(len(sst21), 2)
        self.assertFalse(
            any("Possible continuation" in f for e in sst21 for f in e.flags)
        )

    def test_absent_is_not_a_machine_with_state(self):
        self.assertFalse(any(e.item.lower() == "absent" for e in self.pack.entries))
        self.assertNotIn("| Absent |", self.rendered)
        attendance = [e for e in self.pack.entries if e.work_character == "attendance"]
        for entry in attendance:
            self.assertNotEqual(entry.last_reported_state, "State not established from report")
            self.assertNotIn("State not established from report", entry.flags)


class ExceptionAndItemQuality(unittest.TestCase):
    def _extract(self, text: str, shift: str = "night"):
        unit = ReportingUnit(
            message=Message(
                sender="Lyle",
                timestamp=datetime(2026, 3, 8, 3, 0),
                text=text,
            ),
            shift=shift,
        )
        cfg = load_config(CONFIG_PATH)
        return extract_entries(unit, cfg)

    def test_prose_fragments_are_not_equipment(self):
        for token in (
            "Replace 3",
            "Labor 100",
            "On 763L",
            "Find 2",
            "Burst 2x",
            "Load 1x",
            "Load 2x",
            "from 1",
            "of 1",
            "Workforce 100",
            "Cut 20m",
        ):
            self.assertFalse(is_plausible_equipment_id(token), token)
        for token in ("SST21", "L91", "ARB4", "STC14", "RLH3", "Stvgt 01", "NSC02"):
            self.assertTrue(is_plausible_equipment_id(token), token)

    def test_replace_n_stays_work_not_item(self):
        entries = self._extract(
            "Tmm night shift\n"
            "STC14\n"
            "21h15--00h35\n"
            "Replace 3 broken brackets for the hydraulic hoses on main boom\n"
        )
        items = [e.item.upper().replace(" ", "") for e in entries]
        self.assertIn("STC14", items)
        self.assertFalse(any(i.startswith("REPLACE") for i in items))
        stc = next(e for e in entries if "STC14" in e.item.upper())
        self.assertIn("Replace 3 broken brackets", stc.work_finding)

    def test_labor_percent_not_an_item(self):
        entries = self._extract("Daily Report\nL91\n10h00 - 11h00\nRepair brakes.\nLabor 100%.\n")
        self.assertFalse(any("labor" in e.item.lower() for e in entries))

    def test_on_level_and_find_n_and_burst_load_not_items(self):
        entries = self._extract(
            "Tmm night shift\n"
            "RLH3 22h00 - 23h00\n"
            "On 763L workshop\n"
            "Find 2 spools on transmission block broken\n"
            "Burst 2x hydrolic hose\n"
            "Load 1x drifter\n"
            "Load 2x tramming pumps\n"
        )
        names = {e.item.lower() for e in entries}
        for bad in ("on 763l", "find 2", "burst 2x", "load 1x", "load 2x"):
            self.assertNotIn(bad, names)
        self.assertTrue(any("RLH3" in e.item.upper() for e in entries))

    def test_state_not_established_is_row_not_exception(self):
        pack = build_pack(
            load_messages(FIXTURE),
            load_config(CONFIG_PATH),
            date(2026, 5, 5),
        )
        unset = [
            e
            for e in pack.entries
            if e.last_reported_state == "State not established from report"
        ]
        self.assertTrue(unset)
        self.assertFalse(
            any("State not established from report" in f for e in unset for f in e.flags)
        )
        self.assertFalse(
            any(f == "State not established from report" for f in pack.flags)
        )


class FocusedReplayDateFixes(unittest.TestCase):
    """Regressions from 2026-01-20, 02-18, 03-08, 03-09, 04-09."""

    def _extract(self, text: str, shift: str = "night"):
        unit = ReportingUnit(
            message=Message(
                sender="Lyle",
                timestamp=datetime(2026, 2, 19, 2, 38),
                text=text,
            ),
            shift=shift,
        )
        return extract_entries(unit, load_config(CONFIG_PATH))

    def test_glued_machine_header_l91(self):
        entries = self._extract(
            "L9122h15 - 22h55\n"
            "Fit tyres.\n"
            "Running\n"
        )
        self.assertTrue(any("L91" in e.item.upper() for e in entries))
        l91 = next(e for e in entries if "L91" in e.item.upper())
        self.assertIn("22h15", l91.period_raw.replace(" ", ""))
        self.assertIn("22h55", l91.period_raw.replace(" ", ""))
        self.assertNotIn("L9122", l91.item.replace(" ", ""))

    def test_farm_gates_not_owned_by_sst12(self):
        entries = self._extract(
            "Sst12\n"
            "sos - 21h10\n"
            "Repair  wire harness  and replace amber light.\n"
            "21h00 - 0045\n"
            "Repair  farm gates and post\n"
            "Running\n"
        )
        sst12 = [e for e in entries if "SST12" in e.item.upper().replace(" ", "")]
        self.assertTrue(sst12)
        self.assertFalse(any("farm" in e.work_finding.lower() for e in sst12))
        self.assertTrue(
            any("farm" in (e.item + e.work_finding).lower() for e in entries)
        )

    def test_empty_scotch_car_not_on_rlh3(self):
        entries = self._extract(
            "Rlh3 23h30 - 00h15\n"
            "Showing cable fault.\n"
            "Running.\n"
            "Empty  scotch car.\n"
            "Labor 100%\n"
        )
        rlh = next(e for e in entries if "RLH3" in e.item.upper())
        self.assertNotIn("scotch", rlh.work_finding.lower())
        self.assertFalse(any("labor" in e.item.lower() for e in entries))
        self.assertTrue(any("scotch" in (e.item + e.work_finding).lower() for e in entries))

    def test_second_interval_visible_same_machine(self):
        entries = self._extract(
            "Stc12\n"
            "Replace bosh pump\n"
            "21h00--00h00\n"
            "Brakes binding\n"
            "Dayshift to check please\n"
            "01h14--03h00\n"
        )
        stc = [e for e in entries if "STC12" in e.item.upper().replace(" ", "")]
        self.assertGreaterEqual(len(stc), 2)
        first = next(e for e in stc if "21h00" in e.period_raw)
        second = next(e for e in stc if "01h14" in e.period_raw)
        self.assertIn("bosh pump", first.work_finding.lower())
        self.assertNotIn("brakes binding", first.work_finding.lower())
        self.assertIn("brakes binding", second.work_finding.lower())
        self.assertNotIn("bosh pump", second.work_finding.lower())

    def test_lyle_second_interval_keeps_its_own_time_and_work(self):
        entries = self._extract(
            "Tdr9 20h50 - 21h55\n"
            "Replace Hydraulic  hose.\n"
            "Running\n"
            "00h00 - 01h30\n"
            "Move sec4 into entrance  of bay 1\n"
        )
        tdr = [e for e in entries if "TDR9" in e.item.upper()]
        self.assertGreaterEqual(len(tdr), 2)
        first = next(e for e in tdr if "20h50" in e.period_raw)
        second = next(e for e in tdr if "00h00" in e.period_raw or "00h00" in e.period_raw.replace(" ", ""))
        self.assertIn("hose", first.work_finding.lower())
        self.assertIn("sec4", second.work_finding.lower())
        self.assertNotEqual(first.period_raw, second.period_raw)
        from atlas_morning.reconcile import flag_entries
        flagged = flag_entries(tdr)
        self.assertFalse(any("Overlapping" in f for e in flagged for f in e.flags))

    def test_backwards_interval_withheld(self):
        interval = find_intervals("22h00 - 20h45")[0]
        self.assertTrue(suspicious_numeric_interval(interval, "night"))
        self.assertEqual(reported_work_interval(interval, "night"), "")
        entries = self._extract(
            "Tdr10 22h00 - 20h45\nCharge and fit Accumulators.\nRunning\n"
        )
        tdr = next(e for e in entries if "TDR10" in e.item.upper())
        self.assertEqual(tdr.reported_work_interval, "")
        self.assertTrue(tdr.interval_ambiguous)
        self.assertNotIn("22 h", tdr.reported_work_interval)

    def test_progression_not_conflict(self):
        from atlas_morning.reconcile import flag_entries
        from atlas_morning.models import Entry

        earlier = Entry(
            item="ARB4",
            item_key="ARB4",
            period_raw="11h50 - eos",
            start=None,
            end=None,
            start_kind="missing",
            end_kind="eos",
            what_happened="fitting broke",
            work_finding="Paul still busy",
            last_reported_state="Still under repair",
            follow_up="",
            people="",
            work_character="",
            media_present=False,
            source_ref="2026-04-09 16:00:00|Lyle",
        )
        later = Entry(
            item="ARB4",
            item_key="ARB4",
            period_raw="21h00--22h00",
            start=(21, 0),
            end=(22, 0),
            start_kind="numeric",
            end_kind="numeric",
            what_happened="Remove broken fitting , test all ok",
            work_finding="test all ok",
            last_reported_state="Reported operational",
            follow_up="",
            people="",
            work_character="",
            media_present=False,
            source_ref="2026-04-10 02:22:00|Fanie Lombard",
        )
        flagged = flag_entries([earlier, later])
        self.assertFalse(
            any("Conflicting last-reported" in f for e in flagged for f in e.flags)
        )

    def test_dialy_report_after_midnight_is_night_from_clocks(self):
        from atlas_morning.assign import assign_unit
        from atlas_morning.models import ReportingUnit

        unit = ReportingUnit(
            message=Message(
                sender="Fanie Lombard",
                timestamp=datetime(2026, 3, 9, 3, 56),
                text="Dialy Report\nStc12\nReplace bosh pump\n21h00--00h00\n",
            )
        )
        assigned = assign_unit(unit, load_config(CONFIG_PATH))
        self.assertEqual(assigned.shift, "night")
        self.assertEqual(assigned.operational_day, date(2026, 3, 8))

    def test_chatter_followup_not_attached(self):
        from atlas_morning.filter import build_reporting_units
        from atlas_morning.load import parse_whatsapp_text

        messages = parse_whatsapp_text(
            "18/02/2026, 14:00 - Fanie Lombard: Dialy Report\n"
            "L97\nGot a flat tyre\n"
            "\n"
            "18/02/2026, 14:05 - Fanie Lombard: No sir it was used the entire shift sst 19\n"
            "Jan report by Jaco oor Rlh5\n"
        )
        units = build_reporting_units(messages)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].extra_sources, [])
        entries = extract_entries(units[0], load_config(CONFIG_PATH))
        blob = " ".join(e.work_finding for e in entries).lower()
        self.assertNotIn("no sir", blob)


class IntervalUnitTests(unittest.TestCase):
    def test_sos_eos_no_duration(self):
        interval = find_intervals("sos - 14h30")[0]
        self.assertEqual(interval.start_kind, "sos")
        self.assertEqual(reported_work_interval(interval), "")

    def test_numeric(self):
        interval = find_intervals("20h40 - 21h45")[0]
        self.assertEqual(reported_work_interval(interval), "1 h 05 min")

    def test_night_daytime_start_wrap_withheld(self):
        from atlas_morning.intervals import suspicious_night_wrap

        interval = find_intervals("10:30-00:10")[0]
        self.assertTrue(suspicious_night_wrap(interval, "night"))
        self.assertEqual(reported_work_interval(interval, "night"), "")
        self.assertEqual(reported_work_interval(interval, "day"), "")
        evening = find_intervals("22:30-01:55")[0]
        self.assertFalse(suspicious_night_wrap(evening, "night"))
        self.assertEqual(reported_work_interval(evening, "night"), "3 h 25 min")
        backward = find_intervals("22h00 - 20h45")[0]
        self.assertTrue(suspicious_numeric_interval(backward, "night"))
        self.assertEqual(reported_work_interval(backward, "night"), "")

    def test_midnight_span(self):
        interval = find_intervals("22:30-01:55")[0]
        self.assertEqual(reported_work_interval(interval), "3 h 25 min")


if __name__ == "__main__":
    unittest.main()
