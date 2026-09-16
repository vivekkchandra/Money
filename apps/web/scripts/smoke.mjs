// Real HTTP integration across Next, the Python API, an isolated durable database
// and a separate worker. Uses only synthetic DEMO.L research and ephemeral keys.
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import assert from "node:assert/strict";

const web = fileURLToPath(new URL("..", import.meta.url));
const root = resolve(web, "../..");
const temporary = mkdtempSync(resolve(tmpdir(), "money-web-smoke-"));
const children = [];
const secret = () => randomBytes(32).toString("hex");
const token = secret();
const password = secret();
const env = { ...process.env, MONEY_ENV: "test", MONEY_RESEARCH_MODE: "demo", DATABASE_URL: `sqlite:///${temporary}/research.db`, RESEARCH_API_TOKEN: token, MONEY_WEB_PASSWORD: password, SESSION_SECRET: secret(), NEXT_TELEMETRY_DISABLED: "1" };

function run(command, args, cwd, overrides = {}) {
  const child = spawn(command, args, { cwd, env: { ...env, ...overrides }, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  child.stdout.on("data", (chunk) => { output = (output + chunk).slice(-10000); });
  child.stderr.on("data", (chunk) => { output = (output + chunk).slice(-10000); });
  child.diagnostics = () => output;
  child.done = new Promise((resolveDone, reject) => { child.on("error", reject); child.on("exit", (code) => code === 0 ? resolveDone() : reject(new Error(`Process exited ${code}: ${output}`))); });
  // Long-running services are intentionally terminated in finally.
  child.done.catch(() => {});
  children.push(child);
  return child;
}

async function port() {
  const server = createServer();
  await new Promise((resolveListen, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolveListen); });
  const value = server.address().port;
  await new Promise((resolveClose) => server.close(resolveClose));
  return value;
}

async function waitFor(url, child) {
  for (let i = 0; i < 80; i++) {
    if (child.exitCode !== null) throw new Error(child.diagnostics());
    try { const response = await fetch(url); if (response.status < 500 || response.status === 503) return; } catch { /* service booting */ }
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  }
  throw new Error(`Service did not start: ${child.diagnostics()}`);
}

try {
  const backendPort = await port();
  const webPort = await port();
  const origin = `http://127.0.0.1:${webPort}`;
  const backend = `http://127.0.0.1:${backendPort}`;
  await run(resolve(root, ".venv/bin/alembic"), ["upgrade", "head"], root).done;
  const api = run(resolve(root, ".venv/bin/uvicorn"), ["money.api.app:app", "--host", "127.0.0.1", "--port", String(backendPort)], root);
  const app = run(process.execPath, [resolve(web, "node_modules/next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", String(webPort)], web, { RESEARCH_API_URL: backend });
  await Promise.all([waitFor(`${backend}/health`, api), waitFor(`${origin}/api/session`, app)]);
  const signedOut = await fetch(`${origin}/api/research`);
  assert.equal(signedOut.status, 401);
  const login = await fetch(`${origin}/api/session`, { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
  assert.equal(login.status, 200);
  const cookie = login.headers.get("set-cookie").split(";")[0];
  const headers = { Cookie: cookie, Origin: origin, "Content-Type": "application/json" };
  const created = await fetch(`${origin}/api/research`, { method: "POST", headers, body: JSON.stringify({ ticker: "DEMO.L" }) });
  assert.equal(created.status, 202);
  const queued = await created.json();
  assert.equal(queued.status, "QUEUED");
  await run(resolve(root, ".venv/bin/python"), ["-m", "money.worker", "--once"], root).done;
  const complete = await (await fetch(`${origin}/api/research/${queued.id}`, { headers })).json();
  assert.equal(complete.status, "COMPLETE", JSON.stringify(complete));
  assert.equal(complete.packet.final_state, "INSUFFICIENT_EVIDENCE");
  assert.equal(complete.packet.runtime, "demo");
  assert.equal(complete.packet.signal, null);
  const reports = await (await fetch(`${origin}/api/research/${queued.id}/reports`, { headers })).json();
  assert.equal(reports.locked, true);
  assert.deepEqual(Object.keys(reports.reports).sort(), ["ai_hedge_fund", "qlib", "tradingagents"]);
  const evidence = await (await fetch(`${origin}/api/research/${queued.id}/evidence`, { headers })).json();
  assert.ok(evidence.evidence.length > 0);
  const page = await fetch(`${origin}/research/${queued.id}`, { headers });
  assert.equal(page.status, 200);
  const html = await page.text();
  for (const credential of [password, token, env.SESSION_SECRET]) assert.ok(!html.includes(credential));
  console.log("HTTP smoke passed: authenticated enqueue 202 → separate worker → COMPLETE / INSUFFICIENT_EVIDENCE; 3 locked fixture reports, evidence and web page retrieved by research ID.");
} finally {
  for (const child of children) if (child.exitCode === null) child.kill("SIGTERM");
  await Promise.allSettled(children.map((child) => child.done));
  rmSync(temporary, { recursive: true, force: true });
}
