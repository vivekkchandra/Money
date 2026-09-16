// Isolated real SaaS acceptance. Synthetic research only; never targets production.
// Email verification is inspected in the encrypted test outbox, not fabricated.
import { randomBytes } from "node:crypto";

import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import assert from "node:assert/strict";
import { stopChild, trackChild, waitForChild } from "./process-lifecycle.mjs";
import { checkDeployment } from "./check-deployment.mjs";
const web = fileURLToPath(new URL("..", import.meta.url));
const root = resolve(web, "../..");
const temporary = mkdtempSync(resolve(tmpdir(), "money-commercial-smoke-"));
const children = [];
const secret = () => randomBytes(32).toString("hex");
const password = secret();
const token = secret();

const env = {
  ...process.env,
  MONEY_ENV: "test",
  MONEY_AUTH_MODE: "saas",
  MONEY_RESEARCH_MODE: "unconfigured",
  MONEY_ENABLE_SYNTHETIC_DEMO: "true",
  MONEY_JOB_TIMEOUT_SECONDS: "30",
  DATABASE_URL: `sqlite:///${temporary}/research.db`,
  RESEARCH_API_TOKEN: token,
  MONEY_EMAIL_ENCRYPTION_KEY: `${randomBytes(32).toString("base64url")}=`,
  NEXT_TELEMETRY_DISABLED: "1"
};

const fetch = (url, options = {}) => globalThis.fetch(url, {
  ...options,
  signal: options.signal ?? AbortSignal.timeout(10000),
  redirect: "error"
});

let stage = "allocate local listeners";
let failed = false;
const redact = value => [password, token, env.MONEY_EMAIL_ENCRYPTION_KEY].reduce((text, item) => text.replaceAll(item, "[redacted]"), String(value));

function run(command, args, cwd = root, overrides = {}) {
  const child = spawn(command, args, {
    cwd,

    env: {
      ...env,
      ...overrides
    },

    stdio: ["ignore", "pipe", "pipe"]
  });

  let output = "";

  child.stdout.on("data", chunk => {
    output = (output + chunk).slice(-8000);
  });

  child.stderr.on("data", chunk => {
    output = (output + chunk).slice(-8000);
  });

  const tracked = trackChild(child, {
    label: stage,
    diagnostics: () => redact(output),
    requireCleanExit: args.includes("money.worker")
  });

  children.push(tracked);
  return tracked;
}

async function port() {
  const listener = createServer();

  await new Promise((yes, no) => {
    listener.once("error", no);
    listener.listen(0, "127.0.0.1", yes);
  });

  const number = listener.address().port;
  await new Promise(yes => listener.close(yes));
  return number;
}

async function ready(url, process) {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (process.exited())
      throw new Error(process.diagnostics());

    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(1000)
      });

      if ([200, 401, 503].includes(response.status))
        return;
    } catch {}

    await new Promise(yes => setTimeout(yes, 250));
  }

  throw new Error("Local service did not start");
}

async function emailToken(email, kind = "verify") {
  const program = `import os,json\nfrom cryptography.fernet import Fernet\nfrom sqlalchemy import select\nfrom money.accounts.models import email_outbox,users\nfrom money.storage import ResearchStore\ns=ResearchStore(os.environ['DATABASE_URL'],allow_sqlite=True)\nwith s.engine.connect() as c:\n p=c.scalar(select(email_outbox.c.encrypted_payload).join(users).where(users.c.email==os.environ['MONEY_TEST_EMAIL'],email_outbox.c.kind==os.environ['MONEY_TEST_EMAIL_KIND']).order_by(email_outbox.c.created_at.desc()).limit(1))\nd=json.loads(Fernet(os.environ['MONEY_EMAIL_ENCRYPTION_KEY'].encode()).decrypt(p.encode()))\nprint(d['body'].split('#token=',1)[1].split()[0])\ns.engine.dispose()`;

  const result = await promisify(execFile)(resolve(root, ".venv/bin/python"), ["-c", program], {
    cwd: root,

    env: {
      ...env,
      MONEY_TEST_EMAIL: email,
      MONEY_TEST_EMAIL_KIND: kind
    },

    timeout: 10000,
    maxBuffer: 8192
  });

  const value = result.stdout.trim();
  assert.match(value, /^[A-Za-z0-9_-]{32,256}$/);
  return value;
}

