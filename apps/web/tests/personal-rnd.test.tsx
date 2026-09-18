import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { NextRequest } from "next/server";
import { PersonalResearchDetail, PersonalRndNotice } from "@/components/personal-rnd";
import { AccountForm } from "@/components/account";
import { Workspace } from "@/components/workspace";
import { JobList } from "@/components/primitives";
import { parsePersonalRnd } from "@/lib/personal-rnd";
import { parseInstrumentSearch, instrumentStatus } from "@/lib/instruments";
import { authConfigured, sameOrigin, sessionCookie } from "@/lib/auth";
import { commercialProxy } from "@/lib/commercial";
import { serviceEndpoint } from "@/lib/service";
import { proxy } from "@/proxy";
import type { Job } from "@/lib/contracts";

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

const rnd = {
  research_id: "5f45707b-243b-4c38-9c08-0e9bdc6cd926", ticker: "NVDA", issued_at: "2026-09-16T12:00:00Z",
  runtime: "live_rnd", purpose: "PERSONAL_RND", final_state: "INSUFFICIENT_EVIDENCE", signal: null,
  snapshot_id: "rnd-snapshot-1", snapshot_hash: "a".repeat(64),
  limitations: ["Public data may be delayed. Commercial usage rights are not qualified."],
  reasons: ["No production model or validation runtime is available."],
  components: [{ component: "TradingAgents", status: "MISSING_RUNTIME", reason: "Native runtime unavailable; no firm report was produced." }],
  analysis: { observations: 5, first_close: 100, last_close: 105, raw_currency: "USD", price_change_fraction: 0.05 },
};
const job: Job = { id: rnd.research_id, ticker: rnd.ticker, status: "COMPLETE", current_stage: "COMPLETE", created_at: rnd.issued_at, updated_at: rnd.issued_at, research_kind: "live_rnd", packet: rnd };
const search = { mode: "live_rnd", coverage: "public_provider", total: 1, limit: 10, offset: 0, instruments: [{ instrument_id: "NVDA", ticker: "NVDA", company: "NVIDIA Corporation", currency: "USD", exchange: "NASDAQ", eligibility: "UNKNOWN", research_allowed: true, synthetic: false, verified_at: rnd.issued_at, canonical_symbol: "NVDA", provider_symbol: "NVDA", country: "US", instrument_type: "STOCK" }] };

describe("explicit live personal R&D contracts", () => {
  it("allows public-data collection without falsely certifying research eligibility or currency", () => {
    const result = parseInstrumentSearch(search);
    expect(result.mode).toBe("live_rnd");
    expect(result.instruments[0]).toMatchObject({ research_mode: "live_rnd", eligibility: "UNKNOWN", currency: "USD", research_allowed: true });
    expect(instrumentStatus(result.instruments[0])).toContain("Research eligibility unverified");
    expect(instrumentStatus(result.instruments[0])).toContain("public-data collection only");
    expect(() => parseInstrumentSearch({ ...search, mode: "live", coverage: "reviewed_catalogue" })).toThrow();
    expect(() => parseInstrumentSearch({ ...search, mode: "live", coverage: "public_provider" })).toThrow();
  });
  it("never substitutes synthetic records or accepts unbounded provider metadata", () => {
    for (const update of [{ synthetic: true }, { instrument_id: "DEMO.L", ticker: "DEMO.L" }, { provider_symbol: "x".repeat(101) }]) {
      expect(() => parseInstrumentSearch({ ...search, instruments: [{ ...search.instruments[0], ...update }] })).toThrow();
    }
  });
  it("projects known numeric observations while retaining the raw USD currency", () => {
    const parsed = parsePersonalRnd({ ...rnd, analysis: { ...rnd.analysis, raw_html: "SECRET HTML" }, provider_token: "secret" });
    expect(parsed?.analysis.raw_currency).toBe("USD");
    expect(JSON.stringify(parsed)).not.toContain("SECRET HTML");
    expect(JSON.stringify(parsed)).not.toContain("provider_token");
    const split = parsePersonalRnd({ ...rnd, analysis: { ...rnd.analysis, price_change_fraction: null, split_events_present: true, return_semantics: "Not total return or backtest performance" } });
    expect(split?.analysis.split_events_present).toBe(true);
    expect(split?.analysis.price_change_fraction).toBeUndefined();
    expect(split?.analysis.return_semantics).toBe("Not total return or backtest performance");
  });
  it.each([{ signal: { state: "WATCH" } }, { final_state: "RESEARCH_CANDIDATE" }, { runtime: "demo" }, { purpose: "PRODUCTION" }, { snapshot_hash: "bad" }, { analysis: { raw_currency: "USD", last_close: Infinity } }, { components: [{ component: "Qlib", status: "DONE", reason: "x".repeat(1001) }] }])("rejects mislabelled or unsafe R&D records: %j", update => {
    expect(parsePersonalRnd({ ...rnd, ...update })).toBeNull();
  });
  it("renders truthful statuses and source provenance, never the production firm barrier or raw articles", () => {
    const html = renderToStaticMarkup(<PersonalResearchDetail job={job} evidence={{ snapshot: { private_payload: "DO NOT DISPLAY" }, evidence: [{ id: "evidence-1", provider: "Public provider", kind: "market", content_hash: "b".repeat(64), retrieval_time: rnd.issued_at, source_url: "https://example.test/source", payload: { raw_html: "<script>DO NOT DISPLAY</script>" } }] }} evidenceError={null} onRefresh={() => {}} />);
    expect(html).toContain("R&amp;D / PERSONAL USE");
    expect(html).toContain("USD");
    expect(html).toContain("Missing runtime");
    expect(html).toContain("No production signal was generated");
    expect(html).toContain('href="https://example.test/source"');
    expect(html).not.toContain("DO NOT DISPLAY");
    expect(html).not.toContain("First-pass reports are locked");
    expect(html).not.toContain("Decision packet");
  });
  it("shows failed collection distinctly, escapes evidence and rejects unsafe source links", () => {
    const html = renderToStaticMarkup(<PersonalResearchDetail job={{ ...job, status: "FAILED", error_message: "Public provider unavailable", packet: null }} evidence={{ snapshot: null, evidence: [{ provider: "<script>bad</script>", source_url: "javascript:alert(1)" }] }} evidenceError={null} onRefresh={() => {}} />);
    expect(html).toContain("Public provider unavailable");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("<script>");
  });
  it("labels the environment and preserves verified-email/private-workspace onboarding choices", () => {
    const banner = renderToStaticMarkup(<PersonalRndNotice />);
    expect(banner).toContain("possibly delayed");
    expect(banner).toContain("Not commercially licensed");
    const shell = renderToStaticMarkup(<Workspace view={["jobs"]} personalRnd />);
    expect(shell).toContain("Personal public-data R&amp;D");
    expect(shell).not.toContain("UK · GBP / GBX");
    expect(renderToStaticMarkup(<JobList jobs={[job]} />)).toContain("R&amp;D / PERSONAL USE");
    expect(renderToStaticMarkup(<AccountForm page="verify-email" enabled />)).toContain("Code from your email");
    expect(renderToStaticMarkup(<AccountForm page="signup" enabled={false} />)).toContain("Open the existing private workspace");
  });
});

