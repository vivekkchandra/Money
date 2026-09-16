import Link from "next/link";
import { z } from "zod";
import { EmptyState, SectionHeading } from "@/components/primitives";

const identity = z.string().min(1).max(512);
const digest = z.string().regex(/^[a-f0-9]{64}$/);
const evidenceIds = z.array(identity).max(100);
const recordedCost = z.union([
  z.string().max(128).regex(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/)
    .refine((value) => Number.isFinite(Number(value)) && Number(value) >= 0),
  z.number().nonnegative(),
]);
const respondent = z.enum(["tradingagents", "ai_hedge_fund", "qlib", "lean", "cio"]);
const claim = z.object({
  claim_id: identity,
  family: z.enum(["technical", "fundamental", "catalyst", "quantitative", "risk"]),
  statement: z.string().min(1).max(4000),
  evidence_ids: evidenceIds,
  classification: z.enum(["INFERENCE", "FIRM_OPINION"]),
});
const challenge = z.object({
  challenge_id: identity, author: z.literal("CIO Contradiction Analyst"), respondent,
  claim_id: identity.nullable(), original_report_hash: digest,
  question: z.string().min(1).max(4000), evidence_ids: evidenceIds,
  original_claim: claim.nullish(),
});
const invocation = z.object({
  component: z.enum(["tradingagents", "ai_hedge_fund", "qlib", "lean", "crewai"]),
  provider: identity, model: identity, prompt_version: identity,
  upstream_sha: z.string().regex(/^[a-f0-9]{40}$/),
  calls: z.number().int().min(0).max(2), runtime: z.enum(["live", "deterministic"]),
  usage: z.object({
    input_tokens: z.number().int().nonnegative().nullish(),
    output_tokens: z.number().int().nonnegative().nullish(),
    cost_gbp: recordedCost.nullish(),
  }),
});
const exchange = z.object({
  challenge,
  response: z.object({
    challenge_id: identity, respondent, snapshot_hash: digest, original_report_hash: digest,
    position: z.enum(["MAINTAIN", "WITHDRAW", "INSUFFICIENT_EVIDENCE"]),
    explanation: z.string().min(1).max(4000), evidence_ids: evidenceIds,
    corrected_claim: claim.nullish(), invocation: invocation.nullish(),
  }).nullish(),
  verification: z.object({
    auditor: identity, claim_id: identity.nullish(),
    state: z.enum(["VERIFIED", "UNSUPPORTED", "CONTRADICTED", "WARN"]),
    explanation: z.string().min(1).max(8000), evidence_ids: evidenceIds,
  }).nullish(),
  verification_invocation: invocation.nullish(),
  verification_evidence_checks: z.array(z.object({
    evidence_id: identity, record_hash: digest, quoted_fact: z.string().min(5).max(500),
  })).max(24).optional(),
  resolved: z.boolean(), reason: z.string().min(1).max(4000),
});
const packetSchema = z.object({
  snapshot_id: identity, snapshot_hash: digest,
  report_hashes: z.array(z.tuple([z.enum(["tradingagents", "ai_hedge_fund", "qlib"]), digest])).length(3),
  lean_hash: digest, initial_audit_hash: digest, hash: digest,
  issued_at: z.iso.datetime({ offset: true }),
  challenges: z.array(challenge).max(24),
  rounds: z.array(z.object({ number: z.number().int().min(1).max(2), exchanges: z.array(exchange).max(24) })).max(2),
  unresolved_challenge_ids: z.array(identity).max(24), material_disagreement: z.boolean(),
}).refine((packet) => {
  // Display consistency only: cryptographic integrity remains a server responsibility.
  const originals = new Map<string, string>(packet.report_hashes);
  if (originals.size !== 3) return false;
  originals.set("lean", packet.lean_hash).set("cio", packet.initial_audit_hash);
  const pending = new Map(packet.challenges.map((item) => [item.challenge_id, item]));
  if (pending.size !== packet.challenges.length) return false;
  for (const item of packet.challenges) {
    if (originals.get(item.respondent) !== item.original_report_hash
      || (item.original_claim && (item.original_claim.claim_id !== item.claim_id
        || item.original_claim.evidence_ids.some((id) => !item.evidence_ids.includes(id))))) return false;
  }
  for (const [index, round] of packet.rounds.entries()) {
    if (round.number !== index + 1 || round.exchanges.length !== pending.size) return false;
    const seen = new Set<string>();
    for (const item of round.exchanges) {
      const original = pending.get(item.challenge.challenge_id);
      if (!original || seen.has(original.challenge_id) || JSON.stringify(original) !== JSON.stringify(item.challenge)) return false;
      seen.add(original.challenge_id);
      const permitted = new Set(original.evidence_ids);
      const { response, verification } = item;
      if (response && (response.challenge_id !== original.challenge_id || response.respondent !== original.respondent
        || response.original_report_hash !== original.original_report_hash || response.snapshot_hash !== packet.snapshot_hash
        || [...response.evidence_ids, ...(response.corrected_claim?.evidence_ids ?? [])].some((id) => !permitted.has(id)))) return false;
      if (verification?.evidence_ids.some((id) => !permitted.has(id))
        || item.verification_evidence_checks?.some((check) => !permitted.has(check.evidence_id))) return false;
      if (item.resolved) {
        if (!response || response.position !== "MAINTAIN" || verification?.state !== "VERIFIED"
          || verification.claim_id !== original.claim_id || !verification.evidence_ids.some((id) => response.evidence_ids.includes(id))) return false;
        pending.delete(original.challenge_id);
      }
    }
  }
  return packet.material_disagreement === (pending.size > 0)
    && packet.unresolved_challenge_ids.length === pending.size
    && new Set(packet.unresolved_challenge_ids).size === pending.size
    && packet.unresolved_challenge_ids.every((id) => pending.has(id));
});

