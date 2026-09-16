"use client";
import Link from "next/link";
import { useEffect, useReducer, useState, type FormEvent } from "react";
import { api, resetAccountView } from "@/lib/client";
import type { Job } from "@/lib/contracts";
import type { Account } from "@/components/account";
import { DataRecord, EmptyState, JobList } from "@/components/primitives";
import { historyFiltersReducer, historyQuery, INITIAL_HISTORY_FILTERS } from "@/lib/history";

function useCustomer<T>(path: string, revision = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState("");

  useEffect(() => {
    const controller = new AbortController();

    api<T>(path, {
      signal: controller.signal
    }).then(value => {
      if (!controller.signal.aborted) {
        setData(value);
        setError(null);
        setLoaded(path);
      }
    }).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason.message);
        setLoaded(path);
      }
    });

    return () => controller.abort();
  }, [path, revision]);

  return {
    data: loaded === path ? data : null,
    error: loaded === path ? error : null,
    loading: loaded !== path
  };
}

type Summary = {
  plan: string;
  status: string;
  allowance: number;
  used: number;
  remaining: number;
  period_end: string;
  billing_available: boolean;
  cost_status: string;
  account_monthly_limit?: number;
  account_monthly_used?: number;
};

type Plan = {
  plan_id: string;
  research_jobs_per_period: number;
  seats: number;
  retention_days: number;
  live_research: boolean;
};

export function CustomerToolbar() {
  const {
    data,
    error
  } = useCustomer<Account>("/api/account/me");

  const [failure, setFailure] = useState<string | null>(null);

  async function select(id: string) {
    try {
      await api("/api/account/select-workspace", {
        method: "POST",

        body: JSON.stringify({
          workspace_id: id
        })
      });

      resetAccountView("/dashboard");
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "Workspace unavailable.");
    }
  }

  return (
    <section className="customer-toolbar" aria-label="Workspace controls"><div><label htmlFor="workspace-switch">Your workspace</label>{data?.workspaces.length ? <select
          id="workspace-switch"
          value={data.selected_workspace ?? ""}
          onChange={event => void select(event.target.value)}><option value="" disabled>Choose a workspace</option>{data.workspaces.map(item => <option key={item.id} value={item.id}>{item.name}· {item.role}</option>)}</select> : <Link href="/onboarding">Create your first workspace</Link>}</div><form action="/history" className="global-search"><label className="sr-only" htmlFor="global-search">Search your workspace research</label><input
          id="global-search"
          type="search"
          name="query"
          placeholder="Search ticker, company or research ID"
          maxLength={100} /><button className="button secondary">Search</button></form><Link href="/account">Account</Link><Link href="/team">Team</Link><Link href="/billing">Plan & billing</Link>{(failure || error) && <p className="form-error" role="alert">{failure || error}</p>}</section>
  );
}

export function UsageSummary() {
  const {
    data,
    error
  } = useCustomer<Summary>("/api/product/summary");

  return <section className="panel usage-panel"><div><p className="eyebrow">YOUR RESEARCH ALLOWANCE</p><h2>{data ? `${data.plan} workspace` : "Plan & usage"}</h2></div>{data ? <ResearchAllowances summary={data} /> : <p role="status">{error ?? "Loading your allowance…"}</p>}<Link className="text-button" href="/billing">Manage plan →</Link></section>;
}

export function ResearchAllowances({ summary }: {
  summary: Pick<Summary, "remaining" | "allowance" | "period_end" | "account_monthly_limit" | "account_monthly_used">;
}) {
  const limit = summary.account_monthly_limit;
  const used = summary.account_monthly_used;
  const accountUsageAvailable = typeof limit === "number" && Number.isSafeInteger(limit) && limit >= 0 &&
    typeof used === "number" && Number.isSafeInteger(used) && used >= 0;
  return <>
    <p>Workspace: <strong>{summary.remaining}</strong>{" "}of {summary.allowance} research requests remaining.</p>
    <p className="small-print">Workspace plan period ends {new Date(summary.period_end).toLocaleDateString("en-GB")}.</p>
    {accountUsageAvailable && <div>
      <p>Account-wide: <strong>{Math.max(0, limit - used)}</strong>{" "}of {limit} research requests remaining.</p>
      <p className="small-print">Operational ceiling across your workspaces for the current UTC calendar month. This is separate from workspace billing periods.</p>
    </div>}
    <p className="small-print">A research request can finish without a qualified candidate.</p>
  </>;
}

