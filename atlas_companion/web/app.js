const $ = (selector) => document.querySelector(selector);
const state = { tasks: [], health: null, filter: "all" };
const screenTitles = { today: "Today", work: "Work", ask: "Ask", knowledge: "Knowledge", approvals: "Approvals", health: "Health" };

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

function toast(text) {
  const node = $("#toast");
  node.textContent = text;
  node.style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.style.display = "none"; }, 2800);
}

function showScreen(name) {
  document.querySelectorAll(".screen").forEach((node) => node.classList.toggle("active", node.id === `${name}-screen`));
  document.querySelectorAll(".nav-btn").forEach((node) => node.classList.toggle("active", node.dataset.screen === name));
  $("#crumb").textContent = screenTitles[name];
  $("#page-title").textContent = screenTitles[name];
  if (name === "approvals") loadApprovals();
  if (name === "health") loadHealth();
}

function record(task) {
  return `<button class="record" data-task-id="${esc(task.id)}"><span class="record-row"><span class="record-title">${esc(task.objective)}</span>${pill(task.status)}</span><span class="record-meta">${esc(task.id)} · ${esc(task.authority_scope)}</span></button>`;
}

function bindTaskLinks(root = document) {
  root.querySelectorAll("[data-task-id]").forEach((node) => {
    node.onclick = () => openTask(node.dataset.taskId);
  });
}

function renderToday() {
  const open = state.tasks.filter(isOpen);
  const completed = state.tasks.filter((task) => task.status === "completed");
  const failed = state.tasks.filter((task) => task.status === "failed");
  const running = state.health?.atlas?.running_executions ?? 0;
  $("#today-metrics").innerHTML = [
    metric("Open work", open.length, `${running} execution${running === 1 ? "" : "s"} actually running`),
    metric("Completed", completed.length, "Durable verified outcomes"),
    metric("Failed", failed.length, failed.length ? "Review failure evidence" : "No failed work"),
    metric("Provider", state.health?.atlas?.provider_healthy ? "Healthy" : "Unavailable", state.health?.network?.inference_bind || "Server-side only"),
  ].join("");
  const attention = [...state.tasks.filter((task) => task.status === "waiting"), ...failed].slice(0, 5);
  $("#needs-attention").innerHTML = attention.length ? attention.map(record).join("") : '<div class="empty"><p>Nothing currently needs attention.</p></div>';
  $("#recent-outcomes").innerHTML = completed.slice(0, 5).map(record).join("") || '<div class="empty"><p>No completed tasks yet.</p></div>';
  bindTaskLinks($("#today-screen"));
}

function renderWork() {
  const filtered = state.tasks.filter((task) => {
    if (state.filter === "all") return true;
    if (state.filter === "open") return isOpen(task);
    return task.status === state.filter;
  });
  $("#tasks").innerHTML = filtered.length ? filtered.map(record).join("") : '<div class="empty"><p>No tasks in this view.</p></div>';
  bindTaskLinks($("#tasks"));
}

async function openTask(id) {
  showScreen("work");
  const detail = $("#detail");
  detail.innerHTML = '<div class="empty"><p>Loading durable task…</p></div>';
  try {
    const data = await api(`/api/tasks/${id}`);
    const snapshot = data.snapshot;
    const task = snapshot.task;
    const steps = snapshot.steps || [];
    const executions = snapshot.executions || [];
    const approvals = data.presentation.pending_approvals || [];
    detail.innerHTML = `
      <div class="task-head"><div><h3>${esc(task.objective)}</h3><p class="technical">${esc(task.id)} · authority ${esc(task.authority_scope)}</p></div>${pill(task.status)}</div>
      <div class="detail-actions"><button class="secondary" id="run-task">Run / resume</button><button class="danger" id="cancel-task">Cancel</button></div>
      <h4 class="subhead">Success criteria</h4>${(snapshot.criteria || []).map((item) => `<div class="record-row"><span>${esc(item.text)}</span>${pill(item.status)}</div>`).join("") || '<p class="muted-panel">No criteria recorded.</p>'}
      <h4 class="subhead">Plan and execution</h4>${steps.map((step) => {
        const attempt = executions.filter((item) => item.step_id === step.id).at(-1);
        return `<div class="step"><span class="step-number">${step.ordinal}</span><div><strong>${esc(step.description)}</strong><small>${esc(step.capability)}@${esc(step.capability_version)}${attempt?.provider ? ` · ${esc(attempt.provider)}` : ""}</small></div>${pill(step.status)}</div>`;
      }).join("") || '<p class="muted-panel">No steps recorded.</p>'}
      ${approvals.map((approval) => `<article class="panel approval-card"><strong>${esc(approval.requested_action)}</strong><p>Explicit approval is required before Atlas may continue.</p><div class="approval-actions"><button class="primary" data-approval="${esc(approval.id)}" data-decision="approve">Approve</button><button class="danger" data-approval="${esc(approval.id)}" data-decision="deny">Deny</button></div></article>`).join("")}
      <h4 class="subhead">Presented result</h4><pre class="result">${esc(data.markdown)}</pre>`;
    $("#run-task").onclick = () => mutate(`/api/tasks/${id}/run`, "Task resumed.");
    $("#cancel-task").onclick = () => mutate(`/api/tasks/${id}/cancel`, "Task cancelled.");
    detail.querySelectorAll("[data-approval]").forEach((button) => {
      button.onclick = () => mutate(`/api/approvals/${button.dataset.approval}/${button.dataset.decision}`, `Approval ${button.dataset.decision}d.`);
    });
  } catch (error) {
    detail.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
  }
}

