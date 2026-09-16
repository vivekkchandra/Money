import "server-only";
import { json, readLimitedJson, sameOrigin } from "@/lib/auth";
import { productionEnvironment, serviceEndpoint } from "@/lib/service";
export const ACCOUNT_COOKIE = "money_account";
export const WORKSPACE_COOKIE = "money_workspace";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
export const saasMode = () => process.env.MONEY_AUTH_MODE === "saas";

export function cookieValue(request: Request, name: string): string | undefined {
  return request.headers.get("cookie")?.split(";").map(part => part.trim()).find(part => part.startsWith(`${name}=`))?.slice(name.length + 1);
}

export function accountHeaders(request: Request): Record<string, string> {
  const session = cookieValue(request, ACCOUNT_COOKIE);
  const workspace = cookieValue(request, WORKSPACE_COOKIE);

  return {
    ...(session && /^[A-Za-z0-9_-]{32,256}$/.test(session) ? {
      "X-Money-Session": session
    } : {}),

    ...(workspace && UUID.test(workspace) ? {
      "X-Money-Workspace": workspace
    } : {})
  };
}

function cookie(name: string, value: string, request: Request, maxAge: number): string {
  return `${name}=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${productionEnvironment() || new URL(request.url).protocol === "https:" ? "; Secure" : ""}`;
}

export async function accountFetch(request: Request, path: string, body?: unknown): Promise<Response> {
  const [pathname, query] = path.split("?");
  const endpoint = serviceEndpoint(pathname);

  if (query)
    endpoint.search = query;

  return fetch(endpoint, {
    method: request.method,
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(10_000),

    headers: {
      Authorization: `Bearer ${process.env.RESEARCH_API_TOKEN}`,
      "Content-Type": "application/json",
      ...accountHeaders(request)
    },

    ...(body === undefined ? {} : {
      body: JSON.stringify(body)
    })
  });
}

async function boundedResponse(response: Response): Promise<Record<string, unknown>> {
  const reader = response.body?.getReader();

  if (!reader)
    throw new Error("Empty response");

  const chunks: Uint8Array[] = [];
  let size = 0;

  try {
    while (true) {
      const {
        value,
        done
      } = await reader.read();

      if (done)
        break;

      size += value.byteLength;

      if (size > 2_000_000) {
        await reader.cancel();
        throw new Error("Response too large");
      }

      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));

  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid response");

  return value as Record<string, unknown>;
}

function safeUser(value: unknown) {
  const user = value as Record<string, unknown> | undefined;

  if (!user || typeof user.id !== "string" || typeof user.email !== "string" || typeof user.display_name !== "string")
    throw new Error("Invalid account response");

  return {
    id: user.id,
    email: user.email,
    display_name: user.display_name,
    email_verified: user.email_verified === true,
    email_notifications: user.email_notifications === true
  };
}

function safeWorkspaces(value: unknown) {
  if (!Array.isArray(value) || value.length > 100)
    throw new Error("Invalid workspaces");

  return value.map(workspace => {
    if (!workspace || !UUID.test(workspace.id) || typeof workspace.name !== "string" || !["OWNER", "ADMIN", "MEMBER", "VIEWER"].includes(workspace.role))
      throw new Error("Invalid workspace");

    return {
      id: String(workspace.id),
      name: workspace.name as string,
      role: workspace.role as string
    };
  });
}

