import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CrossExamination } from "@/components/cross-examination";

const hash = "a".repeat(64);
const evidenceHref = "/research/record-123/evidence";

function fixture() {
  const original = {
    claim_id: "ta:claim", family: "fundamental", classification: "FIRM_OPINION",
    statement: "The original sealed interpretation.", evidence_ids: ["filing&1"],
  };
  const challenge = {
    challenge_id: "challenge-1", author: "CIO Contradiction Analyst", respondent: "tradingagents",
    claim_id: original.claim_id, original_report_hash: hash, original_claim: original,
    question: "Does the frozen filing support the complete claim?", evidence_ids: ["filing&1"],
  };
  const invocation = {
    component: "tradingagents", provider: "configured-provider", model: "configured-model",
    prompt_version: "challenge-v1", upstream_sha: "b".repeat(40), calls: 2, runtime: "live",
    usage: { input_tokens: 100, output_tokens: 25, cost_gbp: null },
  };
  const exchange = {
    challenge, resolved: false, reason: "Independent verification has not supported the claim.",
    response: {
      challenge_id: challenge.challenge_id, respondent: challenge.respondent,
      snapshot_hash: hash, original_report_hash: hash, position: "MAINTAIN",
      explanation: "The firm maintains its interpretation.", evidence_ids: ["filing&1"], invocation,
      corrected_claim: { ...original, statement: "A later narrower interpretation." },
    },
    verification: {
      auditor: "Independent Evidence Auditor", claim_id: original.claim_id, state: "UNSUPPORTED",
      explanation: "A quotation does not support the complete original inference.", evidence_ids: ["filing&1"],
    },
    verification_invocation: {
      ...invocation, component: "crewai", runtime: "deterministic", calls: 0,
      usage: { input_tokens: 0, output_tokens: 0, cost_gbp: null },
    },
    verification_evidence_checks: [{ evidence_id: "filing&1", record_hash: hash, quoted_fact: "Recorded cash increased." }],
  };
  return {
    snapshot_id: "snapshot-123", snapshot_hash: hash,
    report_hashes: [["tradingagents", hash], ["ai_hedge_fund", hash], ["qlib", hash]],
    lean_hash: hash, initial_audit_hash: hash, hash, issued_at: "2026-09-16T12:00:00Z",
    challenges: [challenge], rounds: [{ number: 1, exchanges: [exchange] }],
    unresolved_challenge_ids: [challenge.challenge_id], material_disagreement: true,
  };
}

function render(artifact: unknown, path = evidenceHref) {
  return renderToStaticMarkup(<CrossExamination artifact={artifact} evidenceHref={path} />);
}