type Invocation = z.infer<typeof invocation>;
type Exchange = z.infer<typeof exchange>;
const firms = { tradingagents: "TradingAgents", ai_hedge_fund: "AI-Hedge-Fund", qlib: "Qlib", lean: "LEAN", cio: "CIO" };

function Citations({ ids, evidenceHref }: { ids: string[]; evidenceHref: string }) {
  const safePath = /^\/research\/[A-Za-z0-9_-]{1,128}\/evidence$/.test(evidenceHref);
  return ids.length ? <div className="citation-list" aria-label="Evidence references">{ids.map((id, index) => safePath
    ? <Link href={`${evidenceHref}#evidence-${encodeURIComponent(id)}`} key={`${id}-${index}`}>{id}</Link>
    : <span key={`${id}-${index}`}>{id}</span>)}</div> : <p className="muted">No evidence references recorded.</p>;
}

function InvocationDetails({ value, title }: { value?: Invocation | null; title: string }) {
  if (!value) return <p className="muted">{title}: usage metadata not recorded; cost is unknown.</p>;
  const cost = value.usage.cost_gbp;
  return <details className="evidence-item"><summary>{title}</summary><dl className="data-record">
    <div><dt>Runtime</dt><dd>{value.runtime === "deterministic" ? "Deterministic recheck; no model agreement inferred" : "Recorded live invocation"}</dd></div>
    <div><dt>Component</dt><dd>{value.component}</dd></div>
    <div><dt>Provider / model</dt><dd>{value.provider} / {value.model}</dd></div>
    <div><dt>Prompt version</dt><dd>{value.prompt_version}</dd></div>
    <div><dt>Upstream SHA</dt><dd className="data-text"><code>{value.upstream_sha}</code></dd></div>
    <div><dt>Actual calls</dt><dd>{value.calls}</dd></div>
    <div><dt>Input tokens</dt><dd>{value.usage.input_tokens ?? "Unknown"}</dd></div>
    <div><dt>Output tokens</dt><dd>{value.usage.output_tokens ?? "Unknown"}</dd></div>
    <div><dt>Recorded cost</dt><dd>{cost === null || cost === undefined ? "Unknown (not zero)" : `£${cost}`}</dd></div>
  </dl></details>;
}

