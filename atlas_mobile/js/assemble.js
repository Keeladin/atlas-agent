/**
 * End Report assembly and compact WhatsApp-ready text.
 * Generated view only — not a second stored model.
 */
(function (global) {
  const R = global.AtlasRecords;
  const V = global.AtlasValidate;

  function stateLabel(id) {
    const found = (R.MACHINE_STATES || []).find(function (item) {
      return item.id === id;
    });
    return found ? found.label : id || "";
  }

  function outcomeLabel(id) {
    const found = (R.JOB_OUTCOMES || []).find(function (item) {
      return item.id === id;
    });
    return found ? found.label : id || "";
  }

  function compactState(id) {
    if (id === "running") return "Running";
    if (id === "not_tested") return "Not tested";
    if (id === "still_under_repair") return "Still under repair / standing";
    if (id === "awaiting_parts") return "Awaiting parts";
    if (id === "other") return "Other";
    return "";
  }

  function timeLine(activity) {
    if (activity.time_mode === "no_times") return "";
    if (activity.start && activity.end) return activity.start + "-" + activity.end;
    return "";
  }

  function groupActivities(activities) {
    const groups = [];
    const index = {};
    (activities || []).forEach(function (activity) {
      let key;
      if (activity.kind === "attendance") key = "ATTENDANCE";
      else if (activity.kind === "other") key = "OTHER:" + (activity.subject || "other");
      else key = "M:" + R.activitySubjectKey(activity);
      if (!index[key]) {
        index[key] = {
          key: key,
          kind: activity.kind,
          subject: activity.kind === "attendance" ? "Attendance" : activity.subject,
          activities: [],
        };
        groups.push(index[key]);
      }
      index[key].activities.push(activity);
    });
    return groups;
  }

  function lastStateForGroup(group, report, lastCompleted) {
    if (group.kind !== "machine") return "";
    const reports = [];
    if (lastCompleted) reports.push(lastCompleted);
    reports.push(report);
    let last = "";
    reports.forEach(function (rep) {
      (rep.activities || []).forEach(function (activity) {
        if (activity.kind !== "machine") return;
        if (!R.sameSubject(activity, group.subject)) return;
        last = activity.machine_state;
      });
    });
    return last;
  }

  function unresolvedList(report, lastCompleted) {
    const reports = [];
    if (lastCompleted) reports.push(lastCompleted);
    reports.push(report);
    const seen = {};
    const open = [];
    (report.activities || []).forEach(function (activity) {
      if (activity.kind !== "machine") return;
      const subject = activity.subject;
      if (seen[R.subjectKey(subject)]) return;
      if (R.threadIsOpen(subject, reports)) {
        seen[R.subjectKey(subject)] = true;
        const last = R.lastOpenActivity(subject, reports);
        open.push({
          subject: subject,
          state: last ? last.machine_state : "",
          follow_up: last ? last.follow_up : "",
          outcome: last ? last.job_outcome : "",
        });
      }
    });
    return open;
  }

  function whatsappText(report) {
    const lines = [];
    const who = report.user_display_name || report.supervisor_name || report.user_id;
    const shift = report.shift === "night" ? "Night shift" : "Day shift";
    lines.push(shift + " " + (report.operational_day || "") + (who ? " — " + who : ""));
    lines.push("");
    groupActivities(report.activities).forEach(function (group) {
      group.activities.forEach(function (activity) {
        if (activity.kind === "attendance") {
          lines.push("Attendance");
          if (activity.all_at_work) lines.push("All at work");
          else lines.push(activity.work || "");
          lines.push("");
          return;
        }
        const when = timeLine(activity);
        const head = activity.subject + (when ? " " + when : "");
        lines.push(head);
        if (activity.work) lines.push(activity.work);
        if (activity.kind === "machine") {
          if (activity.job_outcome === "incomplete") lines.push("Incomplete");
          const state = compactState(activity.machine_state);
          if (state) {
            if (activity.machine_state === "other" && activity.machine_state_other) {
              lines.push("Other: " + activity.machine_state_other);
            } else {
              lines.push(state);
            }
          }
        }
        if (activity.follow_up) lines.push(activity.follow_up);
        if (activity.people) lines.push(activity.people);
        lines.push("");
      });
    });
    return lines.join("\n").trim() + "\n";
  }

  function reviewModel(report, lastCompleted) {
    const groups = groupActivities(report.activities);
    return {
      supervisor: report.user_display_name || report.supervisor_name,
      operational_day: report.operational_day,
      shift: report.shift,
      status: report.status,
      groups: groups.map(function (group) {
        return {
          kind: group.kind,
          subject: group.subject,
          last_state: lastStateForGroup(group, report, lastCompleted),
          activities: group.activities.map(function (activity) {
            const start = V.parseClock(activity.start);
            const end = V.parseClock(activity.end);
            let interval = "";
            if (
              activity.time_mode === "clocks" &&
              start &&
              end &&
              !V.isSuspiciousClockPair(start, end)
            ) {
              interval = V.formatInterval(V.intervalMinutes(start, end));
            }
            return {
              id: activity.id,
              time: timeLine(activity) || "No times",
              interval: interval,
              work: activity.work,
              job_outcome: activity.job_outcome,
              job_outcome_label: outcomeLabel(activity.job_outcome),
              machine_state: activity.machine_state,
              machine_state_label: stateLabel(activity.machine_state),
              follow_up: activity.follow_up,
              continuation: activity.continuation,
              people: activity.people,
            };
          }),
        };
      }),
      unresolved: unresolvedList(report, lastCompleted),
      whatsapp: whatsappText(report),
    };
  }

  global.AtlasAssemble = {
    groupActivities: groupActivities,
    whatsappText: whatsappText,
    reviewModel: reviewModel,
    unresolvedList: unresolvedList,
    timeLine: timeLine,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = global.AtlasAssemble;
  }
})(typeof window !== "undefined" ? window : globalThis);
