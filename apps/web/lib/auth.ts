import "server-only";
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { authService, secureDeployment, serviceEndpoint } from "@/lib/service";

export const SESSION_COOKIE = "money_session";
export const SESSION_SECONDS = 60 * 60 * 8;

export function authConfigured(): boolean {
  const environment = process.env.MONEY_ENV;
  if (environment && !["development", "test", "preview", "production"].includes(environment)) return false;
  if (process.env.MONEY_DEPLOYMENT_ENV && !["local", "hosted"].includes(process.env.MONEY_DEPLOYMENT_ENV)) return false;
  if (process.env.NODE_ENV === "production" && !environment) return false;
  if (process.env.MONEY_AUTH_MODE === "saas") { try { serviceEndpoint("/v1/account/me"); return true; } catch { return false; } }
  if (process.env.MONEY_AUTH_MODE && process.env.MONEY_AUTH_MODE !== "private") return false;
  const password = process.env.MONEY_WEB_PASSWORD ?? "";
  const secret = process.env.SESSION_SECRET ?? "";
  return password.length >= 16 && secret.length >= 32 && password !== secret &&
    (!secureDeployment() || !/^(?:password|changeme|change-me|default|example|test)[-\s_\d!]*$/i.test(password));
}

export function passwordMatches(password: string): boolean {
  if (process.env.MONEY_AUTH_MODE === "saas") return false;
  if (!authConfigured() || password.length > 512) return false;
  const digest = (text: string) => createHash("sha256").update(text).digest();
  return timingSafeEqual(digest(password), digest(process.env.MONEY_WEB_PASSWORD!));
}

function sign(payload: string): string {
  if (!authConfigured()) throw new Error("Authentication is not configured");
  // Password rotation revokes every previously issued session.
  return createHmac("sha256", process.env.SESSION_SECRET!).update(`${payload}:${process.env.MONEY_WEB_PASSWORD}`).digest("base64url");
}

export function createSession(now = Date.now()): string {
  const payload = `${Math.floor(now / 1000) + SESSION_SECONDS}.${randomBytes(32).toString("base64url")}`;
  return `${payload}.${sign(payload)}`;
}

export function validSession(cookie: string | undefined, now = Date.now()): boolean {
  if (process.env.MONEY_AUTH_MODE === "saas") return false;
  if (!authConfigured() || !cookie || !/^\d{10}\.[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}$/.test(cookie)) return false;
  const [timestamp, nonce, signature] = cookie.split(".");
  const payload = `${timestamp}.${nonce}`;
  const expiry = Number(timestamp);
  if (expiry <= now / 1000 || expiry > now / 1000 + SESSION_SECONDS + 5) return false;
  return timingSafeEqual(Buffer.from(signature), Buffer.from(sign(payload)));
}

export function requestSession(request: Request): string | undefined {
  const cookie = request.headers.get("cookie")?.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${SESSION_COOKIE}=`));
  return cookie?.slice(SESSION_COOKIE.length + 1);
}

export const digest = (value: string) => createHash("sha256").update(value).digest("hex");
export const credentialVersion = () => digest(`${process.env.SESSION_SECRET}:${process.env.MONEY_WEB_PASSWORD}`);

export async function requestAuthenticated(request: Request): Promise<boolean> {
  if (process.env.MONEY_AUTH_MODE === "saas") {
    const { accountFetch, accountHeaders } = await import("@/lib/commercial");
    if (!accountHeaders(request)["X-Money-Session"]) return false;
    const response = await accountFetch(new Request(request.url, { headers: request.headers }), "/v1/account/me");
    if (response.status >= 500) throw new Error("Account service unavailable");
    return response.ok;
  }
  const cookie = requestSession(request);
  if (!validSession(cookie)) return false;
  const result = await authService<{ valid: boolean }>("sessions/validate", { token_hash: digest(cookie!), credential_version: credentialVersion() });
  return result.valid === true;
}

export async function registerSession(cookie: string): Promise<void> {
  const result = await authService<{ created: boolean }>("sessions", {
    token_hash: digest(cookie), credential_version: credentialVersion(),
    expires_at: new Date(Number(cookie.split(".")[0]) * 1000).toISOString(),
  });
  if (result.created !== true) throw new Error("Session could not be registered");
}

export async function revokeSession(request: Request): Promise<void> {
  const cookie = requestSession(request);
  if (!validSession(cookie)) return;
  await authService("sessions/revoke", { token_hash: digest(cookie!), credential_version: credentialVersion() });
}

export function sessionCookie(cookie: string, request: Request, maxAge = SESSION_SECONDS): string {
  return `${SESSION_COOKIE}=${cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${secureDeployment() || new URL(request.url).protocol === "https:" ? "; Secure" : ""}`;
}

export function sameOrigin(request: Request): boolean {
  const origin = new URL(request.url);
  return (!secureDeployment() || origin.protocol === "https:") && request.headers.get("origin") === origin.origin;
}

export function json(data: unknown, status = 200): Response {
  return Response.json(data, { status, headers: { "Cache-Control": "private, no-store" } });
}

export async function readLimitedJson(request: Pick<Request, "headers" | "body">, limit = 8192): Promise<unknown> {
  if (!request.headers.get("content-type")?.startsWith("application/json")) throw new Error("JSON required");
  if (Number(request.headers.get("content-length")) > limit) throw new Error("Request too large");
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Body required");
  let bytes = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > limit) { await reader.cancel(); throw new Error("Request too large"); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

export async function loginAllowed(): Promise<{ allowed: boolean; retry_after: number }> {
  // A global private-workspace bucket cannot be bypassed by forged IP headers.
  // PostgreSQL owns the counter across every Netlify function instance.
  return authService("rate-limit", { key: digest("money:private:login"), limit: 8, window_seconds: 60 });
}
