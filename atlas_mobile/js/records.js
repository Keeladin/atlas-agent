/**
 * Atlas report / activity records.
 * These are AtlasAgent records stored locally, not a side-app schema.
 */
(function (global) {
  const JOB_OUTCOMES = [
    { id: "completed", label: "Completed" },
    { id: "incomplete", label: "Incomplete / continue" },
  ];

  const MACHINE_STATES = [
    { id: "running", label: "Running / operational" },
    { id: "not_tested", label: "Not tested" },
    { id: "still_under_repair", label: "Still under repair / standing" },
    { id: "awaiting_parts", label: "Awaiting parts" },
    { id: "other", label: "Other" },
  ];

  const OPEN_STATES = [
    "not_tested",
    "still_under_repair",
    "awaiting_parts",
    "other",
  ];

  function uid(prefix) {
    return (
      prefix +
      "-" +
      Date.now().toString(36) +
      "-" +
      Math.random().toString(36).slice(2, 8)
    );
  }

  function emptyActivity() {
    return {
      id: uid("act"),
      kind: "machine",
      subject: "",
      subject_entered: "",
      machine_entity_id: "",
      work: "",
      time_mode: "clocks",
      start: "",
      end: "",
      job_outcome: "",
      machine_state: "",
      machine_state_other: "",
      follow_up: "",
      continuation: "",
      people: "",
      extra: "",
      orange_acks: [],
    };
  }

  function emptyReport(partial) {
    return Object.assign(
      {
        type: "atlas.report",
        id: uid("rep"),
        user_id: "",
        user_display_name: "",
        user_role: "",
        operational_day: "",
        shift: "",
        status: "draft",
        activities: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      partial || {}
    );
  }

  function subjectKey(subject) {
    return String(subject || "")
      .replace(/[\s\-]/g, "")
      .toUpperCase();
  }

  function activitySubjectKey(activity) {
    if (!activity) return "";
    if (typeof activity === "string") return subjectKey(activity);
    return subjectKey(
      activity.subject || activity.subject_entered || activity.machine_entity_id
    );
  }

  function sameSubject(a, b) {
    const left = typeof a === "object" ? activitySubjectKey(a) : subjectKey(a);
    const right = typeof b === "object" ? activitySubjectKey(b) : subjectKey(b);
    return left === right && left !== "";
  }

  function isUnresolvedActivity(activity) {
    if (!activity || activity.kind !== "machine") return false;
    if (activity.job_outcome === "incomplete") return true;
    return OPEN_STATES.indexOf(activity.machine_state) !== -1;
  }

  function closesThread(activity) {
    return (
      activity &&
      activity.kind === "machine" &&
      activity.continuation === "continue" &&
      activity.machine_state === "running"
    );
  }

  /**
   * Thread is open only if the latest relevant activity left it unresolved
   * and no later Continue+Running closed it.
   * Same machine + Completed + Running without Continue does not close.
   */
  function threadIsOpen(subject, reportsInOrder) {
    let open = false;
    (reportsInOrder || []).forEach(function (report) {
      (report.activities || []).forEach(function (activity) {
        if (activity.kind !== "machine") return;
        if (!sameSubject(activity, subject)) return;
        if (closesThread(activity)) {
          open = false;
        } else if (isUnresolvedActivity(activity)) {
          open = true;
        }
      });
    });
    return open;
  }

  function lastOpenActivity(subject, reportsInOrder) {
    let last = null;
    (reportsInOrder || []).forEach(function (report) {
      (report.activities || []).forEach(function (activity) {
        if (activity.kind !== "machine") return;
        if (!sameSubject(activity, subject)) return;
        if (closesThread(activity)) {
          last = null;
        } else if (isUnresolvedActivity(activity)) {
          last = activity;
        }
      });
    });
    return last;
  }

  global.AtlasRecords = {
    JOB_OUTCOMES: JOB_OUTCOMES,
    MACHINE_STATES: MACHINE_STATES,
    OPEN_STATES: OPEN_STATES,
    uid: uid,
    emptyActivity: emptyActivity,
    emptyReport: emptyReport,
    subjectKey: subjectKey,
    activitySubjectKey: activitySubjectKey,
    sameSubject: sameSubject,
    isUnresolvedActivity: isUnresolvedActivity,
    closesThread: closesThread,
    threadIsOpen: threadIsOpen,
    lastOpenActivity: lastOpenActivity,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = global.AtlasRecords;
  }
})(typeof window !== "undefined" ? window : globalThis);