export function Billing() {
  const {
    data,
    error
  } = useCustomer<Summary>("/api/product/summary");

  const plans = useCustomer<{
    plans: Plan[];
  }>("/api/product/plans");

  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function open(path: string, plan?: string) {
    setPending(true);
    setFailure(null);

    try {
      const result = await api<{
        url: string;
      }>(`/api/product/billing/${path}`, {
        method: "POST",

        body: JSON.stringify({
          ...(plan ? {
            plan
          } : {}),

          idempotency_key: crypto.randomUUID()
        })
      });

      const url = new URL(result.url);

      if (url.protocol !== "https:" || !["checkout.stripe.com", "billing.stripe.com"].includes(url.hostname))
        throw new Error("Billing link could not be verified.");

      window.location.assign(url.href);
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "Billing unavailable.");
    } finally {
      setPending(false);
    }
  }

  return (
    <><UsageSummary />{(error || failure) && <p className="notice error" role="alert">{failure ?? error}</p>}<section className="panel"><h2>Your subscription</h2><p>Manage renewals, payment details and cancellation through the secure billing portal. The payment provider determines subscription state, never the browser.</p>{data?.billing_available ? <button className="button" disabled={pending} onClick={() => void open("portal")}>Open billing portal</button> : <p className="notice" role="status">Paid subscriptions are not available on this deployment. No payment is being taken.</p>}</section><div className="marketing-cards plans">{plans.data?.plans.map(
          plan => <section className="panel" key={plan.plan_id}><p className="eyebrow">{plan.plan_id}</p><h2>{plan.research_jobs_per_period}investigations</h2><p>Per subscription period · {plan.seats}seat{plan.seats === 1 ? "" : "s"}</p><p>{plan.retention_days}days plan retention</p><p>{plan.live_research ? "Live research subject to qualification" : "Sandbox entitlement only"}</p>{data?.billing_available && plan.plan_id !== "FREE" && <button className="button" disabled={pending} onClick={() => void open("checkout", plan.plan_id)}>Review secure checkout</button>}</section>
        )}</div>{plans.error && <p className="notice error">{plans.error}</p>}<Link className="button secondary" href="/mandate">Review research preferences →</Link></>
  );
}

export function ResearchHistory() {
  const [filters, dispatch] = useReducer(historyFiltersReducer, INITIAL_HISTORY_FILTERS);
  const { state, offset, created_from, created_to, sort } = filters;
  const [draft, setDraft] = useState("");

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("query")?.slice(0, 100) ?? "";

    const timer = setTimeout(() => {
      dispatch({ type: "filter", changes: { query: initial } });
      setDraft(initial);
    }, 0);

    return () => clearTimeout(timer);
  }, []);

  const params = historyQuery(filters);

  const {
    data,
    error,
    loading
  } = useCustomer<{
    jobs: Job[];
    next_offset: number | null;
  }>(`/api/product/history?${params}`);

  return (
    <section className="panel"><h2>Your research history</h2><form
        className="history-filters"
        onSubmit={event => {
          event.preventDefault();
          dispatch({ type: "filter", changes: { query: draft } });
        }}><div><label htmlFor="history-query">Ticker, company or research ID</label><input
            id="history-query"
            type="search"
            value={draft}
            onChange={event => setDraft(event.target.value)}
            maxLength={100} /></div><div><label htmlFor="history-state">Research status</label><select
            id="history-state"
            value={state}
            onChange={event => {
              dispatch({ type: "filter", changes: { state: event.target.value } });
            }}><option value="">All states</option>{["QUEUED", "RUNNING", "COMPLETE", "FAILED", "REJECTED"].map(value => <option key={value}>{value}</option>)}</select></div>
        <div><label htmlFor="history-from">Created from (UTC)</label><input id="history-from" type="date" value={created_from} max={created_to || undefined} onChange={event => dispatch({ type: "filter", changes: { created_from: event.target.value } })} aria-describedby="history-date-help" /></div>
        <div><label htmlFor="history-to">Created to (UTC)</label><input id="history-to" type="date" value={created_to} min={created_from || undefined} onChange={event => dispatch({ type: "filter", changes: { created_to: event.target.value } })} aria-describedby="history-date-help" /></div>
        <div><label htmlFor="history-sort">Sort by date</label><select id="history-sort" value={sort} onChange={event => dispatch({ type: "filter", changes: { sort: event.target.value === "oldest" ? "oldest" : "newest" } })}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></div>
        <button className="button">Search</button></form><p id="history-date-help" className="field-help">Optional date filters include the entire selected UTC calendar days.</p>{error ? <p className="notice error" role="alert">{error}</p> : loading ? <p role="status">Loading research history…</p> : <JobList jobs={data?.jobs ?? []} />}<div className="pagination"><button
          className="button secondary"
          disabled={offset === 0}
          onClick={() => dispatch({ type: "page", offset: offset - 25 })}>Previous</button><span>Page {Math.floor(offset / 25) + 1}</span><button
          className="button secondary"
          disabled={data?.next_offset == null}
          onClick={() => dispatch({ type: "page", offset: data?.next_offset ?? offset })}>Next</button></div></section>
  );
}

