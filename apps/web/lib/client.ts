export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}

/** Clear every in-memory resource when account/workspace authority changes. */
export function resetAccountView(path: "/dashboard" | "/onboarding" | "/billing"): void {
  window.location.assign(new URL(path, window.location.origin).href);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const signal = init?.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
  const response = await fetch(path, { ...init, signal, cache: "no-store", headers: { "Content-Type": "application/json", ...init?.headers } });
  let data: Record<string, unknown>;
  try { data = await response.json(); }
  catch { throw new ApiError("Research service unavailable. Please retry.", response.status); }
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/session" && typeof window !== "undefined") window.dispatchEvent(new Event("money:session-expired"));
    throw new ApiError(typeof data.error === "string" ? data.error : "The request could not be completed", response.status);
  }
  return data as T;
}
