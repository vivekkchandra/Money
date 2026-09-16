import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Workspace } from "@/components/workspace";
import { canonicalView } from "@/lib/routes";
import { boundedText, checkDeployment, deploymentArguments, deploymentOrigin, ROUTES } from "../scripts/check-deployment.mjs";

const origin = "https://money-deployment.example.test";

function fixtureResponse(path: string, nonce: string, unavailable = false): Response {
  const route = ROUTES.find(([candidate]) => candidate === path);
  if (route) return new Response(`<!doctype html><html><head><title>Money · Independent investment research</title><link rel="stylesheet" href="/_next/static/styles.css" /></head><body><div data-money-page="${route[1]}">${route[2]}</div><script nonce="${nonce}" src="/_next/static/app.js"></script></body></html>`, { headers: {
    "content-type": "text/html", "content-security-policy": `default-src 'self'; script-src 'self' 'nonce-${nonce}' 'strict-dynamic'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'`,
    "x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "same-origin", "permissions-policy": "camera=(), microphone=(), geolocation=()", "cache-control": "private, no-store", "strict-transport-security": "max-age=31536000",
  } });
  if (path.endsWith(".js")) return new Response("/* acceptance fixture only */", { headers: { "content-type": "application/javascript" } });
  if (path.endsWith(".css")) return new Response("body { color: black; }", { headers: { "content-type": "text/css" } });
  if (path === "/api/session") return Response.json({ configured: !unavailable, authenticated: false });
  return Response.json({ error: unavailable ? "Research service unavailable" : "Please sign in" }, { status: unavailable ? 503 : 401 });
}

function server({ unavailable = false, change = (_path: string, response: Response): Response => response } = {}) {
  let counter = 0;
  return vi.fn(async (input: string | URL | Request) => {
    const path = new URL(input instanceof Request ? input.url : String(input)).pathname;
    return change(path, fixtureResponse(path, `nonce-${++counter}`, unavailable));
  });
}

describe("supported production product routes", () => {
  it.each([
    [[], "dashboard", "Your research workspace"],
    [["dashboard"], "dashboard", "Your research workspace"],
    [["research"], "jobs", "Research jobs"],
    [["system"], "health", "System health"],
    [["health"], "health", "System health"],
    [["plans"], "billing", "A plan for your research."],
    [["history"], "history", "Research history"],
  ] as const)("renders %j without requiring a backend", (view, marker, title) => {
    const html = renderToStaticMarkup(<Workspace view={[...view]} />);
    expect(html).toContain(`data-money-page="${marker}"`);
    expect(html).toContain(title);
    expect(html).not.toContain("This page is outside the research universe");
    expect(html).not.toContain("A valid research ID is required");
  });
  it("preserves research IDs and does not swallow unknown nested aliases", () => {
    expect(canonicalView(["research", "research-id", "evidence"])).toEqual(["research", "research-id", "evidence"]);
    expect(canonicalView(["dashboard", "unknown"])).toEqual(["dashboard", "unknown"]);
  });
});

