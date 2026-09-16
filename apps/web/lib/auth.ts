import "server-only";
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

export const SESSION_COOKIE = "money_session";
export const SESSION_SECONDS = 60 * 60 * 8;

export function authConfigured(): boolean {
  return (process.env.MONEY_WEB_PASSWORD?.length ?? 0) >= 16 && (process.env.SESSION_SECRET?.length ?? 0) >= 32;
}

export function passwordMatches(password: string): boolean {
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
  const payload = String(Math.floor(now / 1000) + SESSION_SECONDS);
  return `${payload}.${sign(payload)}`;
}

export function validSession(cookie: string | undefined, now = Date.now()): boolean {
  if (!authConfigured() || !cookie || !/^\d{10}\.[A-Za-z0-9_-]{43}$/.test(cookie)) return false;
  const [payload, signature] = cookie.split(".");
  const expiry = Number(payload);
  if (expiry <= now / 1000 || expiry > now / 1000 + SESSION_SECONDS + 5) return false;
  return timingSafeEqual(Buffer.from(signature), Buffer.from(sign(payload)));
}

export function requestAuthenticated(request: Request): boolean {
  const cookie = request.headers.get("cookie")?.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${SESSION_COOKIE}=`));
  return validSession(cookie?.slice(SESSION_COOKIE.length + 1));
}

export function sameOrigin(request: Request): boolean {
  return request.headers.get("origin") === new URL(request.url).origin;
}

export function json(data: unknown, status = 200): Response {
  return Response.json(data, { status, headers: { "Cache-Control": "private, no-store" } });
}

export async function readLimitedJson(request: Request, limit = 8192): Promise<unknown> {
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

// Defense in depth within an instance. Configure Netlify's durable edge rate limit
// for /api/session before exposing the single-user workspace publicly.
const attempts = new Map<string, { count: number; reset: number }>();
export function loginAllowed(request: Request, now = Date.now()): boolean {
  const key = request.headers.get("x-nf-client-connection-ip") ?? "local";
  for (const [address, attempt] of attempts) if (attempt.reset < now) attempts.delete(address);
  const current = attempts.get(key) ?? { count: 0, reset: now + 60_000 };
  if (attempts.size >= 4096 && !attempts.has(key)) return false;
  current.count += 1;
  attempts.set(key, current);
  return current.count <= 8;
}