function Correspondence({ item, evidenceHref }: { item: Exchange; evidenceHref: string }) {
  const { challenge: request, response, verification } = item;
  return <details className="evidence-item"><summary>{firms[request.respondent]} · {request.challenge_id} · {item.resolved ? "Recorded resolved" : "Unresolved"}</summary>
    <section aria-label="Immutable original claim"><h4>Original sealed claim</h4><p className="small-print">Immutable first pass — later correspondence cannot replace this statement.</p>
      {request.original_claim ? <><span className="type-label opinion">{request.original_claim.classification.replaceAll("_", " ")}</span><p className="data-text">{request.original_claim.statement}</p><Citations ids={request.original_claim.evidence_ids} evidenceHref={evidenceHref} /></> : <p className="muted">No original firm claim is included; this may be a validation or global audit challenge.</p>}
      <p className="small-print data-text">Original report hash: <code>{request.original_report_hash}</code></p>
    </section>
    <section aria-label="Challenge"><h4>Challenge · {request.author}</h4><p className="data-text">{request.question}</p><p className="small-print">Permitted evidence only; no implicit permission to cite other records.</p><Citations ids={request.evidence_ids} evidenceHref={evidenceHref} /></section>
    <section aria-label="Firm response"><h4>{firms[request.respondent]} response</h4>{response ? <>
      <strong>{response.position === "MAINTAIN" ? "Original position maintained" : response.position === "WITHDRAW" ? "Concession — original claim withdrawn in later correspondence" : "Uncertainty — insufficient evidence"}</strong>
      <p className="data-text">{response.explanation}</p><Citations ids={response.evidence_ids} evidenceHref={evidenceHref} />
      {response.corrected_claim && <div className="notice"><h5>Later revised position — original remains unchanged</h5><p className="data-text">{response.corrected_claim.statement}</p><Citations ids={response.corrected_claim.evidence_ids} evidenceHref={evidenceHref} /></div>}
      <InvocationDetails value={response.invocation} title="Response usage and provenance" />
    </> : <p className="muted">No response recorded. Unavailable responders do not imply agreement.</p>}</section>
    <section aria-label="Independent verification"><h4>Independent verification</h4>{verification ? <><span className="type-label audit">AUDIT FINDING · {verification.state}</span><p>{verification.auditor}</p><p className="data-text">{verification.explanation}</p><Citations ids={verification.evidence_ids} evidenceHref={evidenceHref} /></> : <p className="muted">No independent verification recorded. A firm cannot certify its own response.</p>}
      {(item.verification_evidence_checks ?? []).map((check, index) => <details className="evidence-item" key={`${check.evidence_id}-${index}`}><summary>Frozen source check · {check.evidence_id}</summary><blockquote className="data-text">{check.quoted_fact}</blockquote><Citations ids={[check.evidence_id]} evidenceHref={evidenceHref} /><p className="small-print data-text">Recorded source hash: <code>{check.record_hash}</code></p></details>)}
      <InvocationDetails value={item.verification_invocation} title="Verification usage and provenance" />
    </section>
    <p><strong>{item.resolved ? "Recorded resolution" : "Unresolved challenge"}:</strong> {item.reason}</p>
  </details>;
}

export function CrossExamination({ artifact, evidenceHref }: { artifact: unknown; evidenceHref: string }) {
  if (artifact === null || artifact === undefined) return <section className="panel"><SectionHeading title="Cross-examination" /><EmptyState title="No correspondence packet published" detail="Bounded challenges follow locked first-pass reports, completed validation and the initial CIO audit. Missing correspondence is not agreement." icon="lock" /></section>;
  const result = packetSchema.safeParse(artifact);
  if (!result.success) return <section className="panel"><SectionHeading title="Cross-examination" /><div className="notice error" role="alert"><strong>Correspondence cannot be verified for display.</strong><p>The recorded packet is malformed or inconsistent. No resolution is inferred; reload the research record or contact the workspace operator.</p></div></section>;
  const packet = result.data;
  return <section className="panel"><SectionHeading kicker="POST-LOCK · EVIDENCE-BASED CHALLENGE" title="Cross-examination" />
    <p>Original reports remain sealed. Correspondence is a later audit artifact, not a replacement report or an agent vote.</p>
    <p role="status"><strong>{packet.material_disagreement ? `Material disagreement remains · ${packet.unresolved_challenge_ids.length} unresolved challenge(s)` : "No unresolved challenges recorded"}</strong></p>
    <p className="small-print">At most two rounds. A recorded resolution is not investment approval and cannot override a hard research gate.</p>
    <details className="evidence-item"><summary>Recorded packet provenance</summary><dl className="data-record"><div><dt>Packet hash (recorded)</dt><dd className="data-text"><code>{packet.hash}</code></dd></div><div><dt>Snapshot</dt><dd>{packet.snapshot_id}</dd></div><div><dt>Snapshot hash</dt><dd className="data-text"><code>{packet.snapshot_hash}</code></dd></div><div><dt>Issued at</dt><dd><time dateTime={packet.issued_at}>{packet.issued_at}</time></dd></div></dl><p className="small-print">The browser displays the server-recorded hash; it does not recalculate or independently verify cryptographic integrity.</p></details>
    {packet.rounds.map((round) => <details className="evidence-item" key={round.number} open><summary>Round {round.number} of at most 2 · {round.exchanges.length} exchange(s)</summary>{round.exchanges.map((item) => <Correspondence key={item.challenge.challenge_id} item={item} evidenceHref={evidenceHref} />)}</details>)}
    {!packet.rounds.length && <p className="muted">No correspondence rounds recorded. {packet.challenges.length ? "Challenges remain unresolved; no agreement is inferred." : "The initial audit raised no challenges in this packet; this is not a new validation result."}</p>}
    {packet.unresolved_challenge_ids.length > 0 && <div className="notice"><strong>Unresolved challenge IDs</strong><ul>{packet.unresolved_challenge_ids.map((id) => <li key={id}>{id}</li>)}</ul></div>}
  </section>;
}
