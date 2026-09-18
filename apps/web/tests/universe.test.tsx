import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { UniverseRecords } from "@/components/universe";
import { currentUniverse, parseUniverse } from "@/lib/universe";

const now = Date.parse("2026-09-16T12:00:00Z");
const fixture = {
  coverage: "reviewed_manifest", complete_broker_universe: false, mode: "live", evaluated_at: "2026-09-16T12:00:00Z",
  total: 25, limit: 20, offset: 0, catalogue_hash: "a".repeat(64),
  instruments: [{ instrument_id: "TEST.L", ticker: "TEST.L", company: "Synthetic UK test company", exchange: "LSE", currency: "GBX", eligibility: "VERIFIED_ELIGIBLE", research_allowed: true, verified_at: "2026-09-16T11:00:00Z", synthetic: false, instrument_type: "STOCK", source: "Reviewed fixture", provider: "fixture-provider", source_id: "fixture-proof-1", metadata_hash: "b".repeat(64), eligibility_proof_hash: "c".repeat(64), ethical_proof_hash: "d".repeat(64), verified_until: "2026-09-16T13:00:00Z" }],
};

describe("qualified stock universe display", () => {
  it("projects provenance without claiming complete broker coverage", () => {
    const page = parseUniverse({ ...fixture, secret: "hidden", instruments: [{ ...fixture.instruments[0], private_provider_payload: "hidden" }] });
    expect(JSON.stringify(page)).not.toContain("hidden");
    expect(parseUniverse(page)).toEqual(page);
    const html = renderToStaticMarkup(<UniverseRecords page={page} now={now} />);
    expect(html).toContain("25 mandate-eligible instruments");
    expect(html).toContain("not the complete Trading 212 accessible universe");
    expect(html).toContain("GBX"); expect(html).toContain("fixture-proof-1");
    expect(html).toContain("Ethical proof hash");
    expect(html).toContain("/jobs?ticker=TEST.L");
    expect(html).not.toContain("Historical eligibility record");
    expect(html).not.toContain("ISA eligibility");
    expect(html).toContain("Research eligibility verified");
  });
  it.each([{ mode: "live_rnd" }, { complete_broker_universe: true }, { catalogue_hash: "invalid" }, { offset: 10001 }])("fails closed on mislabelled coverage %j", update => {
    expect(() => parseUniverse({ ...fixture, ...update })).toThrow();
  });
  it.each([{ currency: "USD" }, { eligibility: "UNKNOWN" }, { research_allowed: false }, { synthetic: true }, { ticker: "DEMO.L", instrument_id: "DEMO.L" }, { eligibility_proof_hash: "missing" }, { instrument_type: "ETF" }])("rejects inadmissible live row %j", update => {
    expect(() => parseUniverse({ ...fixture, instruments: [{ ...fixture.instruments[0], ...update }] })).toThrow();
  });
  it("hides expired rows and their active-research links without waiting for polling", () => {
    const page = parseUniverse(fixture);
    if (page.coverage !== "reviewed_manifest") throw new Error("Fixture is not a live universe");
    expect(currentUniverse(page, now)).toHaveLength(1);
    const html = renderToStaticMarkup(<UniverseRecords page={page} now={Date.parse(fixture.instruments[0].verified_until)} />);
    expect(html).toContain("expired verification record is");
    expect(html).toContain("No current verified instruments");
    expect(html).not.toContain("/jobs?ticker=");
  });
  it("preserves historical demo/R&D records without promoting them to current availability", () => {
    const page = parseUniverse({ coverage: "previously_researched_only", instruments: [{ id: "fixture", payload: { ticker: "DEMO.L", provider: "money-demo" } }] });
    const html = renderToStaticMarkup(<UniverseRecords page={page} now={now} />);
    expect(html).toContain("Historical eligibility checks");
    expect(html).toContain("does not establish current broker membership or research eligibility");
    expect(html).toContain("DEMO.L");
    expect(html).not.toContain("ISA eligibility verified");
  });
});
