import "server-only";
import { authConfigured, json, readLimitedJson, requestAuthenticated, requestSession, sameOrigin, validSession } from "@/lib/auth";
import { validateJobInput } from "@/lib/contracts";
import { serviceEndpoint } from "@/lib/service";
import { parseSystemMetrics } from "@/lib/system";
import { accountHeaders, saasMode } from "@/lib/commercial";

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const allowed = new RegExp(`^(?:/health(?:/ready)?|/research/(?:signals|outcomes|discovery|universe|alerts|system)|/research/jobs(?:/${UUID}(?:/(?:reports|evidence))?)?)$`);

export async function proxyBackend(request: Request, path: string): Promise<Response> {
  if (!allowed.test(path)) return json({ error: "Unknown endpoint" }, 404);
  if (!authConfigured()) return json({ error: "Workspace authentication is not configured" }, 503);
  if (saasMode() ? !accountHeaders(request)["X-Money-Session"] : !validSession(requestSession(request))) return json({ error: "Please sign in to your workspace", code: "SESSION_EXPIRED" }, 401);
  if (!["GET", "POST"].includes(request.method) || (request.method === "POST" && path !== "/research/jobs")) return json({ error: "Method not allowed" }, 405);
  if (request.method === "POST" && !sameOrigin(request)) return json({ error: "Invalid request origin" }, 403);
  const token = process.env.RESEARCH_API_TOKEN;
  let endpoint: URL;
  try {
    endpoint = serviceEndpoint(path);
    if (path === "/research/signals") {
      const expired = new URL(request.url).searchParams.get("expired");
      if (expired === "true" || expired === "false") endpoint.searchParams.set("expired", expired);
    }
  } catch { return json({ error: "Research service configuration is invalid" }, 503); }
  const idempotencyKey = request.headers.get("idempotency-key");
  if (idempotencyKey && !/^[a-zA-Z0-9._:-]{8,128}$/.test(idempotencyKey)) return json({ error: "Invalid idempotency key" }, 400);
  let body: string | undefined;
  if (request.method === "POST") {
    try {
      const input = validateJobInput(await readLimitedJson(request));
      if (!input) return json({ error: "A valid ticker and a mandate within the research limits are required" }, 400);
      body = JSON.stringify(input);
    } catch { return json({ error: "A JSON request of at most 8 KB is required" }, 400); }
  }
  try {
    if (!await requestAuthenticated(request)) return json({ error: "Your session has expired. Please sign in again.", code: "SESSION_EXPIRED" }, 401);
    const response = await fetch(endpoint, {
      method: request.method, body, cache: "no-store", redirect: "error",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(saasMode() ? accountHeaders(request) : {}), ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}) },
      signal: AbortSignal.timeout(10_000),
    });
    if (path === "/health" || path === "/health/ready") {
      const health = await response.json();
      const safe = (value: unknown, options: string[], fallback: string) => typeof value === "string" && options.includes(value) ? value : fallback;
      return json({
        status: safe(health.status, ["ok", "degraded", "unavailable"], "unavailable"),
        database: safe(health.database, ["ready", "unavailable", "unknown"], "unknown"),
        worker: safe(health.worker, ["ready", "unavailable", "unknown", "stale", "offline", "missing"], "unknown"),
        mode: safe(health.mode, ["demo", "live"], "unknown"),
        ...(path === "/health/ready" ? {
          version: typeof health.version === "string" && /^[a-zA-Z0-9.+_-]{1,50}$/.test(health.version) ? health.version : "unknown",
          git_sha: typeof health.git_sha === "string" && /^[a-f0-9]{7,40}$/.test(health.git_sha) ? health.git_sha : "unknown",
          environment: safe(health.environment, ["development", "test", "preview", "production"], "unknown"),
          auth_mode: "private", schema_revision: typeof health.schema_revision === "string" && /^[a-zA-Z0-9_-]{1,64}$/.test(health.schema_revision) ? health.schema_revision : "unknown",
          queue_depth: Number.isSafeInteger(health.queue_depth) && health.queue_depth >= 0 ? health.queue_depth : null,
          queue_age_seconds: typeof health.queue_age_seconds === "number" && Number.isFinite(health.queue_age_seconds) ? health.queue_age_seconds : null,
        } : {}),
      });
    }
    if (!response.ok) {
      const status = [400, 401, 402, 403, 404, 409, 422, 429].includes(response.status) ? response.status : 502;
      const messages: Record<number, string> = { 401: "Your session has expired. Please sign in again.", 402: "Your workspace research allowance is exhausted. Review your plan and usage.", 403: "You do not have access to this research action.", 404: "Research record was not found", 429: "Too many requests. Try again shortly." };
      const requestId = response.headers.get("x-request-id");
      return json({ error: messages[status] ?? "The research service could not fulfil this request", ...(status === 401 ? { code: "SESSION_EXPIRED" } : {}), ...(requestId && /^[a-zA-Z0-9_-]{1,80}$/.test(requestId) ? { request_id: requestId } : {}) }, status);
    }
    const result = await response.json();
    return json(path === "/research/system" ? parseSystemMetrics(result) : result, response.status);
  } catch { return json({ error: "Research service is unavailable. Your saved research remains in durable storage." }, 503); }
}
