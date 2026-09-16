// Read-only HTTP acceptance. No credentials, login, enqueue or deployment writes.
import { pathToFileURL } from "node:url";

export const ROUTES = [
  ["/", "dashboard", "Your research workspace"],
  ["/dashboard", "dashboard", "Your research workspace"],
  ["/research", "jobs", "Research jobs"],
  ["/system", "health", "System health"],
];

function requireCheck(condition, code) {
  if (!condition) throw new Error(code);
}

export function deploymentOrigin(value) {
  const origin = new URL(value);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname);
  requireCheck(origin.protocol === "https:" || (loopback && origin.protocol === "http:"), "DEPLOYMENT_URL_REQUIRES_HTTPS");
  requireCheck(!origin.username && !origin.password && !origin.search && !origin.hash && origin.pathname === "/", "DEPLOYMENT_URL_MUST_BE_BARE_ORIGIN");
  return origin.origin;
}

export async function boundedText(response, maximumBytes) {
  requireCheck(Number(response.headers.get("content-length") ?? 0) <= maximumBytes, "HTTP_BODY_TOO_LARGE");
  requireCheck(response.body, "HTTP_BODY_MISSING");
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      requireCheck(length <= maximumBytes, "HTTP_BODY_TOO_LARGE");
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel();
    throw error;
  } finally { reader.releaseLock(); }
  return Buffer.concat(chunks).toString("utf8");
}

export function verifySecurityHeaders(headers, html, production = false) {
  const csp = headers.get("content-security-policy") ?? "";
  const nonce = csp.match(/script-src[^;]*'nonce-([^']+)'/)?.[1];
  requireCheck(nonce && html.includes(`nonce="${nonce}"`), "CSP_HYDRATION_NONCE_MISSING");
  requireCheck(!/'unsafe-(?:inline|eval)'/.test(csp), "UNSAFE_PRODUCTION_CSP");
  for (const directive of ["frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'"]) requireCheck(csp.includes(directive), "CSP_PROTECTION_MISSING");
  requireCheck(headers.get("x-content-type-options") === "nosniff", "NOSNIFF_HEADER_MISSING");
  requireCheck(headers.get("x-frame-options") === "DENY", "FRAME_HEADER_MISSING");
  requireCheck(headers.get("referrer-policy") === "same-origin", "REFERRER_POLICY_MISSING");
  const permissions = headers.get("permissions-policy") ?? "";
  for (const feature of ["camera=()", "microphone=()", "geolocation=()"]) requireCheck(permissions.includes(feature), "PERMISSIONS_POLICY_MISSING");
  requireCheck((headers.get("cache-control") ?? "").includes("no-store"), "AUTHENTICATED_SHELL_MUST_NOT_BE_CACHED");
  if (production) requireCheck(/max-age=[1-9]\d*/.test(headers.get("strict-transport-security") ?? ""), "PRODUCTION_HSTS_MISSING");
  return nonce;
}

export function extractAssets(html, origin) {
  const assets = new Map();
  for (const tag of html.matchAll(/<(script|link)\b[^>]*>/gi)) {
    const raw = tag[0].match(/\b(?:src|href)=["']([^"']+)["']/i)?.[1];
    if (!raw) continue;
    const asset = new URL(raw.replaceAll("&amp;", "&"), origin);
    const kind = asset.pathname.endsWith(".js") ? "javascript" : asset.pathname.endsWith(".css") ? "stylesheet" : null;
    if (!kind) continue;
    requireCheck(asset.origin === origin && !asset.username && !asset.password && asset.pathname.startsWith("/_next/static/"), "UNEXPECTED_ASSET_ORIGIN_OR_PATH");
    assets.set(asset.href, kind);
  }
  requireCheck([...assets.values()].includes("javascript"), "JAVASCRIPT_ASSET_MISSING");
  requireCheck([...assets.values()].includes("stylesheet"), "STYLESHEET_ASSET_MISSING");
  requireCheck(assets.size <= 48, "ASSET_LIST_EXCEEDS_BOUND");
  return assets;
}

