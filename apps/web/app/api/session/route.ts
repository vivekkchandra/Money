import { authConfigured, createSession, json, loginAllowed, passwordMatches, readLimitedJson, requestAuthenticated, sameOrigin, SESSION_COOKIE, SESSION_SECONDS } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  return json({ configured: authConfigured(), authenticated: requestAuthenticated(request) });
}

export async function POST(request: Request) {
  if (!sameOrigin(request)) return json({ error: "Invalid request origin" }, 403);
  if (!authConfigured()) return json({ error: "Workspace authentication is not configured" }, 503);
  if (!loginAllowed(request)) return json({ error: "Too many attempts. Please wait a minute." }, 429);
  let password: unknown;
  try { password = (await readLimitedJson(request, 1024) as { password?: unknown }).password; }
  catch { return json({ error: "A valid JSON request is required" }, 400); }
  if (typeof password !== "string" || !passwordMatches(password)) return json({ error: "Incorrect workspace password" }, 401);
  const response = json({ authenticated: true });
  response.headers.set("Set-Cookie", `${SESSION_COOKIE}=${createSession()}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${SESSION_SECONDS}${new URL(request.url).protocol === "https:" ? "; Secure" : ""}`);
  return response;
}

export async function DELETE(request: Request) {
  if (!sameOrigin(request)) return json({ error: "Invalid request origin" }, 403);
  const response = json({ authenticated: false });
  response.headers.set("Set-Cookie", `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0`);
  return response;
}
