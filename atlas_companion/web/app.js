const $ = (selector) => document.querySelector(selector);
const state = {
  tasks: [],
  health: null,
  filter: "all",
  documents: [],
  approvals: [],
  hits: null,
  workView: "overview",
  knowledgeView: "library",
  conversationId: null,
};
const ASK_WELCOME = '<div class="ask-welcome muted-panel">Ask a question, request a story, or start operational work. Atlas infers success criteria unless you override them in Advanced.</div>';
const screenTitles = {
  ask: "Ask",
  work: "Work",
  personal: "Personal",
  knowledge: "Knowledge",
  models: "Models",
  settings: "Settings",
};
const workViewTitles = {
  overview: "Overview",
  "one-off": "One-off",
  recurring: "Recurring",
  history: "History",
};
const knowledgeViewTitles = {
  library: "Library",
  search: "Search",
  indexing: "Indexing",
};
const terminal = new Set(["completed", "failed", "cancelled"]);
const recurringWorkflows = new Set(["morning_v1", "knowledge_ingest"]);
let busy = false;

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || response.statusText);
  return data;
}

function esc(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "Unavailable";
  return node.innerHTML;
}

function bytes(value) { return value == null ? "Unavailable" : `${(value / 1073741824).toFixed(1)} GB`; }
function unit(value, suffix = "") { return value == null ? "Unavailable" : `${value}${suffix}`; }
function displayStatus(status) { return status === "active" ? "active / ready" : status; }
function isOpen(task) { return ["planned", "active", "waiting"].includes(task.status); }
function pill(status) { return `<span class="pill ${esc(status)}">${esc(displayStatus(status))}</span>`; }
function metric(label, value, note) { return `<article class="metric-card"><small>${esc(label)}</small><strong>${esc(value)}</strong><span>${esc(note)}</span></article>`; }
function shortHash(value) { return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "Unavailable"; }
function workflowOf(task) { return task.workflow || task.metadata?.workflow || ""; }
function isRecurring(task) { return recurringWorkflows.has(workflowOf(task)) || task.metadata?.recurring === true; }

function toast(text) {
  const node = $("#toast");
  node.textContent = text;
  node.style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.style.display = "none"; }, 2800);
}

