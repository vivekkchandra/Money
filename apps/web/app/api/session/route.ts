import { authConfigured, createSession, json, loginAllowed, passwordMatches, readLimitedJson, registerSession, requestAuthenticated, revokeSession, sameOrigin, sessionCookie } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try { return json({ configured: authConfigured(), authenticated: await requestAuthenticated(request) }); }
  catch { return json({ error: "Workspace security service is unavailable. Please retry." }, 503); }
}

export async function POST(request: Request) {
  if (!sameOrigin(request)) return json({ error: "Invalid request origin" }, 403);
  if (!authConfigured()) return json({ error: "Workspace authentication is not configured" }, 503);
  try {
    const rate = await loginAllowed();
    if (!rate.allowed) {
      const response = json({ error: "Too many attempts. Please wait a minute.", code: "RATE_LIMITED" }, 429);
      response.headers.set("Retry-After", String(Math.max(1, Math.min(rate.retry_after || 60, 60))));
      return response;
    }
  } catch { return json({ error: "Workspace security service is unavailable. Please retry." }, 503); }
  let password: unknown;
  try {
    const data = await readLimitedJson(request, 1024);
    if (!data || typeof data !== "object" || Array.isArray(data) || Object.keys(data).length !== 1 || !("password" in data)) throw new Error("Invalid login");
    password = data.password;
  }
  catch { return json({ error: "A valid JSON request is required" }, 400); }
  if (typeof password !== "string" || !passwordMatches(password)) return json({ error: "Incorrect workspace password" }, 401);
  try {
    const cookie = createSession();
    await revokeSession(request);
    await registerSession(cookie);
    const response = json({ authenticated: true });
    response.headers.set("Set-Cookie", sessionCookie(cookie, request));
    return response;
  } catch { return json({ error: "Workspace security service is unavailable. Please retry." }, 503); }
}

export async function DELETE(request: Request) {
  if (!sameOrigin(request)) return json({ error: "Invalid request origin" }, 403);
  try {
    await revokeSession(request);
    const response = json({ authenticated: false });
    response.headers.set("Set-Cookie", sessionCookie("", request, 0));
    return response;
  } catch { return json({ error: "Sign-out could not be confirmed. Please retry." }, 503); }
}
