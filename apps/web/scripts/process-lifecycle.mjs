// Bounded supervision for smoke-owned children only. No process-name discovery.
const boundedDelay = (milliseconds) => {
  if (!Number.isInteger(milliseconds) || milliseconds < 1 || milliseconds > 120000) throw new Error("Invalid child deadline");
  return milliseconds;
};

/** @returns {string} */
const emptyDiagnostics = () => "";

export function trackChild(child, { label = "Child process", diagnostics = emptyDiagnostics, sendSignal = (signal) => child.kill(signal), requireCleanExit = false } = {}) {
  let exited = child.exitCode !== null || child.signalCode !== null;
  let cleanExit = child.exitCode === 0;
  let resolveExit;
  const exit = new Promise((resolve) => { resolveExit = resolve; });
  if (exited) resolveExit();
  const done = new Promise((resolve, reject) => {
    child.once("error", (error) => {
      // A spawn failure has no process to terminate. Other child errors do not
      // establish that its process has stopped.
      if (child.pid === undefined) { exited = true; cleanExit = true; resolveExit(); }
      reject(new Error(`${label} could not run (${error.code ?? "PROCESS_ERROR"}): ${diagnostics()}`));
    });
    child.once("exit", (code, signal) => {
      exited = true;
      cleanExit = code === 0;
      resolveExit();
      if (code === 0) resolve();
      else reject(new Error(`${label} exited ${signal ?? code}: ${diagnostics()}`));
    });
  });
  // Services remain alive until cleanup, and can exit before their waiter runs.
  done.catch(() => {});
  return { child, label, diagnostics, done, exit, exited: () => exited, cleanupSafe: () => exited && (!requireCleanExit || cleanExit), sendSignal };
}

async function exitedWithin(process, milliseconds) {
  if (process.exited()) return true;
  let timer;
  try {
    return await Promise.race([
      process.exit.then(() => true),
      new Promise((resolve) => { timer = setTimeout(() => resolve(false), boundedDelay(milliseconds)); }),
    ]);
  } finally { clearTimeout(timer); }
}

export async function stopChild(process, { graceMs = 30000, killWaitMs = 5000 } = {}) {
  boundedDelay(graceMs); boundedDelay(killWaitMs);
  if (process.exited()) return process.cleanupSafe();
  try { process.sendSignal("SIGTERM"); } catch { /* Only an exit event confirms exit. */ }
  if (await exitedWithin(process, graceMs)) return process.cleanupSafe();
  try { process.sendSignal("SIGKILL"); } catch { /* Retain owned data if exit cannot be confirmed. */ }
  return await exitedWithin(process, killWaitMs) && process.cleanupSafe();
}

export async function waitForChild(process, { timeoutMs = 90000, graceMs = 30000, killWaitMs = 5000 } = {}) {
  boundedDelay(timeoutMs);
  let timer;
  const timeout = Symbol("child timeout");
  try {
    const result = await Promise.race([
      process.done,
      new Promise((resolve) => { timer = setTimeout(() => resolve(timeout), timeoutMs); }),
    ]);
    if (result !== timeout) return;
    const stopped = await stopChild(process, { graceMs, killWaitMs });
    throw new Error(`${process.label} exceeded ${timeoutMs} ms; exit ${stopped ? "confirmed" : "unconfirmed"}: ${process.diagnostics()}`);
  } finally { clearTimeout(timer); }
}
