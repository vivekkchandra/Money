import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authConfigured, createSession, passwordMatches, readLimitedJson, SESSION_COOKIE, validSession } from "@/lib/auth";
import { proxyBackend } from "@/lib/backend";
import { GET, POST, DELETE } from "@/app/api/session/route";
import { GET as researchGET } from "@/app/api/research/[[...path]]/route";
import { systemFixture } from "./fixtures/system";

const password = "a-test-password-at-least-sixteen";
const secret = "test-session-secret-at-least-thirty-two-characters";
const token = "test-backend-token-never-in-browser";

beforeEach(() => {
  vi.stubEnv("MONEY_ENV", "test");
  vi.stubEnv("MONEY_WEB_PASSWORD", password);
  vi.stubEnv("SESSION_SECRET", secret);
  vi.stubEnv("RESEARCH_API_URL", "https://compute.example.test");
  vi.stubEnv("RESEARCH_API_TOKEN", token);
  mockService();
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function request(path = "/api/research", init: RequestInit = {}) {
  return new Request(`https://money.example.test${path}`, { ...init, headers: { cookie: `${SESSION_COOKIE}=${createSession()}`, origin: "https://money.example.test", "content-type": "application/json", ...init.headers } });
}

function mockService(result: () => Promise<Response> = async () => Response.json({})) {
  const fetch = vi.fn(async (url: URL | string) => {
    const path = new URL(url).pathname;
    if (path === "/internal/auth/rate-limit") return Response.json({ allowed: true, retry_after: 0 });
    if (path === "/internal/auth/sessions/validate") return Response.json({ valid: true });
    if (path === "/internal/auth/sessions/revoke") return Response.json({ revoked: true });
    if (path === "/internal/auth/sessions") return Response.json({ created: true });
    return result();
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("workspace authentication", () => {
  it("reports the actual allowlisted backend authentication mode", async () => {
    for (const [auth_mode, expected] of [["saas", "saas"], ["private", "private"], ["provider-secret", "unknown"]]) {
      mockService(async () => Response.json({ status: "ok", auth_mode }));
      const result = await proxyBackend(request("/api/health"), "/health/ready");
      expect((await result.json()).auth_mode).toBe(expected);
    }
  });
  it("fails closed without strong configured secrets", async () => {
    vi.stubEnv("MONEY_WEB_PASSWORD", "");
    expect(authConfigured()).toBe(false);
    expect(validSession("anything")).toBe(false);
    expect((await proxyBackend(new Request("https://money.example.test/api/research"), "/research/jobs")).status).toBe(503);
  });
  it("rejects unsigned, tampered, expired and password-rotated sessions", () => {
    const now = 1789556400000;
    const session = createSession(now);
    expect(validSession(session, now)).toBe(true);
    expect(createSession(now)).not.toBe(session);
    expect(validSession(`${session.slice(0, -1)}!`, now)).toBe(false);
    expect(validSession(session, now + 8 * 60 * 60 * 1000)).toBe(false);
    expect(validSession("9999999999.fake", now)).toBe(false);
    vi.stubEnv("MONEY_WEB_PASSWORD", `${password}-rotated`);
    expect(validSession(session, now)).toBe(false);
  });
  it("compares passwords and keeps session responses free of credentials", async () => {
    expect(passwordMatches(password)).toBe(true);
    expect(passwordMatches("wrong")).toBe(false);
    const response = await POST(request("/api/session", { method: "POST", body: JSON.stringify({ password }) }));
    expect(response.status).toBe(200);
    const cookie = response.headers.get("set-cookie");
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Strict");
    expect(cookie).toContain("Secure");
    expect(cookie).not.toContain(password);
    expect(await response.json()).toEqual({ authenticated: true });
    const state = await GET(request("/api/session"));
    expect(await state.json()).toEqual({ configured: true, authenticated: true });
  });
  it("rejects cross-origin login and logout", async () => {
    const evil = request("/api/session", { method: "POST", headers: { origin: "https://other.example.test" }, body: JSON.stringify({ password }) });
    expect((await POST(evil)).status).toBe(403);
    expect((await DELETE(evil)).status).toBe(403);
  });
  it("limits streamed request bodies even without Content-Length", async () => {
    const body = JSON.stringify({ password: "x".repeat(2000) });
    await expect(readLimitedJson(new Request("https://money.example.test", { method: "POST", headers: { "Content-Type": "application/json" }, body }), 1024)).rejects.toThrow("Request too large");
  });
  it("requires durable revocation and rejects a revoked cookie on reuse", async () => {
    const fetch = mockService();
    const logout = await DELETE(request("/api/session", { method: "DELETE" }));
    expect(logout.status).toBe(200);
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/sessions/revoke"))).toBe(true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ valid: false })));
    expect((await proxyBackend(request(), "/research/jobs")).status).toBe(401);
  });
  it("fails closed when the durable limiter or session registry is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private service error")));
    const response = await POST(request("/api/session", { method: "POST", body: JSON.stringify({ password }) }));
    expect(response.status).toBe(503);
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(await response.text()).not.toContain("private service error");
  });
  it("honours a durable rate limit before checking the password", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ allowed: false, retry_after: 42 })));
    const response = await POST(request("/api/session", { method: "POST", body: JSON.stringify({ password }) }));
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("42");
  });
  it("rejects unknown login fields and unsafe production configuration", async () => {
    expect((await POST(request("/api/session", { method: "POST", body: JSON.stringify({ password, workspace: "other" }) }))).status).toBe(400);
    vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("MONEY_ENV", "");
    expect(authConfigured()).toBe(false);
    vi.stubEnv("MONEY_ENV", "production"); vi.stubEnv("RESEARCH_API_URL", "http://localhost:8000");
    expect((await proxyBackend(request(), "/research/jobs")).status).toBe(503);
  });
});

