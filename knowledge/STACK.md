# Money Research Architecture

## Product Definition

Money is an independent multi-firm investment research platform.

Money does NOT execute trades.

Money does NOT manage a Trading 212 account.

Money does NOT require access to:
- account balances
- portfolio positions
- order history
- order placement
- order cancellation
- broker execution

The user remains responsible for all investment decisions and executes trades manually.

Money produces research signals, supporting evidence, potential entry/exit ranges, risks, invalidation conditions, and independent research consensus.

---

## User Investment Mandate

Initial mandate:

- Broker universe: Trading 212 Stocks & Shares ISA
- Maximum assumed investment capital: £200
- Instruments: individual stocks
- Quote currency: GBP or GBX
- Exclude defence/weapons businesses
- Exclude oil exploration/production/refining/services businesses
- Primary research horizon: approximately 1-30 days
- £1,000 profit objective is aspirational only and must never override risk controls

Trading 212 is used only to determine whether an instrument is available within the user's Stocks & Shares ISA mandate.

---

## Trading 212

Role:

Eligibility and universe verification only.

Money should expose an abstraction conceptually similar to:

Trading212EligibilityService

Responsibilities:

- determine whether stock is available in Trading 212 Stocks & Shares ISA
- determine whether instrument is currently available to buy
- determine instrument type
- determine quote currency
- provide instrument metadata necessary for eligibility filtering

Trading 212 must NOT be used for:

- account balances
- portfolio state
- current positions
- order placement
- order cancellation
- trade execution
- automated portfolio management

Money must never place, prepare, submit, modify, or cancel Trading 212 orders.

---

# Research Organisations

## TradingAgents — Independent Research Firm A

TradingAgents operates as an independent trading/investment research firm.

Its internal specialist agents may include:

- technical analyst
- fundamental analyst
- market analyst
- news analyst
- sentiment analyst
- bull researcher
- bear researcher
- risk analyst
- portfolio/trading decision analyst

TradingAgents must reach its initial conclusion independently.

It must not see conclusions from:

- ai-hedge-fund
- Qlib
- LEAN
- CrewAI

before its report is locked.

Output:

TradingAgentsResearchReport

---

## ai-hedge-fund — Independent Research Firm B

ai-hedge-fund is a separate independent investment research organisation.

Its internal specialist agents may include:

- value analyst
- growth analyst
- quality analyst
- fundamental analyst
- technical analyst
- macro analyst
- risk analyst
- investment committee

Money should adapt ai-hedge-fund to consume Money-owned normalized evidence rather than relying blindly on US-centric data assumptions.

ai-hedge-fund must reach its first-pass conclusion independently.

It must not see conclusions from:

- TradingAgents
- Qlib
- LEAN
- CrewAI

before its report is locked.

Output:

AIHedgeFundResearchReport

---

## Qlib — Independent Quantitative Research Firm C

Qlib acts as an independent quantitative research company.

Its specialists are primarily deterministic/statistical models rather than LLM personas.

Possible research functions:

- momentum factors
- quality factors
- value factors
- volatility factors
- liquidity factors
- machine-learning models
- ranking models
- regime models
- uncertainty estimation
- expected forward-return modelling

Qlib must not see qualitative research reports before completing its initial analysis.

Output:

QlibQuantResearchReport

---

## QuantConnect LEAN — Independent Validation Firm D

LEAN is not another voting trading agent.

LEAN acts as an external quantitative validation laboratory.

Responsibilities:

- historical validation
- walk-forward analysis
- regime testing
- realistic transaction-cost assumptions
- spread/slippage simulation
- stop-loss behaviour
- target-hit behaviour
- drawdown analysis
- robustness testing
- stress testing

LEAN should attempt to falsify proposed research hypotheses.

Outputs:

PASS
FAIL
INSUFFICIENT_EVIDENCE

plus supporting statistics.

---

# CrewAI — Chief Investment Office

CrewAI is the supervisory research organisation.

CrewAI is NOT merely a summarizer.

CrewAI acts as:

- Chief Investment Office
- due-diligence department
- independent validation team
- audit department
- risk committee
- red team

CrewAI receives locked reports only after independent first-pass research has completed.

Possible specialist agents:

- Evidence Auditor
- Fundamental Auditor
- Technical Auditor
- News/Catalyst Auditor
- Source Verification Agent
- Quant Auditor
- Qlib Auditor
- LEAN/Backtest Auditor
- Point-in-Time Auditor
- Data Quality Auditor
- Contradiction Analyst
- Evidence Independence Auditor
- Ethical Mandate Auditor
- Trading 212 Eligibility Auditor
- Red Team
- Bear Investigator
- Risk Auditor
- Consensus Chair

CrewAI must independently verify important claims instead of trusting upstream reports.

Maximum cross-examination/debate rounds should be bounded.

Unresolved material disagreement should normally result in NO TRADE / NO RESEARCH SIGNAL.

---

# Candidate Discovery

Candidate discovery must not be controlled by one scanner.

Use multiple independent discovery channels.

Conceptual design:

Trading 212 ISA universe
        |
        +--> TA-Lib technical scanner
        |
        +--> Qlib quantitative scanner
        |
        +--> catalyst/news scanner
        |
        +--> fundamental/change scanner
        |
        +--> optional specialist scanners
                 |
                 v
           UNION + DEDUPE
                 |
                 v
          Candidate shortlist

This reduces the chance that a technically weak stock with a major catalyst is never researched.

---

# Money Evidence Fabric

Money owns the canonical normalized evidence model.

Research firms should receive objective evidence such as:

- prices
- OHLCV
- volume
- company metadata
- filings
- financial statements
- Companies House data
- XBRL/iXBRL data
- announcements
- news
- catalysts
- corporate actions
- macro data
- sector information

