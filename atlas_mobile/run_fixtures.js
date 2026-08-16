/* Fixture runner: node atlas_mobile/run_fixtures.js */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadScript(file) {
  const code = fs.readFileSync(file, "utf8");
  vm.runInThisContext(code, { filename: file });
}

const root = __dirname;
loadScript(path.join(root, "js", "records.js"));
loadScript(path.join(root, "js", "directory.js"));
loadScript(path.join(root, "js", "validate.js"));
loadScript(path.join(root, "js", "assemble.js"));

const golden = JSON.parse(
  fs.readFileSync(path.join(root, "fixtures", "golden.json"), "utf8")
);

let failed = 0;
function check(name, cond, detail) {
  if (!cond) {
    failed += 1;
    console.error("FAIL", name, detail || "");
  } else {
    console.log("ok  ", name);
  }
}

golden.cases.forEach(function (testCase) {
  if (testCase.expect_thread) {
    const open = AtlasRecords.threadIsOpen(
      testCase.expect_thread.subject,
      testCase.expect_thread.reports
    );
    check(
      testCase.id,
      open === testCase.expect_thread.open,
      "open=" + open + " expected=" + testCase.expect_thread.open
    );
    return;
  }
  const reports = [];
  if (testCase.context && testCase.context.prior) {
    reports.push(testCase.context.prior);
  }
  const result = AtlasValidate.validateActivity(testCase.activity, {
    machines: golden.machines,
    reportsInOrder: reports.concat([{ activities: [] }]),
  });
  check(
    testCase.id + " status",
    result.status === testCase.expect.status,
    result.status + " != " + testCase.expect.status + " " + JSON.stringify(result)
  );
  if (testCase.expect.red_contains) {
    const hit = (result.red || []).some(function (msg) {
      return msg.indexOf(testCase.expect.red_contains) !== -1;
    });
    check(testCase.id + " red", hit, result.red);
  }
  if (testCase.expect.orange_code) {
    const hit = (result.orange || []).some(function (item) {
      return item.code === testCase.expect.orange_code;
    });
    check(testCase.id + " orange", hit, result.orange);
  }
  if (testCase.expect.closes_thread) {
    check(
      testCase.id + " closes",
      AtlasRecords.closesThread(testCase.activity),
      "expected Continue+Running to close"
    );
    const after = {
      activities: (testCase.context.prior.activities || []).concat([
        testCase.activity,
      ]),
    };
    check(
      testCase.id + " thread closed",
      AtlasRecords.threadIsOpen("ARB4", [testCase.context.prior, after]) ===
        false,
      "thread still open"
    );
  }
});

const assembled = AtlasAssemble.whatsappText(golden.assemble.report);
(golden.assemble.expect_contains || []).forEach(function (needle) {
  check("whatsapp contains " + needle, assembled.indexOf(needle) !== -1, assembled);
});
(golden.assemble.expect_absent || []).forEach(function (needle) {
  check("whatsapp absent " + needle, assembled.indexOf(needle) === -1, assembled);
});

if (failed) {
  console.error("\n" + failed + " failed");
  process.exit(1);
}
console.log("\nAll golden fixtures passed.");
