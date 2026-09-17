import { readFileSync } from "node:fs";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "@/app/api/session/route";
import { sameOrigin } from "@/lib/auth";
import { smokeWebOrigin } from "../scripts/smoke-origin.mjs";

const password = "fixture-only-private-workspace-password";
const origin = smokeWebOrigin(41234);

function login(url = origin, requestOrigin = url) {
  return new NextRequest(`${url}/api/session`, { method: "POST", headers: {
    Origin: requestOrigin, "Content-Type": "application/json",
  }, body: JSON.stringify({ password }) });
}

beforeEach(() => {
  vi.stubEnv("MONEY_ENV", "test");
  vi.stubEnv("MONEY_DEPLOYMENT_ENV", "local");
  vi.stubEnv("MONEY_AUTH_MODE", "private");
  vi.stubEnv("MONEY_WEB_PASSWORD", password);
  vi.stubEnv("SESSION_SECRET", "fixture-only-session-secret-at-least-thirty-two");
  vi.stubEnv("RESEARCH_API_TOKEN", "fixture-only-service-token-at-least-thirty-two");
  vi.stubEnv("RESEARCH_API_URL", "http://127.0.0.1:43210");
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
    const path = new URL(input).pathname;
    if (path === "/internal/auth/rate-limit") return Response.json({ allowed: true, retry_after: 0 });
    if (path === "/internal/auth/sessions") return Response.json({ created: true });
    throw new Error("Unexpected auth operation in test");
  }));
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Next.js smoke origin canonicalization", () => {
  it("uses a canonical origin shared by actual NextRequest and browser Origin", async () => {
    const request = login();
    expect(request.url).toBe(`${origin}/api/session`);
    expect(sameOrigin(request)).toBe(true);
    const response = await POST(request);
    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("HttpOnly; SameSite=Strict");
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  });
  it("reproduces the old loopback-IP failure without weakening CSRF", async () => {
    const request = login("http://127.0.0.1:41234");
    expect(request.url).toBe(`${origin}/api/session`);
    expect(request.headers.get("origin")).toBe("http://127.0.0.1:41234");
    expect(sameOrigin(request)).toBe(false);
    expect((await POST(request)).status).toBe(403);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
  it("still rejects a different port or a forged external Origin", async () => {
    for (const foreign of ["http://localhost:41235", "https://attacker.example.test"]) {
      expect((await POST(login(origin, foreign))).status).toBe(403);
    }
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
  it("does not permit HTTP when the application is hosted", async () => {
    vi.stubEnv("MONEY_DEPLOYMENT_ENV", "hosted");
    expect((await POST(login())).status).toBe(403);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
  it.each([0, -1, 65536, 1.5, NaN, "41234"])("rejects an invalid local port %j", port => {
    expect(() => smokeWebOrigin(port)).toThrow("Invalid smoke web port");
  });
  it.each(["smoke.mjs", "commercial-smoke.mjs"])("keeps %s on the shared origin and explicit loopback listener", filename => {
    const source = readFileSync(new URL(`../scripts/${filename}`, import.meta.url), "utf8");
    expect(source).toContain('import { smokeWebOrigin } from "./smoke-origin.mjs"');
    expect(source).toContain("const origin = smokeWebOrigin(webPort)");
    expect(source).not.toContain("const origin = `http://127.0.0.1:");
    expect(source).toMatch(/"--hostname",\s*"127\.0\.0\.1"/);
    expect(source).toMatch(/\.listen\(0, "127\.0\.0\.1"/);
  });
});