async function mutate(url, success) {
  try {
    const data = await api(url, { method: "POST", body: "{}" });
    toast(success);
    await loadTasks();
    await loadHealth();
    if (data.presentation?.task_id) await openTask(data.presentation.task_id);
  } catch (error) { toast(error.message); }
}

function renderKnowledge() {
  const knowledge = state.tasks.filter((task) => /knowledge|index|search/i.test(task.objective));
  const indexed = knowledge.filter((task) => task.status === "completed" && /index/i.test(task.objective));
  const searches = knowledge.filter((task) => /search/i.test(task.objective));
  $("#knowledge-metrics").innerHTML = [
    metric("Knowledge tasks", knowledge.length, "Durable runtime records"),
    metric("Completed ingests", indexed.length, "Path/hash-backed outcomes"),
    metric("Search tasks", searches.length, "Source-grounded retrieval"),
    metric("Open knowledge work", knowledge.filter(isOpen).length, "Ready or awaiting action"),
  ].join("");
  $("#knowledge-tasks").innerHTML = knowledge.length ? knowledge.map(record).join("") : '<div class="empty"><p>No knowledge work recorded.</p></div>';
  bindTaskLinks($("#knowledge-tasks"));
}

async function loadApprovals() {
  const candidates = state.tasks.filter(isOpen);
  const records = [];
  await Promise.all(candidates.map(async (task) => {
    try {
      const data = await api(`/api/tasks/${task.id}`);
      for (const approval of data.presentation.pending_approvals || []) records.push({ task, approval });
    } catch (_) { /* keep the rest of the screen available */ }
  }));
  $("#approval-count").textContent = `${records.length} waiting`;
  $("#approvals-list").innerHTML = records.length ? records.map(({ task, approval }) => `
    <article class="panel approval-card"><div class="record-row"><strong>${esc(task.objective)}</strong>${pill("pending")}</div><p>${esc(approval.requested_action)}</p><p class="technical">${esc(task.id)} · ${esc(approval.id)}</p><div class="approval-actions"><button class="primary" data-approval="${esc(approval.id)}" data-decision="approve">Approve</button><button class="danger" data-approval="${esc(approval.id)}" data-decision="deny">Deny</button><button class="secondary" data-task-id="${esc(task.id)}">Open task</button></div></article>`).join("") : '<article class="panel muted-panel">No approvals are waiting.</article>';
  $("#approvals-list").querySelectorAll("[data-approval]").forEach((button) => {
    button.onclick = () => mutate(`/api/approvals/${button.dataset.approval}/${button.dataset.decision}`, `Approval ${button.dataset.decision}d.`).then(loadApprovals);
  });
  bindTaskLinks($("#approvals-list"));
}