function safeFailure(response: Response, data: Record<string, unknown>) {
  const status = [400, 401, 402, 403, 404, 409, 422, 429].includes(response.status) ? response.status : 503;
  const detail = data.detail && typeof data.detail === "object" ? data.detail as Record<string, unknown> : data;
  const code = typeof detail.code === "string" && /^[A-Z_]{3,80}$/.test(detail.code) ? detail.code : "REQUEST_UNAVAILABLE";

  const messages: Record<number, string> = {
    400: "Please check the information you entered.",
    401: "Please sign in with a verified account to continue.",
    402: "Your workspace allowance does not cover this request. Review your plan and usage.",
    403: "Your account does not have access to this action.",
    404: "This record was not found in your workspace.",
    409: "This request conflicts with an existing record. Please refresh and retry.",
    422: "Please check the information you entered.",
    429: "Too many attempts. Please wait before trying again.",
    503: "This service is temporarily unavailable. Please try again later."
  };

  const requestId = response.headers.get("x-request-id");
  const accountMessages: Record<string, string> = {
    OWNERSHIP_TRANSFER_REQUIRED: "Transfer workspace ownership to another member before requesting account deletion.",
    BILLING_CHECKOUT_PENDING: "Complete or let the current checkout expire before closing your account.",
    WORKSPACE_CLOSING: "This workspace is closing and cannot start new research or subscriptions.",
    SUCCESSOR_INVALID: "Choose a verified, active member of this workspace as the new owner.",
    PASSWORD_INVALID: "Your current password could not be verified. Please try again."
  };

  return json({
    error: accountMessages[code] ?? messages[status],
    code,

    ...(requestId && /^[a-zA-Z0-9_-]{1,80}$/.test(requestId) ? {
      request_id: requestId
    } : {})
  }, status);
}