describe("thin research control plane", () => {
  it("protects operational metrics behind durable auth and strips unexpected fields", async () => {
    const fetch = mockService(async () => Response.json({ ...systemFixture(), secret: token }));
    const response = await researchGET(request("/api/research/system"), { params: Promise.resolve({ path: ["system"] }) });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(systemFixture());
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/research/system"))).toBe(true);
    expect((await proxyBackend(new Request("https://money.example.test/api/research/system"), "/research/system")).status).toBe(401);
    expect((await proxyBackend(request("/api/research/system", { method: "POST" }), "/research/system")).status).toBe(405);
  });
  it("fails closed for malformed operational telemetry without leaking the provider payload", async () => {
    mockService(async () => Response.json({ detail: token, jobs: { total: -1 } }));
    const response = await proxyBackend(request("/api/research/system"), "/research/system");
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain(token);
  });
  it("requires authentication before touching the compute service", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await proxyBackend(new Request("https://money.example.test/api/research"), "/research/jobs")).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("returns 202 after durable enqueue, without waiting for research", async () => {
    const job = { id: "e372355a-c387-46f9-a727-5c95cd1a1c04", ticker: "DEMO.L", status: "QUEUED" };
    const fetch = mockService(async () => Response.json(job, { status: 202 }));
    const response = await proxyBackend(request(undefined, { method: "POST", headers: { "Idempotency-Key": "test-durable-request" }, body: JSON.stringify({ ticker: "demo.l" }) }), "/research/jobs");
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual(job);
    const [url, init] = vi.mocked(global.fetch).mock.calls.find(([url]) => String(url).endsWith("/research/jobs"))!;
    expect(String(url)).toBe("https://compute.example.test/research/jobs");
    expect(init?.headers).toMatchObject({ Authorization: `Bearer ${token}`, "Idempotency-Key": "test-durable-request" });
    expect(JSON.parse(String(init?.body))).toEqual({ ticker: "DEMO.L" });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(response.headers.get("cache-control")).toContain("no-store");
  });
  it("retrieves the same research ID independently of the create request", async () => {
    const id = "e372355a-c387-46f9-a727-5c95cd1a1c04";
    mockService(async () => Response.json({ id, status: "COMPLETE" }));
    const response = await proxyBackend(request(`/api/research/${id}`), `/research/jobs/${id}`);
    expect(await response.json()).toEqual({ id, status: "COMPLETE" });
  });
  it("rejects arbitrary service paths and cross-origin job creation", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await proxyBackend(request(), "/admin/shell")).status).toBe(404);
    expect((await proxyBackend(request(undefined, { method: "POST", headers: { origin: "https://evil.example" } }), "/research/jobs")).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not forward unsafe mandates or unknown command fields", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const response = await proxyBackend(request(undefined, { method: "POST", body: JSON.stringify({ ticker: "DEMO.L", command: "execute" }) }), "/research/jobs");
    expect(response.status).toBe(400); expect(fetch).not.toHaveBeenCalled();
  });
  it("does not reveal backend errors or server credentials", async () => {
    mockService(async () => Response.json({ detail: `${token} ${password} ${secret}` }, { status: 500 }));
    const response = await proxyBackend(request(), "/research/jobs");
    const body = await response.text();
    expect(response.status).toBe(502);
    expect(body).not.toContain(token); expect(body).not.toContain(password); expect(body).not.toContain(secret);
  });
  it("retains sanitized degraded service health without exposing extra fields", async () => {
    mockService(async () => Response.json({ status: "degraded", database: "ready", worker: "stale", mode: "demo", error: token }, { status: 503 }));
    const response = await proxyBackend(request("/api/health"), "/health");
    expect(await response.json()).toEqual({ status: "degraded", database: "ready", worker: "stale", mode: "demo" });
  });
  it("reports unreachable compute honestly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("secret transport failure")));
    const response = await proxyBackend(request(), "/research/jobs");
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("secret transport failure");
  });
});