Research firms should NOT receive other firms' conclusions during first-pass analysis.

Facts may be shared.

Opinions must remain isolated until reports are locked.

---

# Blind Research Protocol

For each shortlisted candidate:

Money ResearchSnapshot
        |
        +--> TradingAgents
        |
        +--> ai-hedge-fund
        |
        +--> Qlib
                 |
                 v
          independent analysis
                 |
        +--------+--------+
        |        |        |
      sealed   sealed   sealed
      report   report   report
        |        |        |
        +--------+--------+
                 |
                 v
                LEAN
                 |
                 v
             CrewAI CIO
                 |
                 v
       audit / verify / challenge
                 |
                 v
        evidence-based consensus

No first-pass research firm may see another firm's directional conclusion before report locking.

---

# Shared ResearchSnapshot

All independent firms should operate from the same point-in-time snapshot wherever practical.

Snapshot may include:

- ticker
- snapshot ID
- timestamp
- price cutoff
- news cutoff
- filing cutoff
- fundamental-data cutoff
- corporate-action state
- provider metadata
- source timestamps
- hashes
- normalized Money-owned evidence

This ensures disagreements reflect different analysis rather than different observation times.

---

# Consensus

Consensus is NOT majority voting.

Do not implement:

3 BUY
2 SELL
therefore BUY

Consensus should operate across independent evidence families such as:

- quantitative evidence
- technical evidence
- fundamental evidence
- catalyst evidence
- qualitative research
- historical validation
- current market regime
- red-team findings
- data quality
- evidence independence
- ethical compliance
- Trading 212 ISA eligibility

Repeated use of the same source should not count as independent confirmation.

---

# Deterministic Research Risk Gate

Money should maintain deterministic research-quality gates.

Examples:

- Trading 212 ISA eligibility
- GBP/GBX only
- ethical exclusions
- minimum data freshness
- unresolved data conflicts
- spread/liquidity constraints
- transaction-cost awareness
- point-in-time integrity
- maximum assumed user capital
- signal expiration
- minimum evidence standards

LLMs cannot override deterministic vetoes.

---

# Research Alerts

Money's final product is a research alert.

Example information:

- company
- ticker
- Trading 212 ISA availability
- currency
- current market price
- possible entry range
- thesis invalidation
- possible targets
- assumed allocation
- modelled downside
- expected holding horizon
- TradingAgents conclusion
- ai-hedge-fund conclusion
- Qlib conclusion
- LEAN validation
- CrewAI audit
- Red Team findings
- evidence independence
- calibrated confidence
- why the opportunity is interesting
- risks
- signal expiry
- research decision ID

Money stops here.

The user decides whether to trade and executes manually.

---

# TA-Lib

TA-Lib provides deterministic technical calculations and candidate discovery.

It should not make final investment decisions.

Use for:

- RSI
- MACD
- ATR
- moving averages
- momentum
- volatility
- breakouts
- volume relationships
- other deterministic technical features

---

# QuantStats

QuantStats provides outcome/performance analysis.

Use it for:

- performance analytics
- drawdowns
- risk metrics
- portfolio/research evaluation
- post-trade analysis
- calibration support

It is not a research voting agent.

---

# RD-Agent

RD-Agent operates OFFLINE only.

Purpose:

- automated factor research
- feature research
- model discovery
- hypothesis generation
- Qlib improvement

RD-Agent must not directly participate in current trade consensus.

Any discovered model/factor must pass:

- point-in-time validation
- walk-forward validation
- out-of-sample validation
- regression testing

before being promoted.

---

# stream-read-xbrl

Used for UK Companies House/XBRL financial data processing.

Supports the Money UK data/evidence layer.

---

# yfinance

Development/fallback source only.

Do not treat it as the sole authoritative production source for critical decisions.

---

# OpenBB

Optional reference/provider-routing implementation.

Not mandatory runtime infrastructure.

Money owns its normalized data model.

---

# Graphify

Graphify is development infrastructure.

Its role is code understanding and token-efficient repository navigation.

Existing Graphify indexes should be reused.

Do not broadly reread large upstream repositories when Graphify can identify relevant symbols/files.

Preferred development loop:

Graphify query
    |
identify exact symbol/file
    |
inspect minimal relevant source
    |
implement Money adapter/interface
    |
test
    |
update Money graph when appropriate

---

# System Architecture

USER RESEARCH MANDATE
        |
        v
TRADING 212 ISA ELIGIBILITY UNIVERSE
        |
        v
MONEY DATA / EVIDENCE FABRIC
        |
        v
MULTI-CHANNEL CANDIDATE DISCOVERY
        |
        v
POINT-IN-TIME RESEARCH SNAPSHOTS
        |
        +----------------+----------------+
        |                |                |
        v                v                v
 TradingAgents      ai-hedge-fund        Qlib
 Research Firm A    Research Firm B    Quant Firm C
        |                |                |
        +----------------+----------------+
                         |
                   LOCK REPORTS
                         |
                         v
               QuantConnect LEAN
                Validation Firm D
                         |
                         v
                    CrewAI CIO
                         |
         +---------------+---------------+
         |               |               |
       Audit         Independent       Red Team
                       validation
         |               |               |
         +---------------+---------------+
                         |
                         v
                 Cross Examination
                         |
                         v
                Evidence Consensus
                         |
                         v
             Deterministic Quality Gate
                         |
                  +------+------+
                  |             |
                REJECT        APPROVE
                  |             |
                  v             v
              NO SIGNAL    RESEARCH ALERT
                                  |
                                  v
                                USER
                                  |
                                  v
                         MANUAL TRADE DECISION

Money never executes the trade.
