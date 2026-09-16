import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCommit, webRelease } from "@/lib/build-info";
import { GET } from "@/app/api/health/route";

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("public build metadata without backend or secret exposure", () => {
  it("accepts only complete commit hashes, preferring Netlify's actual commit", () => {
    const sha = "a".repeat(40), fallback = "b".repeat(40);
    expect(buildCommit({ COMMIT_REF: sha, MONEY_GIT_SHA: fallback })).toBe(sha);
    expect(buildCommit({ COMMIT_REF: "bad", MONEY_GIT_SHA: fallback })).toBe(fallback);
    expect(buildCommit({ COMMIT_REF: "main", MONEY_GIT_SHA: "1234567", SECRET: "private" })).toBe("unknown");
  });
  it("cannot relabel an existing baked build by changing runtime environment", () => {
    vi.stubEnv("MONEY_WEB_BUILD_SHA", "c".repeat(40));
    vi.stubEnv("COMMIT_REF", "a".repeat(40));
    expect(webRelease().git_sha).toBe("c".repeat(40));
    vi.stubEnv("MONEY_WEB_BUILD_SHA", "unknown");
    expect(webRelease().git_sha).toBe("unknown");
    vi.stubEnv("MONEY_WEB_BUILD_SHA", "unexpected-runtime-secret");
    expect(webRelease().git_sha).toBe("unknown");
  });
  it("reports web build on missing backend configuration without opening readiness", async () => {
    vi.stubEnv("MONEY_AUTH_MODE", "private");
    vi.stubEnv("MONEY_WEB_PASSWORD", "");
    vi.stubEnv("MONEY_WEB_BUILD_SHA", "d".repeat(40));
    vi.stubEnv("RESEARCH_API_TOKEN", "sensitive-backend-token");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const response = await GET(new Request("https://money.example.test/api/health"));
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.web).toEqual({ status: "ok", version: "0.1.0", git_sha: "d".repeat(40) });
    expect(body.database).toBeUndefined();
    expect(JSON.stringify(body)).not.toContain("sensitive-backend-token");
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(fetch).not.toHaveBeenCalled();
  });
});
