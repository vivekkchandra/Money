import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InstrumentPicker } from "@/components/instrument-picker";
import { ResearchRequest } from "@/components/workspace";
import { CustomerWatchlist } from "@/components/customer-tools";
import { proxyBackend } from "@/lib/backend";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { ACCOUNT_COOKIE, WORKSPACE_COOKIE } from "@/lib/commercial";
import { validateJobInput } from "@/lib/contracts";
import { INITIAL_SEARCH, instrumentQuery, instrumentStatus, parseInstrumentSearch, searchReducer, type Instrument, type InstrumentSearch } from "@/lib/instruments";
import { GET } from "@/app/api/research/[[...path]]/route";

const stock: Instrument = { instrument_id: "ABC.L", ticker: "ABC.L", company: "Example UK plc", exchange: "LSE", currency: "GBX", eligibility: "VERIFIED_ELIGIBLE", research_allowed: true, verified_at: "2026-09-16T10:00:00+00:00", synthetic: false };
const unavailable: Instrument = { ...stock, instrument_id: "XYZ.L", ticker: "XYZ.L", company: "Unverified company", eligibility: "UNKNOWN", research_allowed: false };
const catalogue: InstrumentSearch = { instruments: [stock, unavailable], total: 2, limit: 10, offset: 0, coverage: "reviewed_catalogue", mode: "live" };
const origin = "https://money.example.test";
const serviceToken = "test-backend-token-server-only-long-enough";

describe("bounded reviewed company catalogue", () => {
  it("normalizes queries and supplies bounded pagination defaults", () => {
    expect(instrumentQuery(new URLSearchParams({ query: "  Example UK  " }))?.toString()).toBe("query=Example+UK&limit=10&offset=0");
    expect(instrumentQuery(new URLSearchParams({ query: "ABC.L", limit: "20", offset: "1000" }))?.get("offset")).toBe("1000");
  });
  it.each(["", "query=", "query=%20", `query=${"x".repeat(81)}`, "query=a%00b", "query=a&query=b", "query=a&limit=10&limit=11", "query=a&offset=0&offset=1", "query=a&limit=0", "query=a&limit=21", "query=a&offset=-1", "query=a&offset=1001", "query=a&limit=1.5", "query=a&offset=1e2", "query=a&url=http://localhost"])("rejects invalid search parameters: %s", value => {
    expect(instrumentQuery(new URLSearchParams(value))).toBeNull();
  });
  it("projects only reviewed fields and preserves unsupported-currency unavailable entries", () => {
    const response = { ...catalogue, secret: "not-a-browser-field", instruments: [{ ...stock, provider_secret: "not-a-browser-field" }, { ...unavailable, currency: "USD" }] };
    const result = parseInstrumentSearch(response);
    expect(result.instruments[0]).toEqual(stock);
    expect(result.instruments[1].currency).toBe("USD");
    expect(JSON.stringify(result)).not.toContain("not-a-browser-field");
  });
  it.each([
    { instruments: [{ ...stock, research_allowed: true, eligibility: "UNKNOWN" }] },
    { instruments: [{ ...stock, currency: "USD" }] },
    { instruments: [{ ...stock, ticker: "wrong" }] },
    { instruments: [{ ...stock, company: "x".repeat(257) }] },
    { instruments: [{ ...stock, verified_at: "not-a-date" }] },
    { instruments: [{ ...stock, verified_at: "2026-09-16T10:00:00" }] },
    { instruments: [{ ...stock, synthetic: true }] },
    { instruments: [{ ...stock, instrument_id: "DEMO.L", ticker: "DEMO.L", synthetic: false }] },
    { instruments: [stock, stock] }, { limit: 21 }, { total: -1 }, { offset: 1001 },
    { instruments: Array.from({ length: 11 }, () => stock) }, { total: 1 }, { coverage: "entire_market" },
  ])("fails closed for malformed or misleading responses: %j", update => {
    expect(() => parseInstrumentSearch({ ...catalogue, ...update })).toThrow("temporarily unavailable");
  });
  it("keeps the explicit synthetic demonstration separate from live companies", () => {
    const demo = { ...stock, instrument_id: "DEMO.L", ticker: "DEMO.L", synthetic: true };
    expect(parseInstrumentSearch({ ...catalogue, instruments: [demo], total: 1, mode: "demo" }).instruments).toEqual([demo]);
    expect(instrumentStatus(demo)).toContain("Synthetic demonstration · not live research");
    expect(() => parseInstrumentSearch({ ...catalogue, mode: "demo" })).toThrow();
    expect(instrumentStatus(unavailable)).toContain("not verified");
    expect(instrumentStatus({ ...unavailable, eligibility: "VERIFIED_INELIGIBLE" })).toContain("Outside");
  });
  it("accepts the canonical API ticker length but rejects longer identifiers", () => {
    const ticker = "A".repeat(32);
    expect(parseInstrumentSearch({ ...catalogue, instruments: [{ ...stock, ticker, instrument_id: ticker }], total: 1 }).instruments[0].ticker).toBe(ticker);
    expect(validateJobInput({ ticker })?.ticker).toBe(ticker);
    expect(validateJobInput({ ticker: `${ticker}A` })).toBeNull();
  });
});