describe("hosted development has production-strength transport/session protections", () => {
  function hosted() {
    vi.stubEnv("MONEY_ENV", "development");
    vi.stubEnv("MONEY_DEPLOYMENT_ENV", "hosted");
    vi.stubEnv("MONEY_AUTH_MODE", "private");
    vi.stubEnv("MONEY_WEB_PASSWORD", "a-unique-long-personal-passphrase");
    vi.stubEnv("SESSION_SECRET", "a-long-test-session-signing-secret-12345");
    vi.stubEnv("RESEARCH_API_TOKEN", "long-test-control-api-token-server-only");
    vi.stubEnv("RESEARCH_API_URL", "https://api.example.test");
  }
  it("forces Secure cookies and same-origin HTTPS even when MONEY_ENV is development", () => {
    hosted();
    expect(sessionCookie("opaque", new Request("http://localhost/"))).toContain("Secure");
    expect(sameOrigin(new Request("http://localhost/api/session", { headers: { Origin: "http://localhost" } }))).toBe(false);
    expect(sameOrigin(new Request("https://money.example.test/api/session", { headers: { Origin: "https://money.example.test" } }))).toBe(true);
    expect(sameOrigin(new Request("https://money.example.test/api/session", { headers: { Origin: "https://attacker.example" } }))).toBe(false);
  });
  it("refuses insecure backend URLs, default passwords and invalid deployment configuration", () => {
    hosted();
    expect(authConfigured()).toBe(true);
    vi.stubEnv("RESEARCH_API_URL", "http://localhost:8000");
    expect(() => serviceEndpoint("/health")).toThrow();
    vi.stubEnv("MONEY_WEB_PASSWORD", "password123456789!");
    expect(authConfigured()).toBe(false);
    vi.stubEnv("MONEY_DEPLOYMENT_ENV", "unknown");
    expect(authConfigured()).toBe(false);
  });
  it("sets HSTS and never enables unsafe dev CSP on hosted R&D", () => {
    hosted();
    vi.stubEnv("NODE_ENV", "development");
    const response = proxy(new NextRequest("https://money.example.test/"));
    expect(response.headers.get("strict-transport-security")).toBe("max-age=31536000");
    expect(response.headers.get("content-security-policy")).not.toMatch(/unsafe-(inline|eval)/);
  });
  it("does not bypass account verification when the backend rejects unverified login", async () => {
    hosted();
    vi.stubEnv("MONEY_AUTH_MODE", "saas");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ code: "EMAIL_UNVERIFIED" }, { status: 401 })));
    const response = await commercialProxy(new Request("https://money.example.test/api/account/login", { method: "POST", headers: { Origin: "https://money.example.test", "Content-Type": "application/json" }, body: JSON.stringify({ email: "researcher@example.test", password: "long-example-password" }) }), "account", "login");
    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});
