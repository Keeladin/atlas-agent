/**
 * Deterministic green / orange / red for Atlas Mobile Capture.
 * Never rewrites clocks. Never defaults job outcome or machine state.
 */
(function (global) {
  const R = global.AtlasRecords;

  function parseClock(value) {
    const text = String(value || "").trim();
    const match = /^(\d{1,2}):(\d{2})$/.exec(text);
    if (!match) return null;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (hour > 23 || minute > 59) return null;
    return { hour: hour, minute: minute };
  }

  function ordinaryOvernight(start, end) {
    return start.hour >= 18 && start.hour <= 23 && end.hour >= 0 && end.hour <= 5;
  }

  function isSuspiciousClockPair(start, end) {
    const startM = start.hour * 60 + start.minute;
    const endM = end.hour * 60 + end.minute;
    if (endM >= startM) return false;
    if (ordinaryOvernight(start, end)) return false;
    return true;
  }

  function intervalMinutes(start, end) {
    let minutes = end.hour * 60 + end.minute - (start.hour * 60 + start.minute);
    if (minutes < 0) minutes += 24 * 60;
    return minutes;
  }

  function formatInterval(minutes) {
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return hours + " h " + String(mins).padStart(2, "0") + " min";
  }

  function knownSubject(subject, machines) {
    if (global.AtlasDirectory) {
      return !!global.AtlasDirectory.resolveMachine(subject, machines);
    }
    const key = R.subjectKey(subject);
    if (!key) return false;
    return (machines || []).some(function (machine) {
      const tokens = [machine.id, machine.canonical_id, machine.display_name].concat(
        machine.aliases || []
      );
      return tokens.some(function (token) {
        return token && R.subjectKey(token) === key;
      });
    });
  }

  function acked(activity, code) {
    return (activity.orange_acks || []).indexOf(code) !== -1;
  }

  function validateActivity(activity, context) {
    context = context || {};
    const red = [];
    const orange = [];
    const kind = activity.kind;
    const work = String(activity.work || "").trim();
    const subject = String(activity.subject || "").trim();

    if (!kind || ["machine", "other", "attendance"].indexOf(kind) === -1) {
      red.push("Activity kind is required.");
    }

    if (kind === "machine" || kind === "other") {
      if (!subject) red.push("Subject is required.");
      if (!work) red.push("Work / what happened is required.");
    }

    if (kind === "attendance") {
      const allAtWork = activity.all_at_work === true;
      if (!work && !allAtWork) {
        red.push("List absences or mark all at work.");
      }
    }

    if (kind === "machine" && !activity.machine_state) {
      red.push("Machine state is required.");
    }

    const timedMachine =
      kind === "machine" && activity.time_mode === "clocks";
    if (timedMachine && !activity.job_outcome) {
      red.push("Job outcome is required for timed machine work.");
    }

    if (activity.time_mode === "clocks") {
      const start = parseClock(activity.start);
      const end = parseClock(activity.end);
      if (!start) red.push("Start time is required as HH:MM.");
      if (!end) red.push("End time is required as HH:MM.");
      if (start && end && isSuspiciousClockPair(start, end)) {
        orange.push({
          code: "suspicious_clocks",
          message:
            "These clocks look backwards or unusual. Confirm they are correct or edit them. Atlas will not change the clocks.",
        });
      }
    } else if (activity.time_mode !== "no_times" && kind !== "attendance") {
      red.push("Choose clocks or no-times.");
    }

    const resolved =
      kind === "machine" && global.AtlasDirectory
        ? global.AtlasDirectory.resolveMachine(subject, context.machines)
        : null;
    const threadSubject = resolved
      ? resolved.canonical_id || resolved.id
      : subject;

    if (kind === "machine" && threadSubject && context.reportsInOrder) {
      if (R.threadIsOpen(threadSubject, context.reportsInOrder)) {
        if (activity.continuation !== "continue" && activity.continuation !== "new") {
          red.push("Choose Continue previous or New issue for this machine.");
        }
      }
    }

    const openCondition =
      activity.job_outcome === "incomplete" ||
      (R.OPEN_STATES || []).indexOf(activity.machine_state) !== -1;
    if (kind === "machine" && openCondition && !String(activity.follow_up || "").trim()) {
      orange.push({
        code: "open_without_note",
        message: "Add a short follow-up note, or confirm leaving this unresolved with no note.",
      });
    }

    if (activity.machine_state === "other") {
      if (!String(activity.machine_state_other || "").trim()) {
        orange.push({
          code: "other_state",
          message: "Describe why Machine state is Other, or choose a listed state.",
        });
      } else if (!acked(activity, "other_state")) {
        orange.push({
          code: "other_state",
          message: "Confirm that Other is necessary for this machine state.",
        });
      }
    }

    if (
      kind === "machine" &&
      subject &&
      context.machines &&
      !knownSubject(subject, context.machines)
    ) {
      orange.push({
        code: "new_identity",
        message: "This machine identity is not in the known list. Confirm the spelling.",
      });
    }

    const unresolvedOrange = orange.filter(function (item) {
      return !acked(activity, item.code);
    });

    let status = "green";
    if (red.length) status = "red";
    else if (unresolvedOrange.length) status = "orange";

    let workInterval = "";
    if (activity.time_mode === "clocks") {
      const start = parseClock(activity.start);
      const end = parseClock(activity.end);
      if (start && end && !isSuspiciousClockPair(start, end)) {
        workInterval = formatInterval(intervalMinutes(start, end));
      } else if (start && end && isSuspiciousClockPair(start, end) && acked(activity, "suspicious_clocks")) {
        workInterval = "";
      }
    }

    return {
      status: status,
      red: red,
      orange: orange,
      unresolved_orange: unresolvedOrange,
      work_interval: workInterval,
      can_next: status === "green",
    };
  }

  function validateReportStart(report) {
    const red = [];
    if (!report.user_id && !report.supervisor_id) red.push("Choose who is reporting.");
    if (!report.operational_day) red.push("Operational day is required.");
    if (report.shift !== "day" && report.shift !== "night") {
      red.push("Choose day or night shift.");
    }
    return { status: red.length ? "red" : "green", red: red };
  }

  global.AtlasValidate = {
    parseClock: parseClock,
    isSuspiciousClockPair: isSuspiciousClockPair,
    ordinaryOvernight: ordinaryOvernight,
    knownSubject: knownSubject,
    validateActivity: validateActivity,
    validateReportStart: validateReportStart,
    intervalMinutes: intervalMinutes,
    formatInterval: formatInterval,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = global.AtlasValidate;
  }
})(typeof window !== "undefined" ? window : globalThis);
