/**
 * Configurable Atlas user and machine directories.
 * Identities are data. Application logic must not hard-code people or fleet IDs.
 */
(function (global) {
  const R = global.AtlasRecords;

  function norm(value) {
    return R.subjectKey(value);
  }

  function machineTokens(machine) {
    const tokens = [machine.id, machine.canonical_id, machine.display_name];
    (machine.aliases || []).forEach(function (alias) {
      tokens.push(alias);
    });
    return tokens.filter(Boolean);
  }

  function resolveMachine(entered, machines) {
    const key = norm(entered);
    if (!key) return null;
    const list = machines || [];
    for (let i = 0; i < list.length; i += 1) {
      const machine = list[i];
      const hit = machineTokens(machine).some(function (token) {
        return norm(token) === key;
      });
      if (hit) return machine;
    }
    return null;
  }

  function pickerMachines(machines) {
    return (machines || []).filter(function (machine) {
      return machine.status === "active" && machine.available_in_picker !== false;
    });
  }

  function machineLabel(machine) {
    return machine.display_name || machine.canonical_id || machine.id;
  }

  function reportAuthors(users) {
    return (users || []).filter(function (user) {
      return user.status === "active" && user.can_create_supervisor_reports === true;
    });
  }

  function resolveUser(id, users) {
    return (users || []).find(function (user) {
      return user.id === id;
    }) || null;
  }

  function userLabel(user) {
    return user ? user.display_name || user.id : "";
  }

  global.AtlasDirectory = {
    resolveMachine: resolveMachine,
    pickerMachines: pickerMachines,
    machineLabel: machineLabel,
    reportAuthors: reportAuthors,
    resolveUser: resolveUser,
    userLabel: userLabel,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = global.AtlasDirectory;
  }
})(typeof window !== "undefined" ? window : globalThis);