describe("read-only deployed HTTP acceptance", () => {
  it("verifies route markers, JS/CSS and headers while backend configuration is missing", async () => {
    const fetchImpl = server({ unavailable: true });
    const result = await checkDeployment(origin, { fetchImpl, production: true });
    expect(result.web_status).toBe("VERIFIED");
    expect(result.authentication).toBe("UNCONFIGURED");
    expect(result.backend_status).toBe("NOT_PROBED_WITHOUT_AUTHENTICATION");
    expect(result.assets).toEqual({ javascript: 1, stylesheets: 1 });
    expect(result.research_readiness).toBe("NOT_QUALIFIED_BY_WEB_CHECK");
    expect(fetchImpl.mock.calls).toHaveLength(ROUTES.length + 7);
    expect(fetchImpl.mock.calls.every(([url]) => new URL(String(url)).origin === origin)).toBe(true);
  });
  it("recognizes generic Netlify 404 content, including a misleading HTTP 200", async () => {
    const fetchImpl = server({ change: (path, response) => path === "/" ? new Response("<title>Page Not Found</title>Looks like you've followed a broken link on Netlify", { status: 200 }) : response });
    await expect(checkDeployment(origin, { fetchImpl })).rejects.toThrow("GENERIC_HOST_404:/");
  });
  it("verifies the actual deployed build rather than trusting a triggering workflow SHA", async () => {
    const sha = "a".repeat(40);
    const fetchImpl = server({ change: (path, response) => path === "/api/health" ? Response.json({ error: "Please sign in", web: { status: "ok", version: "0.1.0", git_sha: sha } }, { status: 401 }) : response });
    expect((await checkDeployment(origin, { fetchImpl, expectedSha: sha })).deployed_sha).toBe(sha);
    await expect(checkDeployment(origin, { fetchImpl, expectedSha: "b".repeat(40) })).rejects.toThrow("DEPLOYED_GIT_SHA_MISMATCH");
    await expect(checkDeployment(origin, { fetchImpl: server(), expectedSha: sha })).rejects.toThrow("DEPLOYED_GIT_SHA_MISMATCH");
    await expect(checkDeployment(origin, { fetchImpl, expectedSha: "" })).rejects.toThrow("INVALID_EXPECTED_GIT_SHA");
  });
  it("parses an optional exact expected SHA and rejects malformed or incomplete flags", () => {
    const sha = "b".repeat(40);
    expect(deploymentArguments([origin, "--production", "--expected-sha", sha], {})).toEqual({ value: origin, production: true, expectedSha: sha });
    expect(deploymentArguments([], { MONEY_WEB_URL: origin })).toMatchObject({ value: origin, expectedSha: undefined });
    for (const args of [[origin, "--expected-sha"], [origin, "--expected-sha", ""], [origin, "--expected-sha", "main"], [origin, "--expected-sha", "--production"], [origin, "--expected-sha", sha, "--expected-sha", sha], [origin, "--unknown"], [origin, origin]]) expect(() => deploymentArguments(args, {})).toThrow();
  });
  it("rejects catchall HTML when the requested product screen is absent", async () => {
    const fetchImpl = server({ change: (path, response) => path === "/dashboard" ? fixtureResponse("/system", "different-nonce") : response });
    await expect(checkDeployment(origin, { fetchImpl })).rejects.toThrow("MONEY_ROUTE_MISSING:/dashboard");
  });
  it.each(["missing", "html-rewrite"])("rejects %s static assets", async (failure) => {
    const fetchImpl = server({ change: (path, response) => path.endsWith(".css") ? new Response(failure === "missing" ? "missing" : "<!doctype html><html>SPA fallback</html>", { status: failure === "missing" ? 404 : 200, headers: { "content-type": "text/css" } }) : response });
    await expect(checkDeployment(origin, { fetchImpl })).rejects.toThrow(failure === "missing" ? "ASSET_UNAVAILABLE" : "ASSET_RETURNED_HTML_REWRITE");
  });
  it("rejects missing security headers", async () => {
    const fetchImpl = server({ change: (path, response) => { if (path === "/") response.headers.delete("x-content-type-options"); return response; } });
    await expect(checkDeployment(origin, { fetchImpl })).rejects.toThrow("NOSNIFF_HEADER_MISSING");
  });
  it("rejects an exposed unauthenticated research resource", async () => {
    const fetchImpl = server({ change: (path, response) => path === "/api/research" ? Response.json({ jobs: [] }) : response });
    await expect(checkDeployment(origin, { fetchImpl })).rejects.toThrow("UNAUTHENTICATED_CONTROL_NOT_CLOSED");
  });
  it("rejects redirects instead of checking a different site", async () => {
    const fetchImpl = server({ change: (path, response) => path === "/" ? new Response("redirect", { status: 301, headers: { location: "https://another.example" } }) : response });
    await expect(checkDeployment(origin, { fetchImpl })).rejects.toThrow("PAGE_HTTP_301");
  });
  it("bounds response bodies and rejects credential-bearing origins", async () => {
    await expect(boundedText(new Response("x".repeat(20)), 10)).rejects.toThrow("HTTP_BODY_TOO_LARGE");
    expect(() => deploymentOrigin("https://user:password@example.test/")).toThrow("BARE_ORIGIN");
    expect(() => deploymentOrigin("http://example.test/")).toThrow("REQUIRES_HTTPS");
    expect(deploymentOrigin("http://127.0.0.1:3000/")).toBe("http://127.0.0.1:3000");
  });
});
