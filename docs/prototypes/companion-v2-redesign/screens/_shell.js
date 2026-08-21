function shell(active, title, subtitle, actionsHtml, bodyHtml) {
  const items = [
    ["home.html", "Home"],
    ["chat.html", "Chat"],
    ["work-list.html", "Work"],
    ["plan-work.html", "Plan"],
    ["#", "Knowledge"],
    ["#", "Files"],
    ["#", "Settings"],
  ];
  const nav = items
    .map(
      ([href, label]) =>
        `<a class="${label === active ? "active" : ""}" href="${href}"><span class="dot"></span>${label}</a>`,
    )
    .join("");
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${title} · Atlas Companion proposal</title>
  <link rel="stylesheet" href="../design-system.css" />
</head>
<body>
  <div class="proposal-banner">Visual proposal only — not wired to production Companion.</div>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="mark"></div>
        <div>
          <strong>Atlas</strong>
          <span>Personal workspace</span>
        </div>
      </div>
      <nav class="nav">${nav}</nav>
      <div class="sidebar-foot">
        <strong>Signed in</strong>
        <small>Owner session · host local</small>
      </div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div>
          <h1>${title}</h1>
          <p>${subtitle}</p>
        </div>
        <div class="actions">${actionsHtml || ""}</div>
      </div>
      ${bodyHtml}
    </main>
  </div>
</body>
</html>`;
}

if (typeof module !== "undefined") module.exports = { shell };