export function CustomerWatchlist() {
  const [revision, setRevision] = useState(0);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const {
    data,
    error,
    loading
  } = useCustomer<{
    items: {
      ticker: string;
      created_at: string;
    }[];
  }>("/api/product/watchlist", revision);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const form = event.currentTarget;

    try {
      await api("/api/product/watchlist", {
        method: "POST",

        body: JSON.stringify({
          ticker: String(new FormData(form).get("ticker")).trim().toUpperCase()
        })
      });

      form.reset();
      setFailure(null);
      setRevision(value => value + 1);
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "Unable to save ticker.");
    } finally {
      setPending(false);
    }
  }

  async function remove(ticker: string) {
    setPending(true);

    try {
      await api(`/api/product/watchlist/${encodeURIComponent(ticker)}`, {
        method: "DELETE"
      });

      setRevision(value => value + 1);
      setFailure(null);
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "Unable to remove ticker.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="panel"><h2>Your watchlist</h2><p>Keep ideas together. Adding a ticker does not create research or imply eligibility.</p><form className="watch-add" onSubmit={add}><label className="sr-only" htmlFor="watch-ticker">Ticker to watch</label><input id="watch-ticker" name="ticker" required maxLength={30} placeholder="Enter ticker" /><button className="button" disabled={pending}>Add ticker</button></form>{(error || failure) && <p className="notice error" role="alert">{failure ?? error}</p>}{loading ? <p role="status">Loading watchlist…</p> : !data?.items.length ? <EmptyState
        title="A place for your next question."
        detail="Add a ticker to keep it in your workspace watchlist." /> : <div className="workspace-list">{data.items.map(
          item => <div key={item.ticker}><strong>{item.ticker}</strong><Link href={`/history?query=${encodeURIComponent(item.ticker)}`}>Research history</Link><Link href={`/jobs?ticker=${encodeURIComponent(item.ticker)}`}>Research again</Link><button
              className="text-button"
              disabled={pending}
              aria-label={`Remove ${item.ticker}`}
              onClick={() => void remove(item.ticker)}>Remove</button></div>
        )}</div>}</section>
  );
}

export function Notifications() {
  const [revision, setRevision] = useState(0);
  const [failure, setFailure] = useState<string | null>(null);

  const {
    data,
    error,
    loading
  } = useCustomer<{
    items: {
      id: string;
      job_id: string | null;
      message: string;
      created_at: string;
      read_at: string | null;
    }[];
  }>("/api/product/notifications", revision);

  return (
    <section className="panel"><h2>Notifications</h2>{(error || failure) && <p className="notice error" role="alert">{failure ?? error}</p>}{loading ? <p role="status">Loading notifications…</p> : !data?.items.length ? <EmptyState title="You're up to date." detail="Research and account notifications will appear here." /> : data.items.map(
        item => <article className="notification-item" key={item.id}><p>{item.message}</p><small>{new Date(item.created_at).toLocaleString("en-GB")}</small>{item.job_id && <Link href={`/research/${item.job_id}`}>Open research</Link>}{!item.read_at && <button
            className="text-button"
            onClick={async () => {
              try {
                await api(`/api/product/notifications/${item.id}/read`, {
                  method: "POST",
                  body: "{}"
                });

                setRevision(value => value + 1);
              } catch (reason) {
                setFailure(reason instanceof Error ? reason.message : "Unable to update notification.");
              }
            }}>Mark read</button>}</article>
      )}</section>
  );
}