describe("explicit accessible selection state", () => {
  const querying = () => searchReducer(INITIAL_SEARCH, { type: "query", query: "Example" });
  const loaded = () => searchReducer(querying(), { type: "loaded", revision: 1, result: catalogue });
  it("never selects from typing, watchlist query prefill or a loaded response", () => {
    expect(querying().phase).toBe("loading");
    expect(loaded().phase).toBe("ready");
    expect(loaded().active).toBe(-1);
    const html = renderToStaticMarkup(<ResearchRequest onCreated={() => {}} />);
    expect(html).toContain('role="combobox"');
    expect(html).toContain('aria-autocomplete="list"');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Request research/);
    expect(html).not.toContain("Stock ticker, e.g.");
  });
  it("ignores stale success and error after a query is changed", () => {
    const current = searchReducer(querying(), { type: "query", query: "New company" });
    expect(searchReducer(current, { type: "loaded", revision: 1, result: catalogue })).toBe(current);
    expect(searchReducer(current, { type: "failed", revision: 1, error: "old failure" })).toBe(current);
    expect(current.results).toEqual([]);
  });
  it("uses arrows to skip unavailable research results and wraps navigation", () => {
    const next = searchReducer(loaded(), { type: "move", direction: 1 });
    expect(next.active).toBe(0);
    expect(searchReducer(next, { type: "move", direction: 1 }).active).toBe(0);
    expect(searchReducer(next, { type: "move", direction: -1 }).active).toBe(0);
    expect(searchReducer(next, { type: "close" })).toMatchObject({ open: false, active: -1 });
  });
  it("requires an actual available returned record and clears the selection state on edits", () => {
    const current = loaded();
    expect(searchReducer(current, { type: "select", instrument: unavailable })).toBe(current);
    expect(searchReducer(current, { type: "select", instrument: { ...stock } })).toBe(current);
    const selected = searchReducer(current, { type: "select", instrument: stock });
    expect(selected).toMatchObject({ phase: "selected", open: false, query: "Example UK plc (ABC.L)" });
    expect(searchReducer(selected, { type: "query", query: "Another" })).toMatchObject({ phase: "loading", results: [], active: -1 });
  });
  it("permits informational watchlisting without making unavailable stocks researchable", () => {
    const current = loaded();
    expect(searchReducer(current, { type: "select", instrument: unavailable, allowUnavailable: true }).phase).toBe("selected");
    expect(unavailable.research_allowed).toBe(false);
    const html = renderToStaticMarkup(<CustomerWatchlist />);
    expect(html).toContain('role="combobox"');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Add company/);
  });
  it("presents empty results, retry state, bounded queries and escaped names", () => {
    const empty = searchReducer(querying(), { type: "loaded", revision: 1, result: { ...catalogue, instruments: [], total: 0 } });
    expect(empty).toMatchObject({ phase: "ready", results: [], total: 0 });
    const failed = searchReducer(querying(), { type: "failed", revision: 1, error: "Company search unavailable" });
    expect(searchReducer(failed, { type: "retry" })).toMatchObject({ phase: "loading", revision: 2, error: null });
    expect(searchReducer(INITIAL_SEARCH, { type: "query", query: "x".repeat(200) }).query).toHaveLength(80);
    const html = renderToStaticMarkup(<InstrumentPicker selected={{ ...stock, company: "<script>alert(1)</script>" }} onSelect={() => {}} />);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain('aria-live="polite"');
  });
});

