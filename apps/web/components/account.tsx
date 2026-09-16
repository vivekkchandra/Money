"use client";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { api, resetAccountView } from "@/lib/client";
import { MarketingShell } from "@/components/marketing";

export type Account = {
  user: {
    id: string;
    email: string;
    display_name: string;
    email_verified: boolean;
    email_notifications?: boolean;
  };
  workspaces: {
    id: string;
    name: string;
    role: string;
  }[];
  selected_workspace?: string | null;
};

const titles: Record<string, [string, string]> = {
  login: ["Welcome back.", "Sign in to your research workspace."],
  signup: ["A clearer view starts here.", "Create your account. Your investment decisions stay yours."],
  "forgot-password": ["Let's get you back in.", "We'll send a reset link if this email has an account."],
  "reset-password": ["Choose a new password.", "Reset links are single-use and expire."],
  "verify-email": ["Confirm your email.", "Verify your address before opening your workspace."],

  "accept-invite": [
    "Research, together.",
    "Sign in with the invited email address, then accept this workspace invitation."
  ],

  "resend-verification": ["Verify your email.", "Request a fresh verification link for your account."]
};

export function AccountForm(
  {
    page,
    enabled
  }: {
    page: string;
    enabled: boolean;
  }
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [token, setToken] = useState("");

  useEffect(() => {
    if (["verify-email", "reset-password", "accept-invite"].includes(page)) {
      const url = new URL(window.location.href);
      const value = new URLSearchParams(url.hash.slice(1)).get("token") ?? url.searchParams.get("token") ?? "";

      if (value) {
        window.history.replaceState(null, "", url.pathname);
        const timer = setTimeout(() => setToken(value), 0);
        return () => clearTimeout(timer);
      }
    }
  }, [page]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setNotice(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");

    const payload = page === "signup" ? {
      email,
      password,
      display_name: String(form.get("display_name") ?? "")
    } : page === "login" ? {
      email,
      password
    } : ["forgot-password", "resend-verification"].includes(page) ? {
      email
    } : page === "reset-password" ? {
      token,
      password
    } : {
      token
    };

    try {
      const result = await api<Account & {
        authenticated?: boolean;
      }>(`/api/account/${page === "accept-invite" ? "workspaces/accept-invitation" : page}`, {
        method: "POST",
        body: JSON.stringify(payload)
      });

      if (page === "login") {
        resetAccountView(result.workspaces?.length ? "/dashboard" : "/onboarding");
        return;
      }

      setNotice(
        page === "signup" ? "Check your email to verify your account before signing in." : page === "resend-verification" ? "If this account needs verification, a fresh link is on its way." : page === "forgot-password" ? "If the address has an account, a password-reset email is on its way." : page === "accept-invite" ? "Invitation accepted. Open your account to select the workspace." : page === "verify-email" ? "Your email is verified. You can now sign in." : "Your password has been reset. Sign in with your new password."
      );

      if (["verify-email", "reset-password", "accept-invite"].includes(page))
        setToken("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Please try again later.");
    } finally {
      setPending(false);
    }
  }

  const [title, description] = titles[page] ?? titles.login;

  return (
    <MarketingShell><main id="main" className="account-main" data-money-page={page}><div className="account-intro"><p className="eyebrow">YOUR RESEARCH, WITH PERSPECTIVE</p><h1>{title}</h1><p>{description}</p><div className="account-principles"><p>Independent perspectives</p><p>Evidence you can inspect</p><p>No automated trading</p></div></div><section className="panel account-card"><h2>{page === "signup" ? "Create your account" : page === "login" ? "Sign in" : "Account security"}</h2>{!enabled ? <div className="notice" role="status">Customer accounts are not enabled on this deployment. <Link href="/dashboard">Open the existing private workspace</Link>.</div> : <form onSubmit={submit}>
            {page === "signup" && <><label htmlFor="display_name">Your name</label><input id="display_name" name="display_name" autoComplete="name" required maxLength={100} /></>}
            {["signup", "login", "forgot-password", "resend-verification"].includes(page) && <><label htmlFor="email">Email address</label><input id="email" name="email" type="email" autoComplete="email" required maxLength={254} /></>}
            {["signup", "login", "reset-password"].includes(page) && <><label htmlFor="password">Password</label><input
                id="password"
                name="password"
                type="password"
                autoComplete={page === "login" ? "current-password" : "new-password"}
                required
                minLength={page === "login" ? 1 : 15}
                maxLength={128} /><p className="field-help">{page !== "login" && "Use at least 15 characters. A long, unique passphrase is best."}</p></>}
            {["verify-email", "reset-password", "accept-invite"].includes(page) && <><label htmlFor="token">Code from your email</label><input
                id="token"
                value={token}
                onChange={event => setToken(event.target.value)}
                autoComplete="one-time-code"
                required
                maxLength={256} /></>}
            {page === "signup" && <label className="checkbox-label"><input name="terms" type="checkbox" required /> <span>I understand that Money provides informational research, not investment advice. I have read the <Link href="/terms">terms</Link>and <Link href="/privacy">privacy notice</Link>.</span></label>}
            <button className="button full-width" disabled={pending}>{pending ? "Please wait…" : page === "signup" ? "Create account" : page === "login" ? "Sign in" : page === "forgot-password" ? "Send reset link" : page === "reset-password" ? "Reset password" : page === "accept-invite" ? "Accept invitation" : page === "resend-verification" ? "Resend verification" : "Verify email"}</button>
          </form>}{error && <p className="notice error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice} {["verify-email", "reset-password", "accept-invite"].includes(page) && <Link href="/login">Sign in</Link>}</p>}<div className="account-links">{page === "login" ? <><Link href="/forgot-password">Forgot password?</Link><Link href="/signup">Create an account</Link></> : <Link href="/login">Back to sign in</Link>}</div><p className="small-print">Secure account access. Your password and session are never stored in browser local storage.</p></section></main></MarketingShell>
  );
}

export function AccountWorkspace(
  {
    page
  }: {
    page: "onboarding" | "account";
  }
) {
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirmDeletion, setConfirmDeletion] = useState(false);

  useEffect(() => {
    let active = true;

    api<Account>("/api/account/me").then(value => {
      if (active)
        setAccount(value);
    }).catch(reason => {
      if (active)
        setError(reason.message);
    });

    return () => {
      active = false;
    };
  }, []);

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const name = String(new FormData(event.currentTarget).get("name"));

    try {
      const result = await api<{
        workspace: {
          id: string;
        };
      }>("/api/account/workspaces", {
        method: "POST",

        body: JSON.stringify({
          name
        })
      });

      await api("/api/account/select-workspace", {
        method: "POST",

        body: JSON.stringify({
          workspace_id: result.workspace.id
        })
      });

      resetAccountView("/billing");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Workspace could not be created.");
    } finally {
      setPending(false);
    }
  }

  async function deletion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);

    try {
      await api("/api/account/deletion", {
        method: "POST",

        body: JSON.stringify({
          password: String(new FormData(event.currentTarget).get("password"))
        })
      });

      setNotice(
        "Your deletion request has been recorded. Audit and billing retention will be reviewed before removal."
      );

      setConfirmDeletion(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Please retry.");
    } finally {
      setPending(false);
    }
  }

  async function exportAccount() {
    setPending(true);
    setError(null);

    try {
      const result = await api<Record<string, unknown>>("/api/account/export");

      const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {
        type: "application/json"
      }));

      const link = document.createElement("a");
      link.href = url;
      link.download = "money-account-export.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Export unavailable.");
    } finally {
      setPending(false);
    }
  }

  return (
    <MarketingShell><main id="main" className="marketing-main editorial" data-money-page={page}><p className="eyebrow">{page === "onboarding" ? "STEP 1 · YOUR WORKSPACE" : "YOUR ACCOUNT"}</p><h1>{page === "onboarding" ? "Make room for better research." : "You're in control."}</h1>{error && <p className="notice error" role="alert">{error} <Link href="/login">Sign in</Link></p>}{notice && <p className="notice" role="status">{notice}</p>}{!account && !error && <p role="status">Loading your account…</p>}{account && <><section className="panel"><h2>{account.user.display_name}</h2><p>{account.user.email}· {account.user.email_verified ? "Email verified" : "Verification required"}</p>{account.workspaces.length > 0 && <div className="workspace-list">{account.workspaces.map(
                workspace => <div key={workspace.id}><span>{workspace.name} <small>{workspace.role}</small></span><button
                    className="button secondary"
                    onClick={async () => {
                      try {
                        await api("/api/account/select-workspace", {
                          method: "POST",

                          body: JSON.stringify({
                            workspace_id: workspace.id
                          })
                        });

                        resetAccountView("/dashboard");
                      } catch (reason) {
                        setError(reason instanceof Error ? reason.message : "Workspace unavailable.");
                      }
                    }}>Open workspace</button></div>
              )}</div>}</section><section className="panel account-card"><h2>Create a workspace</h2><p>A private space for your research, history and subscription.</p><form onSubmit={createWorkspace}><label htmlFor="workspace-name">Workspace name</label><input id="workspace-name" name="name" required maxLength={100} placeholder="Your research desk" /><button className="button" disabled={pending}>Create workspace & choose a plan</button></form></section>{page === "account" && <section className="panel"><h2>Email preferences</h2><label className="checkbox-label"><input
                type="checkbox"
                checked={account.user.email_notifications === true}
                disabled={pending}
                onChange={async event => {
                  const enabled = event.target.checked;
                  setPending(true);

                  try {
                    await api("/api/account/preferences", {
                      method: "PATCH",

                      body: JSON.stringify({
                        email_notifications: enabled
                      })
                    });

                    setAccount({
                      ...account,

                      user: {
                        ...account.user,
                        email_notifications: enabled
                      }
                    });
                  } catch (reason) {
                    setError(reason instanceof Error ? reason.message : "Preferences could not be saved.");
                  } finally {
                    setPending(false);
                  }
                }} /><span>Email me research notifications. Security and essential account messages are not optional.</span></label><h2>Privacy and account controls</h2><p>Export your account data or request deletion. Deletion is reviewed against the published retention policy; it does not silently erase audit evidence.</p><div className="marketing-actions"><button className="button secondary" disabled={pending} onClick={exportAccount}>Export my data</button><button className="text-button" onClick={() => setConfirmDeletion(true)}>Request account deletion</button></div>{confirmDeletion && <form onSubmit={deletion} className="deletion-confirm" aria-label="Confirm account deletion request"><h3>Confirm deletion request</h3><label htmlFor="deletion-password">Current password</label><input
                id="deletion-password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={512} /><button className="button" disabled={pending}>Confirm request</button> <button type="button" className="text-button" onClick={() => setConfirmDeletion(false)}>Keep my account</button></form>}</section>}<Link className="button" href="/dashboard">Go to workspace</Link> <Link className="text-button" href="/team">Manage workspace members</Link></>}</main></MarketingShell>
  );
}
