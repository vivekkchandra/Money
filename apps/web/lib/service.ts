import "server-only";

export function productionEnvironment(): boolean {
  return process.env.MONEY_ENV === "production";
}

export function serviceEndpoint(path: string): URL {
  const base = process.env.RESEARCH_API_URL;
  const token = process.env.RESEARCH_API_TOKEN;
  if (!base || !token || token.length < 32) throw new Error("Research service configuration is invalid");
  const endpoint = new URL(base);
  const local = endpoint.protocol === "http:" && ["localhost", "127.0.0.1", "research-api"].includes(endpoint.hostname);
  if (endpoint.protocol !== "https:" && (!local || productionEnvironment())) throw new Error("Research service configuration is invalid");
  if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash || endpoint.pathname !== "/") throw new Error("Research service configuration is invalid");
  endpoint.pathname = path;
  return endpoint;
}

export async function authService<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await fetch(serviceEndpoint(`/internal/auth/${path}`), {
    method: "POST", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
    headers: { Authorization: `Bearer ${process.env.RESEARCH_API_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error("Workspace security service is unavailable");
  return await response.json() as T;
}