function parseHash() {
  const raw = (location.hash || "#ask").replace(/^#/, "");
  const [screen, ...rest] = raw.split("/").filter(Boolean);
  if (!screenTitles[screen]) return { screen: "ask", workView: "overview", knowledgeView: "library", taskId: null };
  if (screen === "knowledge") {
    const next = rest[0] || "library";
    return { screen: "knowledge", workView: "overview", knowledgeView: knowledgeViewTitles[next] ? next : "library", taskId: null };
  }
  if (screen !== "work") return { screen, workView: "overview", knowledgeView: "library", taskId: null };
  const next = rest[0] || "overview";
  if (workViewTitles[next]) return { screen: "work", workView: next, knowledgeView: "library", taskId: rest[1] || null };
  return { screen: "work", workView: "one-off", knowledgeView: "library", taskId: next };
}

function writeHash(screen, { workView, knowledgeView, taskId, replace = false } = {}) {
  let hash = `#${screen}`;
  if (screen === "work") {
    const view = workView || state.workView || "overview";
    hash = taskId ? `#work/${view}/${taskId}` : `#work/${view}`;
  }
  if (screen === "knowledge") {
    hash = `#knowledge/${knowledgeView || state.knowledgeView || "library"}`;
  }
  if (replace) history.replaceState(null, "", hash);
  else if (location.hash !== hash) location.hash = hash;
}

function showScreen(name, { workView, knowledgeView, taskId, syncHash = true } = {}) {
  if (!screenTitles[name]) name = "ask";
  document.querySelectorAll(".screen").forEach((node) => node.classList.toggle("active", node.id === `${name}-screen`));
  document.querySelectorAll(".nav-btn").forEach((node) => node.classList.toggle("active", node.dataset.screen === name));
  if (name === "work") {
    $("#crumb").textContent = `Work / ${workViewTitles[workView || state.workView] || "Overview"}`;
  } else if (name === "knowledge") {
    $("#crumb").textContent = `Knowledge / ${knowledgeViewTitles[knowledgeView || state.knowledgeView] || "Library"}`;
  } else {
    $("#crumb").textContent = screenTitles[name];
  }
  $("#page-title").textContent = screenTitles[name];
  if (name === "work") {
    setWorkView(workView || state.workView, { syncHash: false });
  }
  if (name === "knowledge") {
    setKnowledgeView(knowledgeView || state.knowledgeView, { syncHash: false });
    loadKnowledge();
  }
  if (name === "ask") loadAsk();
  if (name === "models") loadModels();
  if (name === "settings") loadHealth();
  if (syncHash) writeHash(name, { workView: workView || state.workView, knowledgeView: knowledgeView || state.knowledgeView, taskId });
}

function setKnowledgeView(view, { syncHash = true } = {}) {
  if (!knowledgeViewTitles[view]) view = "library";
  state.knowledgeView = view;
  document.querySelectorAll("#knowledge-tabs [data-knowledge-view]").forEach((node) => {
    node.classList.toggle("active", node.dataset.knowledgeView === view);
  });
  $("#knowledge-library").hidden = view !== "library";
  $("#knowledge-search-view").hidden = view !== "search";
  $("#knowledge-indexing").hidden = view !== "indexing";
  $("#crumb").textContent = `Knowledge / ${knowledgeViewTitles[view]}`;
  renderKnowledge();
  if (syncHash) writeHash("knowledge", { knowledgeView: view });
}

function setWorkView(view, { syncHash = true } = {}) {
  if (!workViewTitles[view]) view = "overview";
  state.workView = view;
  document.querySelectorAll("#work-tabs [data-work-view]").forEach((node) => {
    node.classList.toggle("active", node.dataset.workView === view);
  });
  const overview = $("#work-overview");
  const board = $("#work-board");
  const showBoard = view !== "overview";
  overview.hidden = showBoard;
  board.hidden = !showBoard;
  if (view === "history") state.filter = "all";
  if (view === "one-off" || view === "recurring") {
    document.querySelectorAll("#work-filters .filter").forEach((item) => {
      item.classList.toggle("active", item.dataset.filter === state.filter);
    });
  }
  $("#crumb").textContent = `Work / ${workViewTitles[view]}`;
  renderWork();
  renderOverview();
  if (syncHash) writeHash("work", { workView: view });
}

function record(task) {
  const reason = task.status === "failed" && task.failure_reason
    ? `<span class="record-reason">${esc(task.failure_reason)}</span>`
    : "";
  return `<button class="record" data-task-id="${esc(task.id)}"><span class="record-row"><span class="record-title">${esc(task.objective)}</span>${pill(task.status)}</span>${reason}<span class="record-meta">${esc(task.id)} · ${esc(task.authority_scope)}</span></button>`;
}

function bindTaskLinks(root = document) {
  root.querySelectorAll("[data-task-id]").forEach((node) => {
    node.onclick = () => openTask(node.dataset.taskId);
  });
}

function updateWorkBadge() {
  const waiting = state.approvals.length;
  const failed = state.tasks.filter((task) => task.status === "failed").length;
  const note = $("#work-nav-note");
  if (!note) return;
  if (waiting) note.textContent = `${waiting} approval${waiting === 1 ? "" : "s"} waiting`;
  else if (failed) note.textContent = `${failed} failed`;
  else note.textContent = "Operational execution";
}

function renderOverview() {
  const open = state.tasks.filter(isOpen);
  const completed = state.tasks.filter((task) => task.status === "completed");
  const failed = state.tasks.filter((task) => task.status === "failed");
  const recurring = state.tasks.filter(isRecurring);
  const running = state.health?.atlas?.running_executions ?? 0;
  $("#work-metrics").innerHTML = [
    metric("Open work", open.length, `${running} execution${running === 1 ? "" : "s"} actually running`),
    metric("Recurring", recurring.length, "Standing operational jobs"),
    metric("Approvals", state.approvals.length, state.approvals.length ? "Waiting for authority" : "No pending gates"),
    metric("Failed", failed.length, failed.length ? "Review failure evidence" : "No failed work"),
  ].join("");
  const attention = [
    ...state.approvals.map((item) => ({ ...item, status: "waiting", objective: item.objective, id: item.task_id, authority_scope: item.required_authority })),
    ...state.tasks.filter((task) => task.status === "waiting"),
    ...failed,
  ].slice(0, 6);
  $("#needs-attention").innerHTML = attention.length ? attention.map(record).join("") : '<div class="empty"><p>Nothing currently needs attention.</p></div>';
  $("#recent-outcomes").innerHTML = completed.slice(0, 5).map(record).join("") || '<div class="empty"><p>No completed tasks yet.</p></div>';
  bindTaskLinks($("#work-overview"));
}

function workList() {
  if (state.workView === "recurring") return state.tasks.filter(isRecurring);
  if (state.workView === "history") {
    return state.tasks.filter((task) => terminal.has(task.status));
  }
  return state.tasks.filter((task) => !isRecurring(task));
}

function renderWork() {
  const source = workList();
  const filtered = source.filter((task) => {
    if (state.workView === "history") return true;
    if (state.filter === "all") return true;
    if (state.filter === "open") return isOpen(task);
    return task.status === state.filter;
  });
  $("#tasks").innerHTML = filtered.length ? filtered.map(record).join("") : '<div class="empty"><p>No tasks in this view.</p></div>';
  bindTaskLinks($("#tasks"));
  const historyFilters = $("#work-filters");
  if (historyFilters) historyFilters.hidden = state.workView === "history";
}

function renderHits(data) {
  if (!data) {
    $("#knowledge-hits").innerHTML = '<p class="muted-panel">Search returns source-grounded chunks from the local library, not a synthesized answer and not the web.</p>';
    return;
  }
  if (!data.results.length) {
    $("#knowledge-hits").innerHTML = `<div class="empty"><p>No chunks matched “${esc(data.query)}”.</p></div>`;
    return;
  }
  $("#knowledge-hits").innerHTML = data.results.map((hit) => `
    <article class="hit">
      <div class="record-row"><strong>${esc(hit.title)}</strong><span class="pill completed">chunk ${esc(hit.ordinal)}</span></div>
      <p>${esc(hit.text)}</p>
      <p class="technical">${esc(hit.source_uri || "no source uri")} · ${esc(shortHash(hit.sha256))}</p>
    </article>`).join("");
}

function renderDocuments() {
  if (!state.documents.length) {
    $("#knowledge-docs").innerHTML = '<div class="empty"><p>No documents are indexed yet.</p></div>';
    return;
  }
  $("#knowledge-docs").innerHTML = `<table class="data-table"><thead><tr><th>Title</th><th>Source</th><th>Chunks</th><th>Hash</th></tr></thead><tbody>${
    state.documents.map((doc) => `<tr><td>${esc(doc.title)}</td><td class="technical">${esc(doc.source_uri || "inline")}</td><td>${esc(doc.chunk_count)}</td><td class="technical">${esc(shortHash(doc.content_sha256))}</td></tr>`).join("")
  }</tbody></table>`;
}

function ingestJobs() {
  return state.tasks.filter((task) => workflowOf(task) === "knowledge_ingest");
}

function renderIndexingJobs() {
  const jobs = ingestJobs();
  const root = $("#knowledge-jobs");
  if (!root) return;
  if (!jobs.length) {
    root.innerHTML = '<div class="empty"><p>No indexing jobs yet. Execution is recorded as Work.</p></div>';
    return;
  }
  root.innerHTML = jobs.map(record).join("");
  bindTaskLinks(root);
}

function renderKnowledge() {
  const jobs = ingestJobs();
  $("#knowledge-metrics").innerHTML = [
    metric("Library", state.documents.length, "Indexed documents"),
    metric("Indexing jobs", jobs.length, `${jobs.filter(isOpen).length} open`),
    metric("Completed ingests", jobs.filter((task) => task.status === "completed").length, "Path/hash-backed outcomes"),
    metric("Failed ingests", jobs.filter((task) => task.status === "failed").length, "Retry from Work"),
  ].join("");
  renderHits(state.hits);
  renderDocuments();
  renderIndexingJobs();
}

function renderPresentedResult(data) {
  const outputs = data.presentation?.outputs || [];
  const answer = outputs.find((item) => item.kind === "grounded_answer" && item.preview);
  const hits = outputs.flatMap((item) => item.hits || []);
  const parts = [];
  if (answer) {
    parts.push(`<h4 class="subhead">Result</h4><article class="panel answer-card"><p>${esc(answer.preview)}</p></article>`);
  }
  if (hits.length) {
    parts.push(`<h4 class="subhead">Sources</h4>${hits.map((hit) => `
      <article class="hit">
        <div class="record-row"><strong>${esc(hit.title || hit.source_uri || "Untitled")}</strong><span class="pill completed">chunk ${esc(hit.ordinal ?? "")}</span></div>
        <p>${esc(hit.text)}</p>
        <p class="technical">${esc(hit.source_uri || "no source uri")} · ${esc(shortHash(hit.sha256))}</p>
      </article>`).join("")}`);
  }
  if (!parts.length) {
    parts.push(`<h4 class="subhead">Result</h4><pre class="result">${esc(data.markdown)}</pre>`);
  }
  return parts.join("");
}

async function openTask(id) {
  showScreen("work", { workView: state.workView === "overview" ? "one-off" : state.workView, taskId: id });
  const detail = $("#detail");
  detail.innerHTML = '<div class="empty"><p>Loading durable task…</p></div>';
  try {
    const data = await api(`/api/tasks/${id}`);
    const snapshot = data.snapshot;
    const task = snapshot.task;
    const steps = snapshot.steps || [];
    const executions = snapshot.executions || [];
    const approvals = data.presentation.pending_approvals || [];
    const failures = data.presentation.failures || [];
    const failureReason = data.presentation.failure_reason || failures.find((item) => item.error)?.error;
    const canAct = !terminal.has(task.status);
    detail.innerHTML = `
      <div class="task-head"><div><h3>${esc(task.objective)}</h3><p class="technical">${esc(task.id)}</p></div>${pill(task.status)}</div>
      ${failureReason ? `<article class="panel failure-card"><strong>Why this failed</strong><p>${esc(failureReason)}</p>${failures.filter((item) => item.error && item.error !== failureReason).map((item) => `<p class="technical">${esc(item.capability)} → ${esc(item.status)}: ${esc(item.error)}</p>`).join("")}</article>` : ""}
      <div class="detail-actions">${
        canAct
          ? '<button class="secondary" id="run-task">Run / resume</button><button class="danger" id="cancel-task">Cancel</button>'
          : `<p class="muted-panel">This task is ${esc(task.status)}.</p>`
      }<button class="danger" id="delete-task">Delete</button></div>
      ${renderPresentedResult(data)}
      <details class="advanced-box">
        <summary>How Atlas did this</summary>
        <h4 class="subhead">Success criteria</h4>${(snapshot.criteria || []).map((item) => `<div class="record-row"><span>${esc(item.text)}</span>${pill(item.status)}</div>`).join("") || '<p class="muted-panel">No criteria recorded.</p>'}
        <p class="technical">Authority ${esc(task.authority_scope)}</p>
        <h4 class="subhead">Plan and execution</h4>${steps.map((step) => {
          const attempt = executions.filter((item) => item.step_id === step.id).at(-1);
          const stepError = attempt?.error && attempt.status !== "pass" ? `<small class="record-reason">${esc(attempt.error)}</small>` : "";
          return `<div class="step"><span class="step-number">${step.ordinal}</span><div><strong>${esc(step.description)}</strong><small>${esc(step.capability)}@${esc(step.capability_version)}${attempt?.provider ? ` · ${esc(attempt.provider)}` : ""}</small>${stepError}</div>${pill(step.status)}</div>`;
        }).join("") || '<p class="muted-panel">No steps recorded.</p>'}
        ${approvals.map((approval) => `<article class="panel approval-card"><strong>${esc(approval.requested_action)}</strong><p>Explicit approval is required before Atlas may continue.</p><div class="approval-actions"><button class="primary" data-approval="${esc(approval.id)}" data-decision="approve">Approve</button><button class="danger" data-approval="${esc(approval.id)}" data-decision="deny">Deny</button></div></article>`).join("")}
      </details>`;
    if (canAct) {
      $("#run-task").onclick = () => mutate(`/api/tasks/${id}/run`, "Task resumed.");
      $("#cancel-task").onclick = () => mutate(`/api/tasks/${id}/cancel`, "Task cancelled.");
    }
    $("#delete-task").onclick = () => deleteTask(id);
    detail.querySelectorAll("[data-approval]").forEach((button) => {
      button.onclick = () => mutate(`/api/approvals/${button.dataset.approval}/${button.dataset.decision}`, `Approval ${button.dataset.decision}d.`);
    });
  } catch (error) {
    detail.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
  }
}

async function deleteTask(id) {
  const typed = window.prompt(`This permanently deletes the work and its evidence.\nType ${id} to confirm.`);
  if (typed !== id) return;
  if (busy) return;
  busy = true;
  try {
    await api(`/api/tasks/${id}`, { method: "DELETE", body: JSON.stringify({ confirm_id: id }) });
    toast("Work deleted.");
    $("#detail").innerHTML = '<div class="empty"><strong>Select a task</strong><p>Inspect the result first, then how Atlas executed it.</p></div>';
    await refresh();
    writeHash("work", { workView: state.workView === "overview" ? "one-off" : state.workView });
  } catch (error) {
    toast(error.message);
  } finally {
    busy = false;
  }
}

function appendAsk(role, html) {
  const thread = $("#ask-thread");
  const welcome = thread.querySelector(".ask-welcome");
  if (welcome) welcome.remove();
  thread.insertAdjacentHTML("beforeend", `<article class="ask-bubble ${role}">${html}</article>`);
  thread.scrollTop = thread.scrollHeight;
}

function turnChip(turn) {
  if (turn.task_id && turn.task_status !== "deleted") {
    const status = turn.task_status ? ` · ${esc(turn.task_status)}` : "";
    return `<button class="work-chip" data-task-id="${esc(turn.task_id)}">Open in Work${status}</button>`;
  }
  if (turn.task_status === "deleted") return '<span class="muted-panel">Linked work was deleted</span>';
  return "";
}

function renderAskTurns(turns) {
  const thread = $("#ask-thread");
  if (!thread) return;
  if (!turns || !turns.length) {
    thread.innerHTML = ASK_WELCOME;
    return;
  }
  thread.innerHTML = turns.map((turn) => `<article class="ask-bubble ${esc(turn.role)}"><p>${esc(turn.content)}</p>${turnChip(turn)}</article>`).join("");
  bindTaskLinks(thread);
  thread.scrollTop = thread.scrollHeight;
}

async function loadAsk() {
  try {
    const path = state.conversationId ? `/api/conversations/${state.conversationId}` : "/api/conversations/current";
    const data = await api(path);
    state.conversationId = data.id;
    renderAskTurns(data.turns);
  } catch (error) {
    toast(error.message);
  }
}

async function mutate(url, success) {
  if (busy) return;
  busy = true;
  try {
    const data = await api(url, { method: "POST", body: "{}" });
    toast(success);
    await refresh();
    if (data.presentation?.task_id) await openTask(data.presentation.task_id);
    return data;
  } catch (error) {
    toast(error.message);
    throw error;
  } finally {
    busy = false;
  }
}

async function loadApprovals() {
  try {
    state.approvals = await api("/api/approvals");
  } catch (_) {
    state.approvals = [];
  }
  updateWorkBadge();
  renderOverview();
}

function healthCard(title, rows) {
  return `<article class="health-card"><h3>${esc(title)}</h3><dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${value}</dd>`).join("")}</dl></article>`;
}

function identityFromHealth(h) {
  const runtime = h.runtime || {};
  const providers = [...(runtime.providers || [])].sort((a, b) => Number(Boolean(a.local)) - Number(Boolean(b.local)));
  const model = providers.length
    ? providers.map((item) => `${item.key} · ${item.model}`).join(" + ")
    : "No enabled model";
  return {
    healthy: h.atlas?.healthy,
    assembler: runtime.assembler_version || "Unavailable",
    model,
    started: runtime.started_at || "Unavailable",
    pid: runtime.pid ?? "Unavailable",
  };
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    state.health = h;
    const g = h.gpu, m = h.machine, a = h.atlas;
    const ident = identityFromHealth(h);
    $("#status-strip").innerHTML = `<b>${ident.healthy ? "Atlas healthy" : "Atlas unavailable"}</b><span>${esc(ident.model)}</span><span>asm ${esc(ident.assembler)}</span><span>GPU ${unit(g.utilization_percent, "%")}</span><span>VRAM ${g.available ? `${unit(g.memory_used_mib, " MiB")} / ${unit(g.memory_total_mib, " MiB")}` : "Unavailable"}</span><span>${a.running_executions} running</span>`;
    const runtime = h.runtime || {};
    const providerRows = (runtime.providers || []).map((item) => [item.key, `${esc(item.model)} · ${item.local ? "local" : "cloud"}`]);
    $("#health").innerHTML = healthCard("Runtime identity", [["Assembler", esc(runtime.assembler_version)], ["PID", esc(runtime.pid)], ["Started", esc(runtime.started_at)], ["Provider config", esc(runtime.provider_config)], ["Enabled", (runtime.providers || []).map((item) => item.key).join(", ") || "None"], ["Disabled", (runtime.disabled_provider_keys || []).join(", ") || "None"]]) +
      healthCard("Active models", providerRows.length ? providerRows : [["Status", "No enabled provider"]]) +
      healthCard("Machine", [["CPU utilization", unit(m.cpu.utilization_percent, "%")], ["Load", esc(m.cpu.load_average?.map((value) => value.toFixed(2)).join(" / "))], ["CPU temperature", unit(m.cpu.temperature_c, "°C")], ["RAM", `${bytes(m.memory.used_bytes)} / ${bytes(m.memory.total_bytes)}`], ["Swap", `${bytes(m.memory.swap_used_bytes)} / ${bytes(m.memory.swap_total_bytes)}`], ["Root disk", `${bytes(m.root_disk.used_bytes)} / ${bytes(m.root_disk.total_bytes)}`], ["Models footprint", bytes(m.models_bytes)], ["Atlas DB", bytes(m.atlas_db_bytes)]]) +
      healthCard(g.name ? `GPU — ${g.name}` : "GPU", g.available ? [["Utilization", unit(g.utilization_percent, "%")], ["VRAM", `${unit(g.memory_used_mib, " MiB")} / ${unit(g.memory_total_mib, " MiB")}`], ["Temperature", unit(g.temperature_c, "°C")], ["Power", `${unit(g.power_draw_w, " W")} / ${unit(g.power_limit_w, " W")}`], ["Performance state", esc(g.performance_state)], ["Active processes", g.processes.length ? g.processes.map((process) => `${esc(process.name)} · ${unit(process.memory_mib, " MiB")}`).join("<br>") : "None"]] : [["Status", "Unavailable"]]) +
      healthCard("Atlas runtime", [["Runtime", a.healthy ? "Healthy" : "Unavailable"], ["Provider configured", a.provider_configured ? "Yes" : "No"], ["Inference health", a.provider_healthy == null ? "Not configured" : a.provider_healthy ? "Healthy" : "Unavailable"], ["Durable active tasks", a.active_tasks], ["Running executions", a.running_executions], ["Knowledge documents", a.knowledge_documents ?? state.documents.length], ["Pending approvals", a.pending_approvals ?? state.approvals.length]]) +
      healthCard("Docker", h.docker == null ? [["Status", "Unavailable"]] : h.docker.map((container) => [container.name, `${esc(container.state)} · ${esc(container.status)}`])) +
      healthCard("Network", [["Server IP", esc(h.network.server_ip)], ["Companion bind", esc(h.network.companion_bind)], ["Inference bind", esc(h.network.inference_bind)]]) +
      healthCard("Git", [["Branch", esc(h.git.branch)], ["Commit", esc(h.git.commit)], ["Worktree", h.git.worktree_clean == null ? "Unavailable" : h.git.worktree_clean ? "Clean" : "Dirty"]]);
    renderOverview();
  } catch (error) {
    $("#status-strip").textContent = error.message;
    $("#health").innerHTML = `<article class="panel">${esc(error.message)}</article>`;
  }
}

async function loadTasks() {
  state.tasks = await api("/api/tasks");
  renderWork();
  renderOverview();
  renderKnowledge();
  updateWorkBadge();
}

function renderCloudProviders(rows) {
  const root = $("#cloud-providers");
  if (!root) return;
  if (!rows.length) {
    root.innerHTML = '<p class="muted-panel">No cloud providers are in the overlay.</p>';
    return;
  }
  root.innerHTML = rows.map((row) => {
    const models = row.discovered_models || [];
    const options = models.length
      ? models.map((model) => `<option value="${esc(model)}" ${model === row.selected_model ? "selected" : ""}>${esc(model)}</option>`).join("")
      : `<option value="${esc(row.selected_model || "")}">${esc(row.selected_model || "No models discovered")}</option>`;
    const manage = row.manageable
      ? `<form class="cloud-key-form" data-provider="${esc(row.key)}">
            <label><span>API key</span><input type="password" name="api_key" autocomplete="off" placeholder="${row.configured ? "Saved — enter a new key to replace" : "Paste key"}"></label>
            <div class="form-actions">
              <button class="primary" type="submit">Save and verify</button>
              ${row.configured ? '<button class="danger" type="button" data-cloud-action="remove">Remove key</button>' : ""}
              <button class="secondary" type="button" data-cloud-action="verify">Verify</button>
              <button class="secondary" type="button" data-cloud-action="refresh">Refresh models</button>
            </div>
          </form>
          <div class="form-grid">
            <label><span>Selected model</span><select data-cloud-action="select" data-provider="${esc(row.key)}">${options}</select></label>
            <label class="toggle-label"><span>Enabled</span><input type="checkbox" data-cloud-action="enable" data-provider="${esc(row.key)}" ${row.enabled ? "checked" : ""} ${row.configured ? "" : "disabled"}></label>
          </div>`
      : '<p class="muted-panel">This vendor is listed in the overlay but not manageable in this slice.</p>';
    return `<article class="provider-card">
      <div class="record-row"><strong>${esc(row.key)}</strong>${pill(row.enabled ? "active" : "blocked")}</div>
      <p class="technical">${esc(row.kind)} · ${row.configured ? "key configured" : "no key"} · ${row.verified ? `verified ${esc(row.verified_at || "")}` : "not verified"} · ${esc(row.source || "none")}</p>
      ${row.last_error ? `<p class="record-reason">${esc(row.last_error)}</p>` : ""}
      ${manage}
    </article>`;
  }).join("");
  root.querySelectorAll(".cloud-key-form").forEach((form) => {
    form.onsubmit = async (event) => {
      event.preventDefault();
      const key = form.dataset.provider;
      const apiKey = new FormData(form).get("api_key");
      try {
        await api(`/api/models/cloud/${key}/credentials`, {
          method: "POST",
          body: JSON.stringify({ api_key: apiKey }),
        });
        toast("Credential saved. The key is not shown again.");
        form.querySelector("[name=api_key]").value = "";
        await loadCloudProviders();
        await loadHealth();
      } catch (error) {
        toast(error.message);
      }
    };
  });
  root.querySelectorAll("[data-cloud-action]").forEach((node) => {
    const action = node.dataset.cloudAction;
    const key = node.dataset.provider || node.closest("[data-provider]")?.dataset.provider;
    if (!key) return;
    if (action === "enable" && node.type === "checkbox") {
      node.onchange = async () => {
        try {
          await api(`/api/models/cloud/${key}/enable`, {
            method: "POST",
            body: JSON.stringify({ enabled: node.checked }),
          });
          toast(node.checked ? "Cloud model is now the active Atlas brain." : "Cloud model disabled.");
          await loadCloudProviders();
          await loadHealth();
        } catch (error) {
          toast(error.message);
          await loadCloudProviders();
        }
      };
      return;
    }
    if (action === "select" && node.tagName === "SELECT") {
      node.onchange = async () => {
        try {
          await api(`/api/models/cloud/${key}/select`, {
            method: "POST",
            body: JSON.stringify({ model: node.value }),
          });
          toast(`Selected ${node.value}.`);
          await loadCloudProviders();
          await loadHealth();
        } catch (error) {
          toast(error.message);
        }
      };
      return;
    }
    node.onclick = async (event) => {
      event.preventDefault();
      try {
        if (action === "remove") {
          await api(`/api/models/cloud/${key}/credentials`, { method: "DELETE", body: "{}" });
          toast("Credential removed.");
        } else if (action === "verify") {
          await api(`/api/models/cloud/${key}/verify`, { method: "POST", body: "{}" });
          toast("Credential verified.");
        } else if (action === "refresh") {
          await api(`/api/models/cloud/${key}/models/refresh`, { method: "POST", body: "{}" });
          toast("Model list refreshed.");
        }
        await loadCloudProviders();
        await loadHealth();
      } catch (error) {
        toast(error.message);
        await loadCloudProviders();
      }
    };
  });
}

function renderLocalModels(data) {
  const root = $("#local-models");
  if (!root) return;
  const slots = data.slots || [];
  const gpu = data.gpu || {};
  const extras = data.unmapped_gguf || [];
  const cards = slots.map((slot) => {
    const actions = slot.status === "loaded" || slot.status === "loading"
      ? `<button class="secondary" data-local-action="unload" data-id="${esc(slot.id)}">Unload</button>`
      : `<button class="secondary" data-local-action="load" data-id="${esc(slot.id)}">Load</button>`;
    return `<article class="provider-card">
      <div class="record-row"><strong>${esc(slot.name)}</strong>${pill(slot.status)}</div>
      <p class="technical">${esc(slot.id)} · ${esc(slot.alias)} · ${esc(slot.quantization)} · ctx ${esc(slot.context)} · ${esc(slot.endpoint)}</p>
      <p class="technical">${esc(slot.gguf)}${slot.on_disk === false ? " · missing on disk" : ""}</p>
      <div class="form-actions">${actions}<button class="primary" data-local-action="activate" data-id="${esc(slot.id)}">Activate</button></div>
    </article>`;
  }).join("");
  const extra = extras.length
    ? `<p class="muted-panel">On disk, not wired to a compose service: ${extras.map((item) => esc(item.path)).join(", ")}</p>`
    : "";
  root.innerHTML = `${cards}${extra}<p class="technical">GPU ${gpu.memory_used_mib ?? "?"} / ${gpu.memory_total_mib ?? "?"} MiB</p>`;
  root.querySelectorAll("[data-local-action]").forEach((button) => {
    button.onclick = async () => {
      const action = button.dataset.localAction;
      const id = button.dataset.id;
      try {
        await api(`/api/models/local/${action}`, { method: "POST", body: JSON.stringify({ id }) });
        toast(action === "activate" ? `Activated ${id}.` : `${action} ${id}.`);
        await loadModels();
        await loadHealth();
      } catch (error) {
        toast(error.message);
        await loadModels();
      }
    };
  });
}

async function loadCloudProviders() {
  const root = $("#cloud-providers");
  if (!root) return;
  try {
    const rows = await api("/api/models/cloud");
    renderCloudProviders(rows);
  } catch (error) {
    root.innerHTML = `<p class="muted-panel">${esc(error.message)}</p>`;
  }
}

async function loadModels() {
  await Promise.all([loadCloudProviders(), loadLocalModels()]);
}

async function loadLocalModels() {
  const root = $("#local-models");
  if (!root) return;
  try {
    renderLocalModels(await api("/api/models/local"));
  } catch (error) {
    root.innerHTML = `<p class="muted-panel">${esc(error.message)}</p>`;
  }
}

async function loadKnowledge() {
  try {
    state.documents = await api("/api/knowledge/documents");
  } catch (_) {
    state.documents = [];
  }
  renderKnowledge();
}

async function refresh() {
  await Promise.all([loadTasks(), loadHealth(), loadKnowledge(), loadApprovals(), loadAsk()]);
}

async function inspectPath() {
  const path = $("#knowledge-ingest [name=source_path]").value.trim();
  const meta = $("#file-meta");
  const status = $("#index-status");
  if (!path) {
    meta.textContent = "Enter a path on this Atlas host.";
    return;
  }
  status.textContent = "Inspecting host path…";
  try {
    const data = await api(`/api/knowledge/stat?path=${encodeURIComponent(path)}`);
    meta.textContent = `${data.title} · ${data.byte_size} bytes · sha256 ${data.content_sha256} · ${data.path}`;
    status.textContent = "Path is readable. Work execution is disconnected from Companion.";
    return data;
  } catch (error) {
    meta.textContent = error.message;
    status.textContent = "";
    throw error;
  }
}

function applyLocation() {
  const parsed = parseHash();
  state.workView = parsed.workView;
  state.knowledgeView = parsed.knowledgeView || "library";
  showScreen(parsed.screen, { workView: parsed.workView, knowledgeView: state.knowledgeView, taskId: parsed.taskId, syncHash: false });
  if (parsed.taskId) openTask(parsed.taskId);
}

document.querySelectorAll("[data-screen]").forEach((button) => {
  button.onclick = () => showScreen(button.dataset.screen, {
    workView: button.dataset.screen === "work" ? "overview" : state.workView,
    knowledgeView: button.dataset.screen === "knowledge" ? "library" : state.knowledgeView,
  });
});
document.querySelectorAll("#knowledge-tabs [data-knowledge-view]").forEach((button) => {
  button.onclick = () => showScreen("knowledge", { knowledgeView: button.dataset.knowledgeView });
});
document.querySelectorAll("[data-goto]").forEach((button) => {
  button.onclick = () => showScreen(button.dataset.goto);
});
document.querySelectorAll("#work-tabs [data-work-view]").forEach((button) => {
  button.onclick = () => {
    showScreen("work", { workView: button.dataset.workView });
  };
});
document.querySelectorAll("#work-filters .filter").forEach((button) => {
  button.onclick = () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll("#work-filters .filter").forEach((item) => item.classList.toggle("active", item === button));
    renderWork();
  };
});
$("#refresh-health").onclick = loadHealth;
$("#refresh-models").onclick = loadModels;
$("#inspect-path").onclick = () => inspectPath().catch(() => {});
$("#knowledge-search").onsubmit = async (event) => {
  event.preventDefault();
  const query = new FormData(event.target).get("q");
  try {
    state.hits = await api(`/api/knowledge/search?q=${encodeURIComponent(query)}`);
    renderHits(state.hits);
  } catch (error) {
    toast(error.message);
  }
};
$("#knowledge-ingest").onsubmit = async (event) => {
  event.preventDefault();
  if (busy) return;
  const status = $("#index-status");
  busy = true;
  status.textContent = "Creating path-backed ingest task…";
  try {
    const form = new FormData(event.target);
    const data = await api("/api/knowledge/ingest", {
      method: "POST",
      body: JSON.stringify({ source_path: form.get("source_path") }),
    });
    const taskId = data.presentation?.task_id;
    status.textContent = taskId ? `Ingest recorded as Work ${taskId}.` : "Ingest task finished.";
    toast("Knowledge ingest recorded.");
    await refresh();
    showScreen("knowledge", { knowledgeView: "indexing" });
    if (taskId) {
      status.innerHTML = `Ingest recorded. <button class="work-chip" data-task-id="${esc(taskId)}">Open in Work</button>`;
      bindTaskLinks(status);
    }
  } catch (error) {
    status.textContent = error.message;
    toast(error.message);
  } finally {
    busy = false;
  }
};
$("#new-task").onsubmit = async (event) => {
  event.preventDefault();
  if (busy) return;
  const form = new FormData(event.target);
  const message = $("#message");
  const objective = String(form.get("objective") || "").trim();
  const criteria = String(form.get("criteria") || "").trim();
  const authority = String(form.get("authority") || "auto");
  if (!objective) return;
  busy = true;
  message.textContent = "Atlas is working…";
  appendAsk("user", `<p>${esc(objective)}</p>`);
  try {
    const data = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({
        message: objective,
        conversation_id: state.conversationId,
        criteria: criteria || null,
        authority,
      }),
    });
    event.target.querySelector("[name=objective]").value = "";
    message.textContent = "";
    state.conversationId = data.conversation_id || state.conversationId;
    renderAskTurns(data.conversation?.turns);
    toast("Atlas replied.");
    await refresh();
  } catch (error) {
    message.textContent = error.message;
    try {
      await loadAsk();
    } catch (_) {
      appendAsk("atlas", `<p>${esc(error.message)}</p>`);
    }
  } finally {
    busy = false;
  }
};

window.addEventListener("hashchange", applyLocation);
if (!location.hash) writeHash("ask", { replace: true });
applyLocation();
refresh().catch((error) => toast(error.message));
setInterval(() => loadHealth(), 15000);
