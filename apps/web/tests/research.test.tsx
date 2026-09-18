import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEFAULT_MANDATE, isTerminal, signalState, unwrapSignal, validateJobInput, type Job } from "@/lib/contracts";
import { Badge, DataRecord, FirmReportCard, JobList, safeSourceUrl } from "@/components/primitives";

describe("research mandate", () => {
  it("accepts the default and narrower capital/horizon assumptions", () => {
    expect(DEFAULT_MANDATE.account_type).toBeNull();
    expect(DEFAULT_MANDATE.quote_currencies).toEqual(["GBX"]);
    expect(validateJobInput({ ticker: "demo.l", mandate: DEFAULT_MANDATE })?.ticker).toBe("DEMO.L");
    expect(validateJobInput({ ticker: "DEMO.L", mandate: { ...DEFAULT_MANDATE, maximum_capital_gbp: "100", maximum_horizon_days: 10 } })).not.toBeNull();
  });
  it.each([
    { maximum_capital_gbp: "201" }, { maximum_capital_gbp: "NaN" }, { maximum_capital_gbp: "0" },
    { minimum_horizon_days: 0 }, { maximum_horizon_days: 31 }, { minimum_horizon_days: 20, maximum_horizon_days: 10 },
    { excluded_activities: [] }, { stretch_may_override_risk: true }, { quote_currencies: ["USD"] },
  ])("rejects weakening the mandate: %j", (change) => {
    expect(validateJobInput({ ticker: "DEMO.L", mandate: { ...DEFAULT_MANDATE, ...change } })).toBeNull();
  });
});

describe("research lifecycle presentation", () => {
  it.each(["QUEUED", "RUNNING", "FAILED", "COMPLETE"] as const)("renders %s durably linked by research ID", (status) => {
    const job: Job = { id: "e372355a-c387-46f9-a727-5c95cd1a1c04", ticker: "DEMO.L", status, current_stage: status, created_at: "2026-09-16T10:00:00Z", updated_at: "2026-09-16T10:00:00Z" };
    const html = renderToStaticMarkup(<JobList jobs={[job]} />);
    expect(html).toContain(`/research/${job.id}`);
    expect(html).toContain(status.toLowerCase().replace(/^./, (c) => c.toUpperCase()));
  });
  it("does not equate COMPLETE with a research endorsement", () => {
    expect(isTerminal("COMPLETE")).toBe(true);
    expect(isTerminal("FIRST_PASS_LOCKED")).toBe(false);
    expect(renderToStaticMarkup(<Badge value="INSUFFICIENT_EVIDENCE" />)).toContain("Insufficient evidence");
  });
  it("renders all provider content as text without executing markup", () => {
    const html = renderToStaticMarkup(<DataRecord data={{ thesis: "<script>alert('bad')</script>" }} />);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
  it("does not render a firm's opinion before the three-report barrier is locked", () => {
    const props = { name: "TradingAgents", letter: "A", description: "Independent research", report: { thesis: "A private unshared first-pass conclusion" } };
    const sealed = renderToStaticMarkup(<FirmReportCard {...props} locked={false} />);
    expect(sealed).toContain("Sealed");
    expect(sealed).not.toContain(props.report.thesis);
    const locked = renderToStaticMarkup(<FirmReportCard {...props} locked />);
    expect(locked).toContain(props.report.thesis);
  });
  it("links claims to their frozen evidence without redistributing raw documents", () => {
    const html = renderToStaticMarkup(<DataRecord evidenceHref="/research/record/evidence" data={{ evidence_ids: ["filing&1"], source_url: "https://example.test/filing", raw_content: "unlicensed raw document", raw_text: "raw private copy" }} />);
    expect(html).toContain("/research/record/evidence#evidence-filing%261");
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).not.toContain("unlicensed raw document");
    expect(html).not.toContain("raw private copy");
  });
  it.each(["javascript:alert(1)", "data:text/html,unsafe", "file:///etc/passwd", "https://user:secret@example.test/"])("rejects unsafe source links: %s", (url) => {
    expect(safeSourceUrl(url)).toBeNull();
    expect(renderToStaticMarkup(<DataRecord data={{ source_url: url }} />)).not.toContain("href=");
  });
});

describe("signal expiration", () => {
  const now = Date.parse("2026-09-16T12:00:00Z");
  const signal = { state: "RESEARCH_CANDIDATE", issued_at: "2026-09-16T10:00:00Z", valid_until: "2026-09-17T10:00:00Z" };
  it("shows an in-window setup and immediately expires at the deadline", () => {
    expect(signalState(signal, now)).toBe("RESEARCH_CANDIDATE");
    expect(signalState(signal, Date.parse(signal.valid_until))).toBe("EXPIRED");
    expect(renderToStaticMarkup(<Badge value={signalState(signal, Date.parse(signal.valid_until))} />)).toContain("Expired");
  });
  it("fails closed for missing or invalid dates, future issuance and recorded invalidations", () => {
    expect(signalState({ state: "RESEARCH_CANDIDATE" }, now)).toBe("EXPIRED");
    expect(signalState({ ...signal, valid_until: "not a date" }, now)).toBe("EXPIRED");
    expect(signalState({ ...signal, issued_at: "2027-01-01" }, now)).toBe("EXPIRED");
    expect(signalState({ ...signal, invalidated_at: "2026-09-16T11:00:00Z" }, now)).toBe("EXPIRED");
  });
  it("uses the persisted signal payload, including invalidation, rather than the row state", () => {
    const row = { job_id: "123", final_state: "RESEARCH_CANDIDATE", payload: { ...signal, invalidated_at: "2026-09-16T11:00:00Z" } };
    expect(unwrapSignal(row).research_id).toBe("123");
    expect(signalState(unwrapSignal(row), now)).toBe("EXPIRED");
  });
});