describe("authenticated instrument proxy", () => {
  beforeEach(() => {
    vi.stubEnv("MONEY_ENV", "test");
    vi.stubEnv("MONEY_AUTH_MODE", "private");
    vi.stubEnv("MONEY_WEB_PASSWORD", "test-private-password-at-least-sixteen");
    vi.stubEnv("SESSION_SECRET", "test-session-secret-at-least-thirty-two-characters");
    vi.stubEnv("RESEARCH_API_URL", "https://compute.example.test");
    vi.stubEnv("RESEARCH_API_TOKEN", serviceToken);
  });
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
  const request = (query = "query=Example", headers: Record<string, string> = {}) => new Request(`${origin}/api/research/instruments?${query}`, { headers: { cookie: `${SESSION_COOKIE}=${createSession()}`, ...headers } });
  function service(result = () => Response.json(catalogue)) {
    const call = vi.fn(async (input: URL | string) => new URL(input).pathname === "/internal/auth/sessions/validate" ? Response.json({ valid: true }) : result());
    vi.stubGlobal("fetch", call);
    return call;
  }
  it("routes only the allowlisted catalogue endpoint with validated query parameters", async () => {
    const fetch = service();
    const response = await GET(request("query=Example+UK&limit=5&offset=2"), { params: Promise.resolve({ path: ["instruments"] }) });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(catalogue);
    expect(String(fetch.mock.calls.at(-1)?.[0])).toBe("https://compute.example.test/research/instruments?query=Example+UK&limit=5&offset=2");
    expect((await GET(request(), { params: Promise.resolve({ path: ["instruments", "admin"] }) })).status).toBe(404);
  });
  it("rejects unauthenticated access and malformed queries before any provider fetch", async () => {
    const fetch = service();
    expect((await proxyBackend(new Request(`${origin}/api/research/instruments?query=A`), "/research/instruments")).status).toBe(401);
    for (const query of ["query=A&url=http://127.0.0.1", "query=A&query=B", "query=", "query=A&limit=21"]) {
      expect((await proxyBackend(request(query), "/research/instruments")).status).toBe(400);
    }
    expect(fetch).not.toHaveBeenCalled();
  });
  it("refuses personal R&D search results on a production deployment", async () => {
    vi.stubEnv("MONEY_ENV", "production");
    service(() => Response.json({ ...catalogue, mode: "live_rnd", coverage: "public_provider" }));
    const response = await proxyBackend(request(), "/research/instruments");
    expect(response.status).toBe(503);
    expect(await response.text()).toContain("INSTRUMENT_SEARCH_UNAVAILABLE");
  });
  it("derives SaaS workspace context only from signed-in cookies, not caller headers", async () => {
    vi.stubEnv("MONEY_AUTH_MODE", "saas");
    const fetch = service();
    const workspace = "d45ba8d8-6032-4b97-b515-4c85c63f416f";
    const session = "a".repeat(64);
    const response = await proxyBackend(request("query=Example", { cookie: `${ACCOUNT_COOKIE}=${session}; ${WORKSPACE_COOKIE}=${workspace}`, "X-Money-Workspace": "attacker-workspace" }), "/research/instruments");
    expect(response.status).toBe(200);
    const options = (fetch.mock.calls.at(-1) as unknown as [URL, RequestInit])[1];
    expect(options.headers).toMatchObject({ "X-Money-Session": session, "X-Money-Workspace": workspace });
  });
  it.each(["invalid-json", "oversized", "synthetic-live", "provider-failure"])("bounds and sanitizes unavailable search: %s", failure => {
    service(() => failure === "invalid-json" ? new Response("not json") : failure === "oversized" ? Response.json({ content: "private".repeat(12000) }) : failure === "provider-failure" ? Response.json({ detail: serviceToken }, { status: 503 }) : Response.json({ ...catalogue, instruments: [{ ...stock, synthetic: true }] }));
    return proxyBackend(request(), "/research/instruments").then(async response => {
      expect(response.status).toBe(503);
      const body = await response.text();
      expect(body).toContain("INSTRUMENT_SEARCH_UNAVAILABLE");
      expect(body).not.toContain(serviceToken);
      expect(body).not.toContain("private");
    });
  });
  it("only promises no usage charge for an explicit backend admission rejection", async () => {
    const submit = () => new Request(`${origin}/api/research`, { method: "POST", headers: { cookie: `${SESSION_COOKIE}=${createSession()}`, origin, "Content-Type": "application/json" }, body: JSON.stringify({ ticker: stock.ticker }) });
    service(() => Response.json({ code: "INSTRUMENT_NOT_RESEARCHABLE", detail: serviceToken }, { status: 422 }));
    const rejected = await proxyBackend(submit(), "/research/jobs");
    expect(rejected.status).toBe(422);
    expect(await rejected.text()).toContain("No research job or usage charge was created");
    service(() => { throw new Error("lost response"); });
    expect(await (await proxyBackend(submit(), "/research/jobs")).text()).not.toContain("No research job or usage charge");
  });
});
