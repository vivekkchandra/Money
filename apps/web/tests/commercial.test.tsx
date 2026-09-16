import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { commercialProxy, accountHeaders, ACCOUNT_COOKIE, WORKSPACE_COOKIE } from "@/lib/commercial";
import { MarketingPage, MARKETING_PAGES } from "@/components/marketing";
import { AccountForm } from "@/components/account";
import { proxyBackend } from "@/lib/backend";
import { authConfigured, validSession } from "@/lib/auth";
import ErrorPage from "@/app/error";
import GlobalError from "@/app/global-error";
import { ResearchAllowances, TeamSettings } from "@/components/customer-tools";
const origin = "https://money.example.test";
const workspace = "d45ba8d8-6032-4b97-b515-4c85c63f416f";
const session = "a".repeat(64);
const serviceToken = "backend-secret-for-tests-never-client-side";

describe("separate account and workspace allowances", () => {
  it("labels calendar-month account usage separately and accepts legacy workspace-only responses", () => {
    const workspace = { remaining: 7, allowance: 10, period_end: "2026-10-12T00:00:00Z" };
    const html = renderToStaticMarkup(<ResearchAllowances summary={{ ...workspace, account_monthly_limit: 300, account_monthly_used: 42 }} />);
    expect(html).toContain("Workspace: <strong>7</strong> of 10");
    expect(html).toContain("Account-wide: <strong>258</strong> of 300");
    expect(html).toContain("current UTC calendar month");
    expect(html).toContain("separate from workspace billing periods");
    const legacy = renderToStaticMarkup(<ResearchAllowances summary={workspace} />);
    expect(legacy).toContain("Workspace: <strong>7</strong>");
    expect(legacy).not.toContain("Account-wide");
  });
});

const user = {
  id: "user-test",
  email: "customer@example.test",
  display_name: "Customer",
  email_verified: true
};

const workspaces = [{
  id: workspace,
  name: "My research",
  role: "OWNER"
}];

function request(method = "GET", body?: unknown, headers: Record<string, string> = {}, path = "/api/account/me") {
  return new Request(`${origin}${path}`, {
    method,

    headers: {
      Origin: origin,
      "Content-Type": "application/json",
      Cookie: `${ACCOUNT_COOKIE}=${session}; ${WORKSPACE_COOKIE}=${workspace}`,
      ...headers
    },

    ...(body === undefined ? {} : {
      body: JSON.stringify(body)
    })
  });
}