function generic404(html) {
  return /looks like you(?:'|’|&#39;)ve followed a broken link|<title>\s*(?:page not found|site not found)\s*<\/title>|netlify[^<]*404/i.test(html);
}

export async function checkDeployment(value, { fetchImpl = fetch, production = false, timeoutMs = 10000 } = {}) {
  const origin = deploymentOrigin(value);
  requireCheck(Number.isInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 15000, "INVALID_REQUEST_TIMEOUT");
  const deadline = Date.now() + 60000;
  async function read(path, maximumBytes) {
    const remaining = deadline - Date.now();
    requireCheck(remaining > 0, "DEPLOYMENT_CHECK_TIMEOUT");
    const response = await fetchImpl(new URL(path, origin), { redirect: "error", cache: "no-store", signal: AbortSignal.timeout(Math.min(timeoutMs, remaining)) });
    return { response, text: await boundedText(response, maximumBytes) };
  }
  const assets = new Map();
  const nonces = new Set();
  const pages = [];
  for (const [path, view, title] of ROUTES) {
    const { response, text } = await read(path, 2_000_000);
    requireCheck(!generic404(text), `GENERIC_HOST_404:${path}`);
    requireCheck(response.status === 200, `PAGE_HTTP_${response.status}:${path}`);
    requireCheck((response.headers.get("content-type") ?? "").includes("text/html"), `PAGE_NOT_HTML:${path}`);
    requireCheck(text.includes("Money · Independent investment research") && text.includes(`data-money-page="${view}"`) && text.includes(title), `MONEY_ROUTE_MISSING:${path}`);
    const nonce = verifySecurityHeaders(response.headers, text, production);
    requireCheck(!nonces.has(nonce), "CSP_NONCE_REUSED_BETWEEN_REQUESTS");
    nonces.add(nonce);
    for (const [url, kind] of extractAssets(text, origin)) assets.set(url, kind);
    pages.push({ path, status: response.status, view });
  }
  const entries = [...assets];
  for (let offset = 0; offset < entries.length; offset += 4) {
    await Promise.all(entries.slice(offset, offset + 4).map(async ([url, kind]) => {
      const { response, text } = await read(url, 4_000_000);
      requireCheck(response.status === 200 && text.length > 0, `ASSET_UNAVAILABLE:${new URL(url).pathname}`);
      const mime = response.headers.get("content-type") ?? "";
      requireCheck(kind === "stylesheet" ? mime.startsWith("text/css") : /^(?:application|text)\/(?:javascript|x-javascript)/.test(mime), `ASSET_MIME_MISMATCH:${new URL(url).pathname}`);
      requireCheck(!/^\s*(?:<!doctype|<html)/i.test(text), "ASSET_RETURNED_HTML_REWRITE");
    }));
  }
  const sessionResponse = await read("/api/session", 8192);
  requireCheck([200, 503].includes(sessionResponse.response.status) && (sessionResponse.response.headers.get("content-type") ?? "").includes("application/json"), "SESSION_ROUTE_UNAVAILABLE");
  const session = JSON.parse(sessionResponse.text);
  requireCheck(sessionResponse.response.status === 503 ? typeof session.error === "string" : typeof session.configured === "boolean" && session.authenticated === false, "INVALID_SIGNED_OUT_SESSION_STATE");
  const controls = [];
  for (const path of ["/api/health", "/api/research", "/api/research/system"]) {
    const { response, text } = await read(path, 8192);
    requireCheck([401, 503].includes(response.status), `UNAUTHENTICATED_CONTROL_NOT_CLOSED:${path}`);
    requireCheck((response.headers.get("content-type") ?? "").includes("application/json"), `CONTROL_RETURNED_HTML:${path}`);
    requireCheck(typeof JSON.parse(text).error === "string", `CONTROL_ERROR_CONTRACT_MISSING:${path}`);
    controls.push({ path, status: response.status });
  }
  return {
    origin, observed_at: new Date().toISOString(), web_status: "VERIFIED", pages,
    assets: { javascript: entries.filter(([, kind]) => kind === "javascript").length, stylesheets: entries.filter(([, kind]) => kind === "stylesheet").length },
    authentication: sessionResponse.response.status === 503 ? "UNAVAILABLE" : session.configured ? "CONFIGURED_SIGNED_OUT" : "UNCONFIGURED", controls,
    research_readiness: "NOT_QUALIFIED_BY_WEB_CHECK", backend_status: "NOT_PROBED_WITHOUT_AUTHENTICATION",
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const value = args.find((arg) => !arg.startsWith("--")) ?? process.env.MONEY_WEB_URL;
  if (!value || args.some((arg) => arg.startsWith("--") && arg !== "--production")) {
    console.error("Usage: npm run check:deployment -- https://existing-site.netlify.app [--production]");
    process.exitCode = 2;
  } else {
    checkDeployment(value, { production: args.includes("--production") }).then(
      (result) => console.log(JSON.stringify(result, null, 2)),
      (error) => { console.error(`Deployment acceptance failed: ${error instanceof Error ? error.message : "unknown error"}`); process.exitCode = 1; },
    );
  }
}
