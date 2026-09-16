"""Personal-use evidence contracts, deliberately not commercial signal contracts."""

from typing import Any, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from money.schemas.contracts import Contract, Ticker, content_hash


class RndEvidence(Contract):
    evidence_id: str
    provider: str
    kind: str
    retrieval_time: AwareDatetime
    publication_time: AwareDatetime | None = None
    source_url: str | None = None
    historical_pit_verified: Literal[False] = False
    payload: dict[str, Any]
    content_hash: str = ""

    @model_validator(mode="after")
    def seal(self) -> Self:
        if self.evidence_id != content_hash({
            "provider": self.provider, "kind": self.kind, "data": self.payload,
        }):
            raise ValueError("RND_EVIDENCE_ID_MISMATCH")
        if self.publication_time and self.publication_time > self.retrieval_time:
            raise ValueError("RND_PUBLICATION_IN_FUTURE")
        value = content_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash and self.content_hash != value:
            raise ValueError("RND_EVIDENCE_HASH_MISMATCH")
        object.__setattr__(self, "content_hash", value)
        return self


class RndSnapshot(Contract):
    snapshot_id: str
    ticker: Ticker
    purpose: Literal["PERSONAL_RND"] = "PERSONAL_RND"
    created_at: AwareDatetime
    market: dict[str, Any]
    official: tuple[dict[str, Any], ...] = ()
    macro: tuple[dict[str, Any], ...] = ()
    evidence: tuple[RndEvidence, ...]
    limitations: tuple[str, ...]
    financial_source_precedence: tuple[str, ...] = (
        "Official Companies House / SEC filing facts",
        "Reviewed Git supplemental facts where available",
        "Yahoo summary fields (separate unverified fallback context only)",
    )
    hash: str = ""

    @model_validator(mode="after")
    def freeze(self) -> Self:
        from money.data.rnd_market import RndMarketSnapshot

        if self.ticker == "DEMO.L" or not self.market or not self.evidence:
            raise ValueError("RND_REAL_EVIDENCE_REQUIRED")
        market = RndMarketSnapshot.model_validate(self.market)
        if market.instrument.ticker != self.ticker or market.retrieved_at > self.created_at:
            raise ValueError("RND_MARKET_IDENTITY_OR_TIME_MISMATCH")
        if any(item.retrieval_time > self.created_at for item in self.evidence):
            raise ValueError("RND_EVIDENCE_AFTER_SNAPSHOT")
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("RND_DUPLICATE_EVIDENCE")
        expected = {("market", "yfinance", content_hash(self.market))}
        for kind, contexts in (("filings", self.official), ("macro", self.macro)):
            for context in contexts:
                expected.add((kind, context.get("provider", "official"), content_hash(context)))
        actual = {(item.kind, item.provider, content_hash(item.payload)) for item in self.evidence}
        if actual != expected:
            raise ValueError("RND_EVIDENCE_PAYLOAD_MISMATCH")
        value = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != value:
            raise ValueError("RND_SNAPSHOT_HASH_MISMATCH")
        object.__setattr__(self, "hash", value)
        return self


class RndComponent(Contract):
    component: str
    status: Literal["READY", "UNAVAILABLE", "NOT_CONFIGURED", "FAILED", "RATE_LIMITED"]
    reason: str


class RndResult(Contract):
    """A persisted evidence study is not a qualified multi-firm recommendation."""

    research_id: str
    ticker: Ticker
    runtime: Literal["live_rnd"] = "live_rnd"
    purpose: Literal["PERSONAL_RND"] = "PERSONAL_RND"
    label: Literal["R&D / PERSONAL USE"] = "R&D / PERSONAL USE"
    final_state: Literal["INSUFFICIENT_EVIDENCE"] = "INSUFFICIENT_EVIDENCE"
    signal: None = None
    issued_at: AwareDatetime
    snapshot_id: str
    snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    analysis: dict[str, Any]
    components: tuple[RndComponent, ...]
    limitations: tuple[str, ...]
    reasons: tuple[str, ...]