beforeEach(() => {
  vi.stubEnv("MONEY_ENV", "test");
  vi.stubEnv("MONEY_AUTH_MODE", "saas");
  vi.stubEnv("RESEARCH_API_URL", "https://api.example.test");
  vi.stubEnv("RESEARCH_API_TOKEN", serviceToken);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("commercial marketing and forms", () => {
  it("makes ownership succession explicit and requires fresh password confirmation", () => {
    const html = renderToStaticMarkup(<TeamSettings />);
    expect(html).toContain("Transfer workspace ownership");
    expect(html).toContain('autoComplete="current-password"');
    expect(html).toContain('type="checkbox" required=""');
    expect(html).toContain("Confirm ownership transfer");
  });

  it("proxies ownership transfer without exposing its password in the response", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ transferred: true }));
    vi.stubGlobal("fetch", fetcher);
    const response = await commercialProxy(request("POST", { user_id: workspace, password: "test-transfer-passphrase" }), "account", "workspaces/transfer-ownership");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ transferred: true });
    expect(String(fetcher.mock.calls[0][0])).toBe("https://api.example.test/v1/workspaces/transfer-ownership");
  });

  it("rejects cross-origin ownership transfer before reaching the API", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const response = await commercialProxy(request("POST", {}, { Origin: "https://other.example.test" }), "account", "workspaces/transfer-ownership");
    expect(response.status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([...MARKETING_PAGES])("renders public %s without a backend", page => {
    const html = renderToStaticMarkup(<MarketingPage page={page} />);
    expect(html).toContain("id=\"main\"");
    expect(html).toContain("Money");
    expect(html).toContain("/risk-disclosure");
    expect(html).not.toContain("NEXT_PUBLIC_RESEARCH_API_TOKEN");
  });

  it("keeps the homepage distinct from the authenticated dashboard", () => {
    const html = renderToStaticMarkup(<MarketingPage page="" />);
    expect(html).toContain("data-money-page=\"marketing\"");
    expect(html).toContain("See the evidence.");
    expect(html).toContain("No promised returns");
    expect(html).not.toContain("Workspace password");
  });

  it.each(["terms", "privacy", "acceptable-use"])("labels %s as requiring legal review", page => {
    expect(renderToStaticMarkup(<MarketingPage page={page} />)).toMatch(/review|approval/);
  });

  it("does not invent subscription prices", () => {
    const html = renderToStaticMarkup(<MarketingPage page="pricing" />);
    expect(html).toContain("No payment is taken");
    expect(html).not.toMatch(/£\d+/);
  });

  it("renders signup with password manager semantics and the backend password minimum", () => {
    const html = renderToStaticMarkup(<AccountForm page="signup" enabled />);
    expect(html).toContain("minLength=\"15\"");
    expect(html).toContain("autoComplete=\"new-password\"");
    expect(html).toContain("type=\"email\"");
    expect(html).toContain("informational research");
  });

  it("honestly disables customer signup outside SaaS mode", () => {
    const html = renderToStaticMarkup(<AccountForm page="signup" enabled={false} />);
    expect(html).toContain("not enabled");
    expect(html).not.toContain("<form");
  });

  it("renders a recovery screen without exposing exception messages or stacks", () => {
    const html = renderToStaticMarkup(<ErrorPage error={new Error(serviceToken)} reset={() => undefined} />);
    expect(html).toContain("Try again");
    expect(html).not.toContain(serviceToken);
    expect(renderToStaticMarkup(<GlobalError reset={() => undefined} />)).not.toContain("<script");
  });
});