try {
  const apiPort = await port();
  const webPort = await port();
  const origin = `http://127.0.0.1:${webPort}`;
  const backend = `http://127.0.0.1:${apiPort}`;
  env.MONEY_PUBLIC_WEB_URL = origin;
  stage = "migrate isolated account/research database";

  await waitForChild(run(resolve(root, ".venv/bin/alembic"), ["upgrade", "head"]), {
    timeoutMs: 60000
  });

  stage = "start API and Next";

  const api = run(
    resolve(root, ".venv/bin/uvicorn"),
    ["money.api.app:app", "--host", "127.0.0.1", "--port", String(apiPort)]
  );

  const app = run(process.execPath, [
    resolve(web, "node_modules/next/dist/bin/next"),
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(webPort)
  ], web, {
    RESEARCH_API_URL: backend
  });

  await Promise.all([ready(`${backend}/health`, api), ready(`${origin}/api/session`, app)]);
  await checkDeployment(origin);

  async function post(path, body, cookie = "") {
    return fetch(`${origin}${path}`, {
      method: "POST",

      headers: {
        "Content-Type": "application/json",
        Origin: origin,
        Cookie: cookie
      },

      body: JSON.stringify(body)
    });
  }

  const email = "http-customer@example.test";
  stage = "signup, encrypted verification outbox and login";

  assert.equal((await post("/api/account/signup", {
    email,
    password,
    display_name: "HTTP acceptance customer"
  })).status, 202);

  assert.equal((await post("/api/account/verify-email", {
    token: await emailToken(email)
  })).status, 200);

  const login = await post("/api/account/login", {
    email,
    password
  });

  assert.equal(login.status, 200);
  assert.ok(!(await login.clone().text()).includes("session_token"));
  let cookie = login.headers.getSetCookie().map(value => value.split(";")[0]).join("; ");
  stage = "workspace creation and membership selection";

  const created = await post("/api/account/workspaces", {
    name: "Acceptance research"
  }, cookie);

  assert.equal(created.status, 201);
  const workspace = (await created.json()).workspace;

  const selected = await post("/api/account/select-workspace", {
    workspace_id: workspace.id
  }, cookie);

  assert.equal(selected.status, 200);
  cookie = `${cookie.split(";").map(item => item.trim()).filter(item => !item.startsWith("money_workspace=")).join("; ")}; ${selected.headers.getSetCookie()[0].split(";")[0]}`;
  stage = "entitlement and durable research";

  const summary = await (await fetch(`${origin}/api/product/summary`, {
    headers: {
      Cookie: cookie
    }
  })).json();

  assert.equal(summary.plan, "FREE");

  const queued = await post("/api/research", {
    ticker: "DEMO.L"
  }, cookie);

  assert.equal(queued.status, 202);
  const job = await queued.json();
  stage = "separate worker";
  await waitForChild(run(resolve(root, ".venv/bin/python"), ["-m", "money.worker", "--once"]));
  stage = "immutable result and usage";

  const result = await (await fetch(`${origin}/api/research/${job.id}`, {
    headers: {
      Cookie: cookie
    }
  })).json();

  assert.equal(result.status, "COMPLETE");
  assert.equal(result.packet.runtime, "demo");
  assert.equal(result.packet.signal, null);

  const usage = await (await fetch(`${origin}/api/product/summary`, {
    headers: {
      Cookie: cookie
    }
  })).json();

  assert.equal(usage.used, summary.used + 1);
  assert.equal((await fetch(`${origin}/api/research/${job.id}`)).status, 401);
  assert.equal((await post("/api/account/logout", {}, cookie)).status, 200);

  assert.equal((await fetch(`${origin}/api/research/${job.id}`, {
    headers: {
      Cookie: cookie
    }
  })).status, 401);

  if (process.argv.includes("--browser")) {
    stage = "browser customer journey";

    const {
      chromium
    } = await import("@playwright/test");

    const browser = await chromium.launch();

    try {
      const context = await browser.newContext({
        viewport: {
          width: 1440,
          height: 1000
        }
      });

      context.setDefaultTimeout(20000);
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));

      page.on("console", message => {
        if (message.type() === "error")
          errors.push(message.text());
      });

      const browserEmail = "browser-customer@example.test";
      await page.goto(`${origin}/signup`);
      await page.getByLabel("Your name").fill("Browser customer");

      await page.getByLabel("Email address", {
        exact: true
      }).fill(browserEmail);

      await page.getByLabel("Password", {
        exact: true
      }).fill(password);

      await page.getByRole("checkbox").check();

      await page.getByRole("button", {
        name: "Create account",
        exact: true
      }).click();

      await page.getByText("Check your email to verify your account before signing in.").waitFor();
      await page.goto(`${origin}/verify-email#token=${await emailToken(browserEmail)}`);

      await page.getByRole("button", {
        name: "Verify email",
        exact: true
      }).click();

      await page.getByText("Your email is verified. You can now sign in.").waitFor();
      await page.goto(`${origin}/login`);

      await page.getByLabel("Email address", {
        exact: true
      }).fill(browserEmail);

      await page.getByLabel("Password", {
        exact: true
      }).fill(password);

      await page.getByRole("button", {
        name: "Sign in",
        exact: true
      }).click();

      await page.waitForURL("**/onboarding");
      await page.getByLabel("Workspace name").fill("Browser research");

      await page.getByRole("button", {
        name: "Create workspace & choose a plan"
      }).click();

      await page.waitForURL("**/billing");

      await page.getByRole("heading", {
        name: "Your subscription"
      }).waitFor();

      await page.goto(`${origin}/jobs`);
      await page.getByLabel("Stock ticker").fill("DEMO.L");
      const pending = page.waitForResponse(response => response.url() === `${origin}/api/research` && response.request().method() === "POST");

      await page.getByRole("button", {
        name: "Request research",
        exact: true
      }).click();

      const response = await pending;
      assert.equal(response.status(), 202);
      const research = await response.json();

      await page.getByRole("link", {
        name: "Follow DEMO.L"
      }).click();

      await waitForChild(run(resolve(root, ".venv/bin/python"), ["-m", "money.worker", "--once"]));

      await page.getByText("First-pass reports are locked", {
        exact: true
      }).waitFor();

      await page.getByRole("tab", {
        name: "Evidence & sources"
      }).click();

      await page.getByText("Research snapshot & evidence", {
        exact: true
      }).waitFor();

      await page.getByRole("tab", {
        name: "Cross-examination",
        exact: true
      }).click();

      await page.getByRole("heading", {
        name: "Cross-examination",
        exact: true
      }).waitFor();

      await page.reload();

      await page.getByText(research.id, {
        exact: true
      }).waitFor();

      await page.getByRole("button", {
        name: "Sign out",
        exact: true
      }).click();

      await page.goto(`${origin}/login`);

      await page.getByLabel("Email address", {
        exact: true
      }).fill(browserEmail);

      await page.getByLabel("Password", {
        exact: true
      }).fill(password);

      await page.getByRole("button", {
        name: "Sign in",
        exact: true
      }).click();

      await page.waitForURL("**/dashboard");
      await page.goto(`${origin}/research/${research.id}`);

      await page.getByText("First-pass reports are locked", {
        exact: true
      }).waitFor();

      await page.setViewportSize({
        width: 390,
        height: 844
      });

      assert.ok((await page.evaluate(() => document.body.scrollWidth)) <= 390);
      assert.deepEqual(errors, []);

      console.log(
        "VERIFIED browser SaaS: signup → outbox verification → login → workspace → FREE plan → DEMO.L 202 → separate worker → sealed evidence/challenge → refresh → logout/login → same record. Email delivery and live research NOT qualified."
      );
    } finally {
      await browser.close();
    }
  }

  console.log(
    "VERIFIED HTTP SaaS: actual signup, verification, sessions, workspace, entitlement, durable DEMO.L worker, immutable result, usage and logout. Isolated SQLite test substitute; not production PostgreSQL or delivered email."
  );
} catch (error) {
  failed = true;
  console.error(`FAILED commercial acceptance at ${stage}: ${redact(error.message)}`);
  process.exitCode = 1;
} finally {
  let clean = true;

  for (const child of [...children].reverse())
    clean = (await stopChild(child)) && clean;

  if (clean) rmSync(temporary, {
    recursive: true
  });
  else {
    failed = true;
    process.exitCode = 1;
    console.error("Cleanup not confirmed; isolated test data retained.");
  }

  if (failed)
    console.error("Commercial acceptance NOT VERIFIED.");
}
