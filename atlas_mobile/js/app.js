(function () {
  const R = AtlasRecords;
  const V = AtlasValidate;
  const S = AtlasStore;
  const A = AtlasAssemble;

  const state = {
    users: [],
    machines: [],
    report: null,
    lastCompleted: null,
    editing: null,
    screen: "home",
    online: navigator.onLine,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function show(id) {
    ["screen-home", "screen-start", "screen-report", "screen-activity", "screen-end"].forEach(
      function (name) {
        const el = $(name);
        if (el) el.hidden = name !== id;
      }
    );
    state.screen = id;
  }

  function setBanner() {
    const el = $("net-banner");
    if (!el) return;
    if (state.online) {
      el.textContent = "Online — reports stay local only until Atlas sync exists.";
      el.className = "banner online";
    } else {
      el.textContent = "Offline — capture, Next, End Report and Copy still work.";
      el.className = "banner offline";
    }
  }

  async function loadDirectory() {
    const [usersRes, machinesRes] = await Promise.all([
      fetch("data/users.json"),
      fetch("data/machines.json"),
    ]);
    const usersData = await usersRes.json();
    const machinesData = await machinesRes.json();
    state.users = usersData.users || [];
    state.machines = machinesData.machines || machinesData.identities || [];
  }

  function fillAuthors() {
    const sel = $("field-user");
    sel.innerHTML = '<option value="">Choose…</option>';
    AtlasDirectory.reportAuthors(state.users).forEach(function (user) {
      const opt = document.createElement("option");
      opt.value = user.id;
      opt.textContent = AtlasDirectory.userLabel(user);
      sel.appendChild(opt);
    });
  }

  function machineOptions() {
    return AtlasDirectory.pickerMachines(state.machines)
      .map(function (machine) {
        return AtlasDirectory.machineLabel(machine);
      })
      .sort();
  }

  function reportsForContinuation() {
    const list = [];
    if (state.lastCompleted) list.push(state.lastCompleted);
    if (state.report) list.push(state.report);
    return list;
  }

  function renderHome() {
    show("screen-home");
    S.listReports().then(function (rows) {
      const box = $("report-list");
      if (!rows.length) {
        box.innerHTML = "<p class='muted'>No Atlas reports on this device yet.</p>";
        return;
      }
      box.innerHTML = rows
        .map(function (row) {
          return (
            '<button class="card-btn" data-open="' +
            row.id +
            '">' +
            (row.user_display_name || row.supervisor_name || "") +
            " · " +
            (row.operational_day || "") +
            " · " +
            (row.shift || "") +
            " · " +
            (row.status === "completed_local" ? "completed (local only)" : "draft") +
            " · " +
            (row.activities || []).length +
            " activities</button>"
          );
        })
        .join("");
    });
  }

  function renderReport() {
    show("screen-report");
    const r = state.report;
    $("report-heading").textContent =
      (r.user_display_name || r.supervisor_name || "") +
      " · " +
      r.operational_day +
      " · " +
      r.shift +
      " · " +
      (r.status === "completed_local" ? "completed (local only)" : "draft");
    const list = $("activity-list");
    if (!(r.activities || []).length) {
      list.innerHTML = "<p class='muted'>No activities yet. Add one at a time.</p>";
    } else {
      list.innerHTML = r.activities
        .map(function (a) {
          const when =
            a.kind === "attendance"
              ? "Attendance"
              : a.time_mode === "no_times"
                ? a.subject + " · no times"
                : a.subject + " · " + (a.start || "") + "-" + (a.end || "");
          return (
            '<button class="card-btn" data-edit="' +
            a.id +
            '">' +
            when +
            "<span>" +
            (a.work || "").slice(0, 80) +
            "</span></button>"
          );
        })
        .join("");
    }
  }

  function fillActivityForm(activity) {
    state.editing = activity;
    $("act-kind").value = activity.kind || "machine";
    $("act-subject").value = activity.subject || "";
    $("act-work").value = activity.work || "";
    $("act-time-mode").value = activity.time_mode || "clocks";
    $("act-start").value = activity.start || "";
    $("act-end").value = activity.end || "";
    $("act-outcome").value = activity.job_outcome || "";
    $("act-state").value = activity.machine_state || "";
    $("act-state-other").value = activity.machine_state_other || "";
    $("act-follow").value = activity.follow_up || "";
    $("act-people").value = activity.people || "";
    $("act-extra").value = activity.extra || "";
    $("act-all-at-work").checked = activity.all_at_work === true;
    $("act-continue").value = activity.continuation || "";
    toggleKindFields();
    renderValidation();
  }

  function readActivityForm() {
    const a = state.editing;
    a.kind = $("act-kind").value;
    a.subject_entered = $("act-subject").value.trim();
    const resolved = AtlasDirectory.resolveMachine(a.subject_entered, state.machines);
    if (resolved) {
      a.machine_entity_id = resolved.id;
      a.subject = resolved.canonical_id || resolved.display_name || a.subject_entered;
    } else {
      a.machine_entity_id = "";
      a.subject = a.subject_entered;
    }
    a.work = $("act-work").value;
    a.time_mode = $("act-time-mode").value;
    a.start = $("act-start").value;
    a.end = $("act-end").value;
    a.job_outcome = $("act-outcome").value;
    a.machine_state = $("act-state").value;
    a.machine_state_other = $("act-state-other").value;
    a.follow_up = $("act-follow").value;
    a.people = $("act-people").value;
    a.extra = $("act-extra").value;
    a.all_at_work = $("act-all-at-work").checked;
    a.continuation = $("act-continue").value;
    return a;
  }

  function toggleKindFields() {
    const kind = $("act-kind").value;
    const mode = $("act-time-mode").value;
    $("wrap-subject").hidden = kind === "attendance";
    $("wrap-times").hidden = kind === "attendance";
    $("wrap-clocks").hidden = kind === "attendance" || mode !== "clocks";
    $("wrap-outcome").hidden = !(kind === "machine" && mode === "clocks");
    $("wrap-state").hidden = kind !== "machine";
    $("wrap-state-other").hidden = $("act-state").value !== "other";
    $("wrap-attendance").hidden = kind !== "attendance";
    const subject = $("act-subject").value.trim();
    const open =
      kind === "machine" &&
      subject &&
      R.threadIsOpen(subject, reportsForContinuation());
    $("wrap-continue").hidden = !open;
    if (open) {
      const prior = R.lastOpenActivity(subject, reportsForContinuation());
      $("continue-hint").textContent = prior
        ? subject +
          " — previous: " +
          (prior.machine_state || "") +
          (prior.follow_up ? " / " + prior.follow_up : "")
        : "";
    }
  }

  function renderValidation() {
    const activity = readActivityForm();
    const result = V.validateActivity(activity, {
      machines: state.machines,
      reportsInOrder: reportsForContinuation(),
    });
    const box = $("val-box");
    box.className = "val " + result.status;
    const bits = [];
    if (result.red.length) {
      bits.push("<strong>Red</strong><ul>" + result.red.map(function (m) {
        return "<li>" + escapeHtml(m) + "</li>";
      }).join("") + "</ul>");
    }
    result.unresolved_orange.forEach(function (item) {
      bits.push(
        "<p><strong>Orange</strong> — " +
          escapeHtml(item.message) +
          '</p><button type="button" class="ack" data-ack="' +
          item.code +
          '">Confirm</button>'
      );
    });
    if (result.status === "green") {
      bits.push("<p><strong>Green</strong> — Next is available.</p>");
      if (result.work_interval) {
        bits.push(
          "<p class='muted'>Reported work interval: " +
            result.work_interval +
            "</p>"
        );
      }
    }
    box.innerHTML = bits.join("");
    $("btn-next").disabled = !result.can_next;
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  async function persistActivity() {
    const activity = readActivityForm();
    const result = V.validateActivity(activity, {
      machines: state.machines,
      reportsInOrder: reportsForContinuation(),
    });
    if (!result.can_next) return false;
    const exists = (state.report.activities || []).some(function (item) {
      return item.id === activity.id;
    });
    if (exists) {
      state.report = await S.replaceActivity(state.report.id, activity);
    } else {
      state.report = await S.addActivity(state.report, activity);
    }
    return true;
  }

  function renderEnd() {
    show("screen-end");
    const model = A.reviewModel(state.report, state.lastCompleted);
    $("end-meta").textContent =
      model.supervisor +
      " · " +
      model.operational_day +
      " · " +
      model.shift +
      " · local only";
    const body = model.groups
      .map(function (group) {
        const rows = group.activities
          .map(function (a) {
            return (
              "<li><strong>" +
              escapeHtml(a.time) +
              (a.interval ? " (" + a.interval + ")" : "") +
              "</strong> — " +
              escapeHtml(a.work || "") +
              (a.job_outcome_label ? " · " + a.job_outcome_label : "") +
              (a.machine_state_label ? " · " + a.machine_state_label : "") +
              (a.continuation === "continue" ? " · Continue" : "") +
              (a.follow_up ? " · " + escapeHtml(a.follow_up) : "") +
              "</li>"
            );
          })
          .join("");
        return "<h3>" + escapeHtml(group.subject) + "</h3><ul>" + rows + "</ul>";
      })
      .join("");
    const unresolved = model.unresolved.length
      ? "<h3>Unresolved</h3><ul>" +
        model.unresolved
          .map(function (u) {
            return (
              "<li>" +
              escapeHtml(u.subject) +
              " — " +
              escapeHtml(u.state || "") +
              (u.follow_up ? " / " + escapeHtml(u.follow_up) : "") +
              "</li>"
            );
          })
          .join("") +
        "</ul>"
      : "<p class='muted'>No unresolved machine threads on this device path.</p>";
    $("end-review").innerHTML = body + unresolved;
    $("wa-text").value = model.whatsapp;
  }

  function bind() {
    $("btn-new-report").onclick = function () {
      fillAuthors();
      const today = new Date();
      const iso = today.toISOString().slice(0, 10);
      $("field-day").value = iso;
      $("field-shift").value = "";
      $("field-user").value = "";
      show("screen-start");
    };
    $("btn-start-cancel").onclick = renderHome;
    $("form-start").onsubmit = async function (ev) {
      ev.preventDefault();
      const person = AtlasDirectory.resolveUser($("field-user").value, state.users);
      const report = R.emptyReport({
        user_id: person ? person.id : "",
        user_display_name: AtlasDirectory.userLabel(person),
        user_role: person ? person.role : "",
        operational_day: $("field-day").value,
        shift: $("field-shift").value,
      });
      const start = V.validateReportStart(report);
      $("start-val").textContent = start.red.join(" ");
      if (start.status !== "green") return;
      state.lastCompleted = await S.lastCompletedReport();
      state.report = await S.putReport(report);
      renderReport();
    };
    $("btn-add-activity").onclick = function () {
      fillActivityForm(R.emptyActivity());
      show("screen-activity");
    };
    $("btn-activity-cancel").onclick = renderReport;
    $("act-kind").onchange = function () {
      toggleKindFields();
      renderValidation();
    };
    $("act-time-mode").onchange = function () {
      toggleKindFields();
      renderValidation();
    };
    $("act-state").onchange = function () {
      toggleKindFields();
      renderValidation();
    };
    $("act-subject").oninput = function () {
      toggleKindFields();
      renderValidation();
    };
    [
      "act-work",
      "act-start",
      "act-end",
      "act-outcome",
      "act-state-other",
      "act-follow",
      "act-people",
      "act-extra",
      "act-continue",
      "act-all-at-work",
    ].forEach(function (id) {
      const el = $(id);
      el.addEventListener("input", renderValidation);
      el.addEventListener("change", renderValidation);
    });
    $("machine-suggest").onclick = function (ev) {
      if (ev.target.dataset.machine) {
        $("act-subject").value = ev.target.dataset.machine;
        toggleKindFields();
        renderValidation();
      }
    };
    $("act-subject").addEventListener("focus", function () {
      const box = $("machine-suggest");
      box.innerHTML = machineOptions()
        .map(function (id) {
          return '<button type="button" data-machine="' + id + '">' + id + "</button>";
        })
        .join("");
    });
    $("val-box").addEventListener("click", function (ev) {
      const code = ev.target.dataset.ack;
      if (!code) return;
      const acks = state.editing.orange_acks || [];
      if (acks.indexOf(code) === -1) acks.push(code);
      state.editing.orange_acks = acks;
      renderValidation();
    });
    $("btn-next").onclick = async function () {
      const ok = await persistActivity();
      if (!ok) {
        renderValidation();
        return;
      }
      fillActivityForm(R.emptyActivity());
      renderValidation();
      show("screen-activity");
    };
    $("btn-save-back").onclick = async function () {
      const result = V.validateActivity(readActivityForm(), {
        machines: state.machines,
        reportsInOrder: reportsForContinuation(),
      });
      if (result.can_next) await persistActivity();
      const fresh = await S.getReport(state.report.id);
      state.report = fresh;
      renderReport();
    };
    $("activity-list").addEventListener("click", function (ev) {
      const id = ev.target.closest("[data-edit]");
      if (!id) return;
      const found = state.report.activities.find(function (a) {
        return a.id === id.dataset.edit;
      });
      if (found) {
        fillActivityForm(JSON.parse(JSON.stringify(found)));
        show("screen-activity");
      }
    });
    $("report-list").addEventListener("click", async function (ev) {
      const id = ev.target.closest("[data-open]");
      if (!id) return;
      state.report = await S.getReport(id.dataset.open);
      state.lastCompleted = await S.lastCompletedReport();
      if (state.lastCompleted && state.lastCompleted.id === state.report.id) {
        const rows = await S.listReports();
        state.lastCompleted =
          rows.find(function (row) {
            return row.status === "completed_local" && row.id !== state.report.id;
          }) || null;
      }
      renderReport();
    });
    $("btn-end-report").onclick = async function () {
      renderEnd();
    };
    $("btn-complete").onclick = async function () {
      state.report.status = "completed_local";
      state.report = await S.putReport(state.report);
      renderEnd();
    };
    $("btn-copy-wa").onclick = async function () {
      const text = $("wa-text").value;
      try {
        await navigator.clipboard.writeText(text);
        $("copy-status").textContent = "Copied.";
      } catch (err) {
        $("wa-text").select();
        $("copy-status").textContent = "Select and copy the text below.";
      }
    };
    $("btn-end-back").onclick = renderReport;
    $("btn-home").onclick = renderHome;
    $("btn-home-2").onclick = renderHome;
    window.addEventListener("online", function () {
      state.online = true;
      setBanner();
    });
    window.addEventListener("offline", function () {
      state.online = false;
      setBanner();
    });
  }

  async function boot() {
    setBanner();
    if ("serviceWorker" in navigator) {
      try {
        await navigator.serviceWorker.register("sw.js");
      } catch (err) {
        console.warn("SW register failed", err);
      }
    }
    await loadDirectory();
    bind();
    renderHome();
  }

  boot();
})();
