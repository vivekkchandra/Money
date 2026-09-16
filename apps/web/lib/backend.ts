import "server-only";
import { authConfigured, json, readLimitedJson, requestAuthenticated, sameOrigin } from "@/lib/auth";
import { validateJobInput } from "@/lib/contracts";

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const allowed = new RegExp(`^(?:/health|/research/(?:signals|outcomes|discovery)|/research/jobs(?:/${UUID}(?:/(?:reports|evidence))?)?)$`);

export async function proxyBackend(request: Request, path: string): Promise<Response> {
  if (!allowed.test(path)) return json({ error: "Unknown endpoint" }, 404);
  if (!authConfigured()) return json({ error: "Workspace authentication is not configured" }, 503);
  if (!requestAuthenticated(request)) return json({ error: "Please sign in to your workspace" }, 401);
  if (!["GET", "POST"].includes(request.method) || (request.method === "POST" && path !== "/research/jobs")) return json({ error: "Method not allowed" }, 405);
  if (request.method === "POST" && !sameOrigin(request)) return json({ error: "Invalid request origin" }, 403);
  const base = process.env.RESEARCH_API_URL;
  const token = process.env.RESEARCH_API_TOKEN;
  if (!base || !token) return json({ error: "Research service is not configured" }, 503);
  let endpoint: URL;
  try {
    endpoint = new URL(base);
    if (!(["https:"].includes(endpoint.protocol) || (endpoint.protocol === "http:" && ["localhost", "127.0.0.1", "research-api"].includes(endpoint.hostname)))) throw new Error("Invalid URL");
    if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash || !["", "/"].includes(endpoint.pathname)) throw new Error("Invalid URL");
    endpoint.pathname = path;
    if (path === "/research/signals") {
      const expired = new URL(request.url).searchParams.get("expired");
      if (expired === "true" || expired === "false") endpoint.searchParams.set("expired", expired);
    }
  } catch { return json({ error: "Research service configuration is invalid" }, 503); }
  let body: string | undefined;
  if (request.method === "POST") {
    try {
      const input = validateJobInput(await readLimitedJson(request));
      if (!input) return json({ error: "A valid ticker and a mandate within the research limits are required" }, 400);
      body = JSON.stringify(input);
    } catch { return json({ error: "A JSON request of at most 8 KB is required" }, 400); }
  }
  try {
    const response = await fetch(endpoint, {
      method: request.method, body, cache: "no-store", redirect: "error",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      signal: AbortSignal.timeout(10_000),
    });
    if (path === "/health") {
      const health = await response.json();
      const safe = (value: unknown, options: string[], fallback: string) => typeof value === "string" && options.includes(value) ? value : fallback;
      return json({
        status: safe(health.status, ["ok", "degraded", "unavailable"], "unavailable"),
        database: safe(health.database, ["ready", "unavailable", "unknown"], "unknown"),
        worker: safe(health.worker, ["ready", "unavailable", "unknown", "stale", "offline", "missing"], "unknown"),
        mode: safe(health.mode, ["demo", "live"], "unknown"),
      });
    }
    if (!response.ok) {
      const status = [400, 404, 409, 422, 429].includes(response.status) ? response.status : 502;
      return json({ error: status === 404 ? "Research record was not found" : status === 429 ? "Too many requests. Try again shortly." : "The research service could not fulfil this request" }, status);
    }
    return json(await response.json(), response.status);
  } catch { return json({ error: "Research service is unavailable. Your saved research remains in durable storage." }, 503); }
}
