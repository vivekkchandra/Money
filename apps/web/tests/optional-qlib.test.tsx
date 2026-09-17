import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FirstPassResearchPanel } from "@/components/workspace";
import { MarketingPage } from "@/components/marketing";
import type { RecordData, Reports } from "@/lib/contracts";

const hash = "a".repeat(64);
const snapshot = { snapshot_id: "snapshot-1", hash, qlib_enabled: false };
const firms = {
  tradingagents: { firm: "tradingagents", thesis: "Private first firm conclusion" },
  ai_hedge_fund: { firm: "ai_hedge_fund", thesis: "Private second firm conclusion" },
};
const reports: Reports = { locked: true, reports: firms, artifacts: {} };
const packet = { snapshot_id: snapshot.snapshot_id, snapshot_hash: hash, qlib_enabled: false, frozen_snapshot: snapshot };
const props = { reports, packet, snapshotId: snapshot.snapshot_id, evidenceHref: "/research/record/evidence" };

describe("optional Qlib display preserves the first-pass barrier", () => {
  it("shows only actual required firms for a bound disabled-Qlib packet", () => {
    const html = renderToStaticMarkup(<FirstPassResearchPanel {...props} />);
    expect(html).toContain("First-pass reports are locked");
    expect(html).toContain(firms.tradingagents.thesis);
    expect(html).toContain(firms.ai_hedge_fund.thesis);
    expect(html).not.toContain("<h2>Qlib</h2>");
    expect(html).toContain("not run");
    expect(html).toContain("No Qlib report or quantitative qualification is claimed");
    expect(html).toContain("LEAN validation remains mandatory");
    expect(html).toContain("Missing validation is not a pass");
    expect(html).not.toContain("PRODUCTION READY");
  });

  it("can use the server-published matching snapshot before the final packet exists", () => {
    const html = renderToStaticMarkup(<FirstPassResearchPanel {...props} packet={null} snapshot={snapshot} />);
    expect(html).toContain("Qlib is disabled for this frozen snapshot");
    expect(html).toContain(firms.tradingagents.thesis);
  });

  it("never exposes opinions while the server barrier is unlocked", () => {
    const html = renderToStaticMarkup(<FirstPassResearchPanel {...props} reports={{ ...reports, locked: false }} />);
    expect(html).toContain("Independent research is sealed");
    expect(html).not.toContain(firms.tradingagents.thesis);
    expect(html).not.toContain(firms.ai_hedge_fund.thesis);
  });

  it.each([null, reports])("does not infer disabled Qlib from loading or absent reports", value => {
    const html = renderToStaticMarkup(<FirstPassResearchPanel {...props} reports={value} packet={null} />);
    expect(html).not.toContain("Qlib is disabled for this frozen snapshot");
    expect(html).toContain("A missing or loading report does not mean Qlib is disabled");
    expect(html).not.toContain(firms.tradingagents.thesis);
  });

  it.each([
    { ...packet, qlib_enabled: "false" },
    { ...packet, qlib_enabled: null },
    { ...packet, frozen_snapshot: undefined },
    { ...packet, frozen_snapshot: { ...snapshot, hash: "b".repeat(64) } },
    { ...packet, frozen_snapshot: { ...snapshot, snapshot_id: "other-snapshot" } },
    { ...packet, frozen_snapshot: { ...snapshot, qlib_enabled: true } },
  ])("keeps malformed or unbound mode declarations sealed %#", value => {
    const html = renderToStaticMarkup(<FirstPassResearchPanel {...props} packet={value as RecordData} />);
    expect(html).toContain('role="alert"');
    expect(html).not.toContain(firms.tradingagents.thesis);
    expect(html).not.toContain("Qlib is disabled");
  });

  it("rejects missing or additional first-pass identities even with a server lock", () => {
    for (const malformed of [
      { tradingagents: firms.tradingagents },
      { ...firms, qlib: { firm: "qlib", thesis: "Unconfigured Qlib opinion" } },
      { ...firms, tradingagents: { ...firms.tradingagents, firm: "qlib" } },
    ]) {
      const html = renderToStaticMarkup(<FirstPassResearchPanel {...props} reports={{ ...reports, reports: malformed }} />);
      expect(html).toContain('role="alert"');
      expect(html).not.toContain(firms.tradingagents.thesis);
      expect(html).not.toContain("Unconfigured Qlib opinion");
      expect(html).not.toContain("Qlib is disabled");
    }
  });

  it("retains the legacy enabled default and displays genuine third-firm reports", () => {
    const legacy = { snapshot_id: snapshot.snapshot_id, snapshot_hash: hash };
    const allReports = { ...firms, qlib: { firm: "qlib", thesis: "Recorded quantitative report" } };
    const html = renderToStaticMarkup(<FirstPassResearchPanel {...props} packet={legacy} reports={{ ...reports, reports: allReports }} />);
    expect(html).toContain("First-pass reports are locked");
    expect(html).toContain("Recorded quantitative report");
    expect(html).not.toContain("Qlib is disabled");
  });

  it("describes Qlib as optional and LEAN as mandatory in the public process", () => {
    const html = renderToStaticMarkup(<MarketingPage page="how-it-works" />);
    expect(html).toContain("Optional Qlib");
    expect(html).toContain("Mandatory LEAN validation");
    expect(html).not.toContain("Three independent views");
  });
});
