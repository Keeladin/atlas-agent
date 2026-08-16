/**
 * Durable on-device Atlas report storage (IndexedDB).
 */
(function (global) {
  const DB_NAME = "atlas-agent";
  const DB_VERSION = 1;
  const STORE = "reports";

  function openDb() {
    return new Promise(function (resolve, reject) {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = function () {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "id" });
        }
      };
      request.onsuccess = function () {
        resolve(request.result);
      };
      request.onerror = function () {
        reject(request.error);
      };
    });
  }

  function txDone(tx) {
    return new Promise(function (resolve, reject) {
      tx.oncomplete = function () {
        resolve();
      };
      tx.onerror = function () {
        reject(tx.error);
      };
      tx.onabort = function () {
        reject(tx.error);
      };
    });
  }

  async function putReport(report) {
    report.updated_at = new Date().toISOString();
    const db = await openDb();
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(report);
    await txDone(tx);
    db.close();
    return report;
  }

  async function getReport(id) {
    const db = await openDb();
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).get(id);
      req.onsuccess = function () {
        resolve(req.result || null);
      };
      req.onerror = function () {
        reject(req.error);
      };
      tx.oncomplete = function () {
        db.close();
      };
    });
  }

  async function listReports() {
    const db = await openDb();
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = function () {
        const rows = req.result || [];
        rows.sort(function (a, b) {
          return String(b.updated_at).localeCompare(String(a.updated_at));
        });
        resolve(rows);
      };
      req.onerror = function () {
        reject(req.error);
      };
      tx.oncomplete = function () {
        db.close();
      };
    });
  }

  async function lastCompletedReport() {
    const rows = await listReports();
    return (
      rows.find(function (row) {
        return row.status === "completed_local";
      }) || null
    );
  }

  async function addActivity(report, activity) {
    const current = await getReport(report.id);
    if (!current) throw new Error("Report not found");
    current.activities = (current.activities || []).concat([activity]);
    return putReport(current);
  }

  async function replaceActivity(reportId, activity) {
    const current = await getReport(reportId);
    if (!current) throw new Error("Report not found");
    current.activities = (current.activities || []).map(function (item) {
      return item.id === activity.id ? activity : item;
    });
    return putReport(current);
  }

  global.AtlasStore = {
    putReport: putReport,
    getReport: getReport,
    listReports: listReports,
    lastCompletedReport: lastCompletedReport,
    addActivity: addActivity,
    replaceActivity: replaceActivity,
  };
})(typeof window !== "undefined" ? window : globalThis);