export async function commercialProxy(request: Request, group: "account" | "product", path: string): Promise<Response> {
  if (!saasMode()) return json({
    error: "Customer accounts are not enabled on this deployment."
  }, 503);

  const key = `${request.method} ${path}`;

  const account = new Set([
    "POST signup",
    "POST login",
    "POST verify-email",
    "POST resend-verification",
    "POST forgot-password",
    "POST reset-password",
    "POST logout",
    "GET me",
    "GET export",
    "POST deletion",
    "PATCH preferences",
    "GET workspaces",
    "POST workspaces",
    "POST select-workspace",
    "POST workspaces/invitations",
    "POST workspaces/accept-invitation",
    "POST workspaces/transfer-ownership",
    "GET workspaces/members"
  ]);

  const memberAction = /^(?:PATCH|DELETE) workspaces\/members\/[a-f0-9-]{36}$/.test(key);
  const product = /^(?:GET (?:summary|plans|history|watchlist|notifications|preferences|admin)|PUT preferences|POST (?:events|billing\/(?:checkout|portal)|watchlist|notifications\/[a-f0-9]{64}\/read)|DELETE watchlist\/[A-Z0-9.^_-]{1,30})$/;

  if (group === "account" ? !(account.has(key) || memberAction) : !product.test(key)) return json({
    error: "Unknown endpoint"
  }, 404);

  if (request.method !== "GET" && !sameOrigin(request)) return json({
    error: "Invalid request origin"
  }, 403);

  const publicAction = group === "account" && ["signup", "login", "verify-email", "resend-verification", "forgot-password", "reset-password"].includes(path);

  if (!publicAction && !accountHeaders(request)["X-Money-Session"]) return json({
    error: "Please sign in to continue.",
    code: "SESSION_EXPIRED"
  }, 401);

  let body: unknown;

  if (["POST", "PUT", "PATCH"].includes(request.method)) {
    try {
      body = await readLimitedJson(request);
    } catch {
      return json({
        error: "A JSON request of at most 8 KB is required."
      }, 400);
    }
  }

  try {
    if (path === "select-workspace" && group === "account") {
      const selected = body as Record<string, unknown>;

      if (!selected || Object.keys(selected).length !== 1 || typeof selected.workspace_id !== "string" || !UUID.test(selected.workspace_id)) return json({
        error: "Choose a valid workspace."
      }, 400);

      const upstream = await accountFetch(new Request(request.url, {
        headers: request.headers
      }), "/v1/account/me");

      const data = await boundedResponse(upstream);

      if (!upstream.ok)
        return safeFailure(upstream, data);

      if (!safeWorkspaces(data.workspaces).some(workspace => workspace.id === selected.workspace_id)) return json({
        error: "Workspace access denied."
      }, 403);

      const response = json({
        selected: true
      });

      response.headers.append("Set-Cookie", cookie(WORKSPACE_COOKIE, selected.workspace_id, request, 60 * 60 * 8));
      return response;
    }

    const endpoint = group === "account" && (path === "workspaces" || path.startsWith("workspaces/")) ? `/v1/${path}` : `/v1/${group}/${path}`;
    const target = serviceEndpoint(endpoint);

    if (group === "product" && path === "history") for (const field of ["query", "state", "offset", "limit", "created_from", "created_to", "sort"]) {
      const value = new URL(request.url).searchParams.get(field);

      if (value && value.length <= 100)
        target.searchParams.set(field, value);
    }

    const upstream = await accountFetch(request, `${target.pathname}${target.search}`, body);
    const data = await boundedResponse(upstream);

    if (!upstream.ok)
      return safeFailure(upstream, data);

    if (group === "account" && path === "login") {
      const token = data.session_token;
      const expiry = Date.parse(String(data.expires_at));

      if (typeof token !== "string" || !/^[A-Za-z0-9_-]{32,256}$/.test(token) || !Number.isFinite(expiry) || expiry <= Date.now())
        throw new Error("Invalid session");

      const workspaces = safeWorkspaces(data.workspaces);

      const response = json({
        authenticated: true,
        user: safeUser(data.user),
        workspaces
      });

      response.headers.append("Set-Cookie", cookie(
        ACCOUNT_COOKIE,
        token,
        request,
        Math.min(60 * 60 * 24 * 30, Math.floor((expiry - Date.now()) / 1000))
      ));

      response.headers.append(
        "Set-Cookie",
        cookie(WORKSPACE_COOKIE, workspaces[0]?.id ?? "", request, workspaces.length ? 60 * 60 * 8 : 0)
      );

      return response;
    }

    if (group === "account" && path === "me") {
      const workspaces = safeWorkspaces(data.workspaces);
      const selected = cookieValue(request, WORKSPACE_COOKIE);

      return json({
        user: safeUser(data.user),
        workspaces,
        selected_workspace: workspaces.find(workspace => workspace.id === selected)?.id ?? null
      });
    }

    if (group === "account" && path === "logout") {
      const response = json({
        signed_out: true
      });

      for (const name of [ACCOUNT_COOKIE, WORKSPACE_COOKIE])
        response.headers.append("Set-Cookie", cookie(name, "", request, 0));

      return response;
    }

    if (group === "account" && path === "workspaces") return json(request.method === "GET" ? {
      workspaces: safeWorkspaces(data.workspaces)
    } : {
      workspace: safeWorkspaces([data.workspace])[0]
    }, upstream.status);

    if (group === "account" && path === "workspaces/accept-invitation") return json({
      workspace: safeWorkspaces([data.workspace])[0]
    });

    if (group === "account" && path === "workspaces/members") {
      if (!Array.isArray(data.members))
        throw new Error("Invalid members");

      return json({
        members: data.members.map(member => ({
          user_id: member.user_id,
          display_name: member.display_name,
          email: member.email,
          role: member.role
        }))
      });
    }

    if (group === "account" && path !== "export") return json({
      ...(data.transferred === true ? { transferred: true } : {}),
      ...(data.accepted === true ? {
        accepted: true
      } : {}),

      ...(data.verified === true ? {
        verified: true
      } : {}),

      ...(data.reset === true ? {
        reset: true
      } : {}),

      ...(data.requested === true ? {
        requested: true
      } : {})
    }, upstream.status);

    if (group === "product" && path.startsWith("billing/")) {
      const url = new URL(String(data.url));

      if (url.protocol !== "https:" || !["checkout.stripe.com", "billing.stripe.com"].includes(url.hostname) || url.username || url.password)
        throw new Error("Invalid billing URL");

      return json({
        url: url.href
      });
    }

    return json(data, upstream.status);
  } catch {
    return json({
      error: "This service is temporarily unavailable. Your saved research remains stored.",
      code: "SERVICE_UNAVAILABLE"
    }, 503);
  }
}