describe("server-only account bridge", () => {
  it("puts only the opaque backend token in a secure HttpOnly cookie, never JSON", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      session_token: session,
      expires_at: new Date(Date.now() + 3600000).toISOString(),

      user: {
        ...user,
        password_hash: "private-password-hash"
      },

      workspaces,
      api_key: serviceToken
    }));

    vi.stubGlobal("fetch", fetch);

    const response = await commercialProxy(request("POST", {
      email: user.email,
      password: "customer-passphrase"
    }), "account", "login");

    expect(response.status).toBe(200);
    const content = await response.text();
    expect(content).not.toContain(session);
    expect(content).not.toContain(serviceToken);
    expect(content).not.toContain("password_hash");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
    expect(response.headers.get("set-cookie")).toContain("SameSite=Strict");
    expect(response.headers.get("set-cookie")).toContain("Secure");
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(String(fetch.mock.calls[0][0])).toBe("https://api.example.test/v1/account/login");
  });

  it("refuses malformed or expired sessions returned by the backend", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      session_token: "bad;token",
      expires_at: "2020-01-01",
      user,
      workspaces
    })));

    const response = await commercialProxy(request("POST", {}), "account", "login");
    expect(response.status).toBe(503);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("validates selected workspace membership before setting a cookie", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      user,
      workspaces
    })));

    const response = await commercialProxy(request("POST", {
      workspace_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    }), "account", "select-workspace");

    expect(response.status).toBe(403);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("allows a verified member to select their workspace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      user,
      workspaces
    })));

    const response = await commercialProxy(request("POST", {
      workspace_id: workspace
    }), "account", "select-workspace");

    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain(`${WORKSPACE_COOKIE}=${workspace}`);
  });

  it(
    "forwards only server session/workspace cookies, ignoring spoofed browser authority headers",
    () => {
      expect(accountHeaders(request("GET", undefined, {
        "X-Money-Workspace": "evil",
        "X-Money-Session": "evil"
      }))).toEqual({
        "X-Money-Workspace": workspace,
        "X-Money-Session": session
      });
    }
  );

  it.each(["signup", "login", "reset-password", "select-workspace"])("blocks cross-origin %s before any backend request", async path => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    expect((await commercialProxy(request("POST", {}, {
      Origin: "https://evil.example"
    }), "account", path)).status).toBe(403);

    expect(fetch).not.toHaveBeenCalled();
  });

  it("blocks cross-origin product writes and unauthenticated private reads", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    expect((await commercialProxy(request("PUT", {}, {
      Origin: "https://evil.example"
    }), "product", "preferences")).status).toBe(403);

    expect((await commercialProxy(request("GET", undefined, {
      Cookie: ""
    }), "product", "summary")).status).toBe(401);

    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects oversized requests before contacting the backend", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    expect((await commercialProxy(request("POST", {
      email: "a".repeat(9000)
    }), "account", "signup")).status).toBe(400);

    expect(fetch).not.toHaveBeenCalled();
  });

  it("sanitizes provider failures and preserves quota errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      detail: {
        code: "QUOTA_EXHAUSTED",
        message: serviceToken
      }
    }, {
      status: 402
    })));

    const response = await commercialProxy(request("GET"), "product", "summary");
    expect(response.status).toBe(402);
    expect(await response.text()).not.toContain(serviceToken);
  });

  it("only sends history's allowlisted bounded query parameters", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      jobs: [],
      next_offset: null
    }));

    vi.stubGlobal("fetch", fetch);

    await commercialProxy(
      request("GET", undefined, {}, "/api/product/history?query=ABC.L&limit=25&created_from=2026-09-01&created_to=2026-09-16&sort=oldest&workspace_id=evil&command=run"),
      "product",
      "history"
    );

    const url = new URL(String(fetch.mock.calls[0][0]));
    expect(url.pathname).toBe("/v1/product/history");
    expect(url.searchParams.get("query")).toBe("ABC.L");
    expect(url.searchParams.get("created_from")).toBe("2026-09-01");
    expect(url.searchParams.get("created_to")).toBe("2026-09-16");
    expect(url.searchParams.get("sort")).toBe("oldest");
    expect(url.searchParams.has("workspace_id")).toBe(false);
    expect(url.searchParams.has("command")).toBe(false);
  });

  it.each(["https://evil.example/payment", "javascript:alert(1)", "https://checkout.stripe.com.evil.test/pay"])("rejects unapproved billing redirect %s", async url => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      url
    })));

    expect((await commercialProxy(request("POST", {}), "product", "billing/checkout")).status).toBe(503);
  });

  it("clears both cookies only after durable logout succeeds", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      signed_out: true
    })));

    const response = await commercialProxy(request("POST", {}), "account", "logout");
    expect(response.status).toBe(200);
    expect(response.headers.getSetCookie()).toHaveLength(2);
    expect(response.headers.getSetCookie().every(value => value.includes("Max-Age=0"))).toBe(true);
  });

  it("accepts content-addressed notification IDs, not arbitrary paths", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      ok: true
    }));

    vi.stubGlobal("fetch", fetch);

    expect(
      (await commercialProxy(request("POST", {}), "product", `notifications/${"b".repeat(64)}/read`)).status
    ).toBe(200);

    expect((await commercialProxy(request("POST", {}), "product", "notifications/../admin/read")).status).toBe(404);
  });

  it("does not accept the private password/session mechanism in SaaS mode", () => {
    expect(authConfigured()).toBe(true);
    expect(validSession("anything")).toBe(false);
  });

  it("preserves durable research enqueue with backend-validated tenant headers", async () => {
    const fetch = vi.fn(async (url: URL) => String(url).endsWith("/v1/account/me") ? Response.json({
      user,
      workspaces
    }) : Response.json({
      id: "job",
      status: "QUEUED"
    }, {
      status: 202
    }));

    vi.stubGlobal("fetch", fetch);

    const response = await proxyBackend(request("POST", {
      ticker: "DEMO.L"
    }, {
      "Idempotency-Key": "research-request-1"
    }), "/research/jobs");

    expect(response.status).toBe(202);
    const call = vi.mocked(global.fetch).mock.calls.find(([url]) => String(url).endsWith("/research/jobs"));

    expect(call?.[1]?.headers).toMatchObject({
      "X-Money-Session": session,
      "X-Money-Workspace": workspace,
      Authorization: `Bearer ${serviceToken}`
    });
  });
});
