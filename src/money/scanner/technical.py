"""Real TA-Lib calculations with explicit warmup, units, and policy components."""

import math
from importlib import import_module

from pydantic import Field, model_validator

from money.data.normalization.prices import normalize_gbp
from money.schemas.contracts import Contract, DiscoveryReason, PriceBar, ResearchSnapshot


class ScannerPolicy(Contract):
    version: str = "technical-discovery-v1"
    minimum_bars: int = Field(default=60, ge=60)
    rsi_low: float = Field(default=40, ge=0, le=100)
    rsi_high: float = Field(default=70, ge=0, le=100)
    relative_volume_minimum: float = Field(default=1.5, gt=0)
    breakout_window: int = Field(default=20, ge=5, le=50)

    @model_validator(mode="after")
    def ordered_rsi_interval(self) -> "ScannerPolicy":
        if self.rsi_low > self.rsi_high:
            raise ValueError("invalid RSI discovery interval")
        return self


class TechnicalFeatures(Contract):
    policy_version: str
    observations: int
    available: bool
    values: tuple[tuple[str, float], ...]
    discoveries: tuple[DiscoveryReason, ...]
    limitations: tuple[str, ...] = ()


def calculate_technical(
    snapshot: ResearchSnapshot, policy: ScannerPolicy | None = None
) -> TechnicalFeatures:
    policy = policy or ScannerPolicy()
    records = sorted(
        (e for e in snapshot.evidence if isinstance(e.payload, PriceBar)),
        key=lambda e: e.observation_time,
    )
    if len(records) < policy.minimum_bars:
        return TechnicalFeatures(
            policy_version=policy.version,
            observations=len(records),
            available=False,
            values=(),
            discoveries=(),
            limitations=("TA_LIB_WARMUP_INSUFFICIENT",),
        )
    if len({r.observation_time for r in records}) != len(records):
        raise ValueError("duplicate technical observation")
    if any(not r.available_at(snapshot.price_cutoff) or r.conflicting for r in records):
        raise ValueError("technical input is unsafe")
    if any(
        r.fresh_until <= snapshot.created_at
        or r.payload.currency != snapshot.instrument.quote_currency
        for r in records
        if isinstance(r.payload, PriceBar)
    ):
        raise ValueError("technical input freshness or currency mismatch")
    talib, np = import_module("talib"), import_module("numpy")
    bars = [r.payload for r in records if isinstance(r.payload, PriceBar)]
    arrays = {
        name: np.asarray(
            [float(normalize_gbp(getattr(bar, name), bar.currency).gbp) for bar in bars],
            dtype="float64",
        )
        for name in ("open", "high", "low", "close")
    }
    close, high, low = arrays["close"], arrays["high"], arrays["low"]
    macd, signal, histogram = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    values = {
        "rsi_14": float(talib.RSI(close, timeperiod=14)[-1]),
        "ema_20_gbp": float(talib.EMA(close, timeperiod=20)[-1]),
        "sma_50_gbp": float(talib.SMA(close, timeperiod=50)[-1]),
        "atr_14_gbp": float(talib.ATR(high, low, close, timeperiod=14)[-1]),
        "adx_14": float(talib.ADX(high, low, close, timeperiod=14)[-1]),
        "roc_10_percent": float(talib.ROC(close, timeperiod=10)[-1]),
        "macd_gbp": float(macd[-1]),
        "macd_signal_gbp": float(signal[-1]),
        "macd_histogram_gbp": float(histogram[-1]),
    }
    previous_volume = sum(b.volume for b in bars[-21:-1]) / 20
    if previous_volume > 0:
        values["relative_volume_20"] = bars[-1].volume / previous_volume
    if any(not math.isfinite(v) for v in values.values()):
        raise ValueError("TA-Lib returned unavailable indicator")
    ids = tuple(r.evidence_id for r in records)
    reasons = []
    if (
        close[-1] > max(high[-policy.breakout_window - 1 : -1])
        and values.get("relative_volume_20", 0) >= policy.relative_volume_minimum
    ):
        reasons.append(
            DiscoveryReason(
                channel="technical",
                reason=f"{policy.version}: close exceeds prior {policy.breakout_window}-bar high; relative volume {values['relative_volume_20']:.3f} >= {policy.relative_volume_minimum}.",
                evidence_ids=ids,
            )
        )
    if (
        close[-1] > values["ema_20_gbp"] > values["sma_50_gbp"]
        and policy.rsi_low <= values["rsi_14"] <= policy.rsi_high
        and values["macd_histogram_gbp"] > 0
    ):
        reasons.append(
            DiscoveryReason(
                channel="technical",
                reason=f"{policy.version}: close > EMA20 > SMA50, RSI14 in [{policy.rsi_low}, {policy.rsi_high}], positive MACD histogram.",
                evidence_ids=ids,
            )
        )
    return TechnicalFeatures(
        policy_version=policy.version,
        observations=len(bars),
        available=True,
        values=tuple(values.items()),
        discoveries=tuple(reasons),
    )