export function TeamSettings() {
  const [revision, setRevision] = useState(0);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);

  const {
    data,
    error
  } = useCustomer<{
    members: {
      user_id: string;
      display_name: string;
      email: string;
      role: string;
    }[];
  }>("/api/account/workspaces/members", revision);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const form = event.currentTarget;
    const fields = new FormData(form);

    try {
      await api("/api/account/workspaces/invitations", {
        method: "POST",

        body: JSON.stringify({
          email: fields.get("email"),
          role: fields.get("role")
        })
      });

      setNotice("The invitation has been queued for delivery. Available seats are checked when it is accepted.");
      form.reset();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Invitation unavailable.");
    } finally {
      setPending(false);
    }
  }

  async function remove(id: string) {
    setPending(true);

    try {
      await api(`/api/account/workspaces/members/${id}`, {
        method: "DELETE"
      });

      setRevision(value => value + 1);
      setConfirm(null);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Member could not be removed.");
    } finally {
      setPending(false);
    }
  }

  async function transfer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setPending(true);
    try {
      await api("/api/account/workspaces/transfer-ownership", {
        method: "POST",
        body: JSON.stringify({ user_id: fields.get("user_id"), password: fields.get("password") })
      });
      form.reset();
      setRevision(value => value + 1);
      setNotice("Ownership transferred. Your role in this workspace is now Admin.");
      resetAccountView("/dashboard");
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Ownership could not be transferred.");
    } finally {
      setPending(false);
    }
  }

  return (
    <><section className="panel"><h2>Your workspace team</h2><p>Only an owner can invite or remove members. Seat allowances and permissions are enforced by the service.</p>{(error || notice) && <p className="notice" role="status">{error ?? notice}</p>}<div className="workspace-list">{data?.members.map(
            member => <div key={member.user_id}><span>{member.display_name}<small>· {member.email}</small></span><span>{member.role}</span>{member.role !== "OWNER" && <button className="text-button" onClick={() => setConfirm(member.user_id)}>Remove member</button>}{confirm === member.user_id && <div role="group" aria-label="Confirm removal"><p>Remove this member’s access to workspace research?</p><button className="button" disabled={pending} onClick={() => void remove(member.user_id)}>Confirm removal</button> <button className="text-button" onClick={() => setConfirm(null)}>Cancel</button></div>}</div>
          )}</div></section><section className="panel account-card"><h2>Invite a colleague</h2><form onSubmit={invite}><label htmlFor="invite-email">Email address</label><input id="invite-email" name="email" type="email" autoComplete="off" required maxLength={254} /><label htmlFor="invite-role">Workspace role</label><select id="invite-role" name="role" defaultValue="MEMBER"><option>VIEWER</option><option>MEMBER</option><option>ADMIN</option></select><button className="button full-width" disabled={pending}>Send invitation</button></form></section>
      <section className="panel account-card"><h2>Transfer workspace ownership</h2><p>Owners can transfer responsibility to a verified, existing member before leaving. You will become an Admin. Your research records remain in the workspace.</p>
        <form onSubmit={transfer}>
          <label htmlFor="transfer-member">New owner</label><select id="transfer-member" name="user_id" required defaultValue=""><option value="" disabled>Select a workspace member</option>{data?.members.filter(member => member.role !== "OWNER").map(member => <option key={member.user_id} value={member.user_id}>{member.display_name} — {member.email}</option>)}</select>
          <label htmlFor="transfer-password">Your current password</label><input id="transfer-password" name="password" type="password" autoComplete="current-password" required maxLength={256} />
          <label><input type="checkbox" required /> I understand that ownership and billing responsibility will transfer.</label>
          <button className="button full-width" disabled={pending || !data?.members.some(member => member.role !== "OWNER")}>Confirm ownership transfer</button>
        </form>
      </section></>
  );
}

export function InternalAdmin() {
  const {
    data,
    error,
    loading
  } = useCustomer<Record<string, unknown>>("/api/product/admin");

  return <section className="panel"><h2>Internal service operations</h2><p>Restricted to explicitly authorised support operators. This view omits credentials and research payloads.</p>{error && <p className="notice error" role="alert">{error}</p>}{loading && <p role="status">Checking operator access…</p>}{data && <DataRecord data={data} />}</section>;
}