describe("bounded post-lock correspondence presentation", () => {
  it.each([null, undefined])("renders an honest empty state for %s", (artifact) => {
    const html = render(artifact);
    expect(html).toContain("<h2>Cross-examination</h2>");
    expect(html).toContain("No correspondence packet published");
    expect(html).toContain("Missing correspondence is not agreement");
  });

  it("separates immutable originals, challenge, later response, independent audit and sources", () => {
    const html = render(fixture());
    for (const text of ["Round 1 of at most 2", "TradingAgents", "Original sealed claim",
      "The original sealed interpretation.", "Challenge · CIO Contradiction Analyst",
      "Original position maintained", "Later revised position — original remains unchanged",
      "A later narrower interpretation.", "Independent verification", "AUDIT FINDING · UNSUPPORTED",
      "Frozen source check", "Recorded cash increased.", "Material disagreement remains"]) {
      expect(html).toContain(text);
    }
    expect(html).toContain('<details class="evidence-item" open=""><summary>Round 1');
    expect(html).toContain(`${evidenceHref}#evidence-filing%261`);
    expect(html).toContain('aria-label="Immutable original claim"');
    expect(html).toContain('aria-label="Independent verification"');
    expect(html).toContain('role="status"');
  });

  it("preserves the record and labels hashes as recorded, not browser-verified", () => {
    const packet = fixture();
    const before = JSON.stringify(packet);
    const html = render(packet);
    expect(JSON.stringify(packet)).toBe(before);
    expect(html).toContain("Packet hash (recorded)");
    expect(html).toContain("does not recalculate or independently verify cryptographic integrity");
    expect(html).not.toContain("PRODUCTION READY");
  });

  it("discloses actual provider/model/call usage and never changes unknown cost to zero", () => {
    const html = render(fixture());
    expect(html).toContain("configured-provider / configured-model");
    expect(html).toContain("<dt>Actual calls</dt><dd>2</dd>");
    expect(html).toContain("<dt>Input tokens</dt><dd>100</dd>");
    expect(html).toContain("<dt>Output tokens</dt><dd>25</dd>");
    expect(html).toContain("<dt>Actual calls</dt><dd>0</dd>");
    expect(html).toContain("Deterministic recheck; no model agreement inferred");
    expect(html).toContain("Unknown (not zero)");
    expect(html).not.toContain("£0");
  });

  it.each(["0.0123", "1E-7", "0E-8"])("preserves actual Decimal cost representation %s", (cost) => {
    const packet = fixture();
    const item = packet.rounds[0].exchanges[0];
    const response = { ...item.response, invocation: { ...item.response.invocation,
      usage: { ...item.response.invocation.usage, cost_gbp: cost } } };
    const html = render({ ...packet, rounds: [{ number: 1, exchanges: [{ ...item, response }] }] });
    expect(html).toContain(`£${cost}`);
    expect(html).not.toContain('role="alert"');
  });

  it.each([
    ["WITHDRAW", "Concession — original claim withdrawn in later correspondence"],
    ["INSUFFICIENT_EVIDENCE", "Uncertainty — insufficient evidence"],
  ])("renders %s without implying resolution", (position, label) => {
    const packet = fixture();
    packet.rounds[0].exchanges[0].response.position = position;
    expect(render(packet)).toContain(label);
    expect(render(packet)).toContain("Material disagreement remains");
  });

  it("distinguishes a contradicted finding through visible text, not colour", () => {
    const packet = fixture();
    packet.rounds[0].exchanges[0].verification.state = "CONTRADICTED";
    expect(render(packet)).toContain("AUDIT FINDING · CONTRADICTED");
  });

  it("renders missing respondents and verification without inventing agreement or usage", () => {
    const packet = fixture();
    const missing = { ...packet.rounds[0].exchanges[0], response: null, verification: null, verification_invocation: null, verification_evidence_checks: [] };
    const html = render({ ...packet, rounds: [{ number: 1, exchanges: [missing] }] });
    expect(html).toContain("No response recorded");
    expect(html).toContain("No independent verification recorded");
    expect(html).toContain("usage metadata not recorded; cost is unknown");
  });

  it("renders a bounded second round and distinguishes recorded resolution from approval", () => {
    const packet = fixture();
    const first = packet.rounds[0].exchanges[0];
    packet.rounds.push({ number: 2, exchanges: [{ ...first, resolved: true, verification: { ...first.verification, state: "VERIFIED" } }] });
    packet.material_disagreement = false;
    packet.unresolved_challenge_ids = [];
    const html = render(packet);
    expect(html).toContain("Round 2 of at most 2");
    expect(html).toContain("No unresolved challenges recorded");
    expect(html).toContain("Recorded resolved");
    expect(html).toContain("not investment approval");
  });

  it("does not hide unattempted challenges behind an empty round list", () => {
    const packet = fixture();
    packet.rounds = [];
    expect(render(packet)).toContain("Challenges remain unresolved; no agreement is inferred");
    expect(render(packet)).toContain("Unresolved challenge IDs");
  });

  it.each([
    {}, [], "not a packet", { ...fixture(), hash: "incorrect" },
    { ...fixture(), rounds: [fixture().rounds[0], { ...fixture().rounds[0], number: 2 }, { ...fixture().rounds[0], number: 3 }] },
    { ...fixture(), rounds: [{ ...fixture().rounds[0], number: 2 }] },
    { ...fixture(), material_disagreement: false },
    { ...fixture(), unresolved_challenge_ids: [] },
    { ...fixture(), challenges: Array(25).fill(fixture().challenges[0]) },
    { ...fixture(), report_hashes: [["tradingagents", hash], ["tradingagents", hash], ["qlib", hash]] },
  ])("never presents malformed or inconsistent packets as verified %#", (artifact) => {
    const html = render(artifact);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Correspondence cannot be verified for display");
    expect(html).not.toContain("No unresolved challenges recorded");
    expect(html).not.toContain("Recorded resolved");
  });

  it.each(["forged-resolution", "outside-citation", "changed-original", "demo-trace"])("rejects %s", (case_) => {
    const packet = fixture();
    const item = packet.rounds[0].exchanges[0];
    if (case_ === "forged-resolution") item.resolved = true;
    if (case_ === "outside-citation") item.verification.evidence_ids = ["not-permitted"];
    if (case_ === "changed-original") item.challenge = { ...item.challenge, question: "Replaced challenge" };
    if (case_ === "demo-trace") item.response.invocation.runtime = "demo";
    expect(render(packet)).toContain('role="alert"');
  });

  it("escapes malicious markup and excludes unknown secrets/backend URLs", () => {
    const packet = fixture();
    const item = packet.rounds[0].exchanges[0];
    item.response.explanation = '<script>attack()</script><img src="https://private.invalid/secret">';
    const html = render({ ...packet, api_key: "never-render-this-key", backend_url: "https://backend.private.invalid", raw_document: "unlicensed document text" });
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("never-render-this-key");
    expect(html).not.toContain("backend.private.invalid");
    expect(html).not.toContain("unlicensed document text");
  });

  it.each(["https://backend.private.invalid/evidence", "//evil.example/evidence", "javascript:alert(1)", "/api/private?key=secret", "/research/../evidence"])("never creates unsafe evidence links: %s", (path) => {
    const html = render(fixture(), path);
    expect(html).not.toContain("href=");
    expect(html).not.toContain(path);
    expect(html).toContain("filing&amp;1");
  });
});
