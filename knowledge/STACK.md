# Money Core Stack

## Broker / execution universe

Trading 212 official API

Purpose:
- Stocks & Shares ISA universe
- account state
- portfolio state
- instrument metadata
- availability

Money integrates Trading 212 directly.
No third-party Trading 212 wrapper is required.

## Core research and validation

### TradingAgents
Independent qualitative multi-agent research firm.

### Qlib
Independent quantitative research and candidate gate.

### QuantConnect LEAN
Independent backtesting and execution-validation laboratory.

### Money UK Research Desk
Money-owned CrewAI research group focused on UK/LSE equities.

Sources may include:
- Companies House
- UK XBRL/iXBRL filings
- regulatory announcements
- corporate reports
- market data providers

## Deterministic analysis

### TA-Lib
Technical indicators and deterministic first-pass scanner.

### QuantStats
Portfolio analytics, risk analytics and outcome evaluation.

## Offline R&D

### Microsoft RD-Agent
Offline factor and model research.

RD-Agent does not participate directly in live trade consensus.

Candidate models discovered by RD-Agent must pass independent
walk-forward evaluation before being promoted into Qlib.

## Data

### Money Data Layer
Money owns the canonical normalized data schema.

No external data vendor is itself considered canonical.

### yfinance
Development and fallback data only.

Never treat yfinance as the sole production source for critical data.

### stream-read-xbrl
UK Government-maintained Companies House XBRL parser.

## Orchestration

### CrewAI
Flow orchestration and Money CIO / UK Research Desk.

## Development

### Graphify
Code knowledge graph used by Codex to reduce source rereading.

## Optional reference systems

### ai-hedge-fund
Reference architecture only.

### OpenBB
Optional provider/router/reference implementation.

### ixbrl-parse
Optional UK iXBRL parser/reference.

## Architecture principle

Trading 212 defines what can be bought.

Money defines the canonical schema.

Data providers supply evidence.

TA-Lib eliminates weak candidates deterministically.

Qlib performs quantitative filtering.

TradingAgents and the Money UK Research Desk independently
investigate surviving candidates.

LEAN tests proposed setups.

CrewAI audits, challenges and reconciles evidence.

The deterministic Risk Governor has final veto authority.

The user makes the trade decision.