function healthCard(title, rows) {
  return `<article class="health-card"><h3>${esc(title)}</h3><dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${value}</dd>`).join("")}</dl></article>`;
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    state.health = h;
    const g = h.gpu, m = h.machine, a = h.atlas;
    $("#status-strip").innerHTML = `<b>${a.healthy ? "Atlas healthy" : "Atlas unavailable"}</b><span>GPU ${unit(g.utilization_percent, "%")}</span><span>VRAM ${g.available ? `${unit(g.memory_used_mib, " MiB")} / ${unit(g.memory_total_mib, " MiB")}` : "Unavailable"}</span><span>${unit(g.temperature_c, "°C")}</span><span>${a.running_executions} running</span>`;
    $("#health").innerHTML = healthCard("Machine", [["CPU utilization", unit(m.cpu.utilization_percent, "%")], ["Load", esc(m.cpu.load_average?.map((value) => value.toFixed(2)).join(" / "))], ["CPU temperature", unit(m.cpu.temperature_c, "°C")], ["RAM", `${bytes(m.memory.used_bytes)} / ${bytes(m.memory.total_bytes)}`], ["Swap", `${bytes(m.memory.swap_used_bytes)} / ${bytes(m.memory.swap_total_bytes)}`], ["Root disk", `${bytes(m.root_disk.used_bytes)} / ${bytes(m.root_disk.total_bytes)}`], ["Models footprint", bytes(m.models_bytes)], ["Atlas DB", bytes(m.atlas_db_bytes)]]) +
      healthCard(g.name ? `GPU — ${g.name}` : "GPU", g.available ? [["Utilization", unit(g.utilization_percent, "%")], ["VRAM", `${unit(g.memory_used_mib, " MiB")} / ${unit(g.memory_total_mib, " MiB")}`], ["Temperature", unit(g.temperature_c, "°C")], ["Power", `${unit(g.power_draw_w, " W")} / ${unit(g.power_limit_w, " W")}`], ["Performance state", esc(g.performance_state)], ["Active processes", g.processes.length ? g.processes.map((process) => `${esc(process.name)} · ${unit(process.memory_mib, " MiB")}`).join("<br>") : "None"]] : [["Status", "Unavailable"]]) +
      healthCard("Atlas runtime", [["Runtime", a.healthy ? "Healthy" : "Unavailable"], ["Provider configured", a.provider_configured ? "Yes" : "No"], ["Inference health", a.provider_healthy == null ? "Not configured" : a.provider_healthy ? "Healthy" : "Unavailable"], ["Durable active tasks", a.active_tasks], ["Running executions", a.running_executions], ["GPU inference", a.running_executions ? "May be active" : "Idle"]]) +
      healthCard("Docker", h.docker == null ? [["Status", "Unavailable"]] : h.docker.map((container) => [container.name, `${esc(container.state)} · ${esc(container.status)}`])) +
      healthCard("Network", [["Server IP", esc(h.network.server_ip)], ["Companion bind", esc(h.network.companion_bind)], ["Inference bind", esc(h.network.inference_bind)]]) +
      healthCard("Git", [["Branch", esc(h.git.branch)], ["Commit", esc(h.git.commit)], ["Worktree", h.git.worktree_clean == null ? "Unavailable" : h.git.worktree_clean ? "Clean" : "Dirty"]]);
    renderToday();
  } catch (error) { $("#status-strip").textContent = error.message; $("#health").innerHTML = `<article class="panel">${esc(error.message)}</article>`; }
}

async function loadTasks() {
  state.tasks = await api("/api/tasks");
  renderWork(); renderToday(); renderKnowledge();
}

document.querySelectorAll("[data-screen]").forEach((button) => { button.onclick = () => showScreen(button.dataset.screen); });
document.querySelectorAll("[data-goto]").forEach((button) => { button.onclick = () => showScreen(button.dataset.goto); });
document.querySelectorAll(".filter").forEach((button) => {
  button.onclick = () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll(".filter").forEach((item) => item.classList.toggle("active", item === button));
    renderWork();
  };
});
$("#refresh-health").onclick = loadHealth;
$("#new-task").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const message = $("#message");
  message.textContent = "Creating durable task…";
  try {
    const data = await api("/api/tasks", { method: "POST", body: JSON.stringify({ objective: form.get("objective"), criteria: form.get("criteria"), authority: form.get("authority") }) });
    event.target.reset(); message.textContent = "Task created."; toast("Durable task created.");
    await loadTasks(); await loadHealth(); await openTask(data.presentation.task_id);
  } catch (error) { message.textContent = error.message; }
};

Promise.all([loadTasks(), loadHealth()]).then(loadApprovals).catch((error) => toast(error.message));
setInterval(() => loadHealth(), 15000);
