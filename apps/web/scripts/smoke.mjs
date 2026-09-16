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
  const csp = page.headers.get("content-security-policy");
  assert.ok(csp.includes("'nonce-"));
  assert.ok(!csp.includes("'unsafe-inline'"));
  assert.ok(!csp.includes("'unsafe-eval'"));
  const nonce = csp.match(/'nonce-([^']+)'/)[1];
  assert.ok(html.includes(`nonce="${nonce}"`), "Next hydration scripts must use the request nonce");
  const secondPage = await fetch(`${origin}/research/${queued.id}`, { headers });
  assert.notEqual(secondPage.headers.get("content-security-policy"), csp, "CSP nonce must rotate per request");
  const logout = await fetch(`${origin}/api/session`, { method: "DELETE", headers });
  assert.equal(logout.status, 200);
  assert.equal((await fetch(`${origin}/api/research/${queued.id}`, { headers })).status, 401, "Revoked cookies must not be reusable");
  if (process.argv.includes("--browser")) {
    const { chromium } = await import("@playwright/test");
    const browser = await chromium.launch();
    try {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
      const browserPage = await context.newPage();
      const errors = [];
      browserPage.on("pageerror", (error) => errors.push(error.message));
      browserPage.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
      await browserPage.goto(origin);
      await browserPage.getByLabel("Workspace password").fill(password);
      await browserPage.getByRole("button", { name: "Open workspace" }).click();
      await browserPage.getByRole("link", { name: "Research jobs", exact: true }).click();
      await browserPage.getByLabel("Stock ticker").fill("DEMO.L");
      const createdResponse = browserPage.waitForResponse((response) => response.url() === `${origin}/api/research` && response.request().method() === "POST");
      await browserPage.getByRole("button", { name: "Request research" }).click();
      const browserCreated = await createdResponse;
      assert.equal(browserCreated.status(), 202);
      const browserJob = await browserCreated.json();
      await browserPage.getByRole("link", { name: "Follow DEMO.L" }).click();
      await run(resolve(root, ".venv/bin/python"), ["-m", "money.worker", "--once"], root).done;
      await browserPage.getByText("First-pass reports are locked", { exact: true }).waitFor({ timeout: 15000 });
      await browserPage.getByRole("tab", { name: "Evidence & sources" }).click();
      await browserPage.getByText("Research snapshot & evidence", { exact: true }).waitFor();
      await browserPage.reload();
      await browserPage.getByText(browserJob.id, { exact: true }).waitFor();
      await browserPage.getByText("First-pass reports are locked", { exact: true }).waitFor();
      const bodyWidth = await browserPage.evaluate(() => document.body.scrollWidth);
      assert.ok(bodyWidth <= 1440, "Desktop must not overflow horizontally");
      await browserPage.setViewportSize({ width: 390, height: 844 });
      assert.ok(await browserPage.evaluate(() => document.body.scrollWidth) <= 390, "Mobile must not overflow horizontally");
      for (const path of ["universe", "watch", "rejected", "evidence", "performance", "settings", "health"]) {
        await browserPage.goto(`${origin}/${path}`);
        await browserPage.getByRole("button", { name: "Sign out" }).waitFor();
      }
      await browserPage.getByRole("button", { name: "Sign out" }).click();
      await browserPage.getByLabel("Workspace password").waitFor();
      assert.deepEqual(errors, [], "Browser must have no console, hydration or CSP errors");
      console.log("Browser smoke passed: login → enqueue 202 → separate worker → locked reports → evidence → reload → desktop/mobile layout → logout; no console errors.");
    } finally { await browser.close(); }
  }
  console.log("HTTP smoke passed: authenticated enqueue 202 → separate worker → COMPLETE / INSUFFICIENT_EVIDENCE; 3 locked fixture reports, evidence and web page retrieved by research ID.");
} finally {
  for (const child of children) if (child.exitCode === null) child.kill("SIGTERM");
  await Promise.allSettled(children.map((child) => child.done));
  rmSync(temporary, { recursive: true, force: true });
}
