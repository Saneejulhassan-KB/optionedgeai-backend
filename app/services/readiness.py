"""
Strategy-aware market readiness.

DATA_AVAILABLE and TRADING_READY are deliberately different things. `can_trade`
turns true only when every strategy-critical requirement is independently
satisfied: verified history, a reconstructed session, warmed indicators, usable
structure, a live feed and fresh data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.market_session import (
    SessionPhase,
    candle_open,
    ensure_utc,
    expected_candle_opens,
    session_phase,
    to_ist,
)
from app.core.timeframes import parse_timeframe_list, resolve_timeframe
from app.models.candle import Candle
from app.models.historical_coverage import STATUS_COMPLETE
from app.repositories.candle_repository import CandleRepository
from app.services.candle_reconciler import finalize_due_candles
from app.services.indicators import READY, IndicatorSnapshot, compute_indicators
from app.services.market_structure import MarketStructure, compute_structure

logger = logging.getLogger(__name__)

# Indicators the trading engine cannot operate without. VWAP is excluded on
# purpose: index feeds carry no volume, so it is informational only.
CRITICAL_INDICATORS = ("ema9", "ema21", "ema50", "ema200", "rsi", "atr")

MIN_STRUCTURE_BARS = 50

STATUS_INITIALIZING = "INITIALIZING"
STATUS_HISTORICAL_LOADING = "HISTORICAL_LOADING"
STATUS_TODAY_LOADING = "TODAY_LOADING"
STATUS_CALCULATING = "CALCULATING"
STATUS_DEGRADED = "DEGRADED"
STATUS_READY = "READY"


def trading_timeframes() -> list[str]:
    return [t.id for t in parse_timeframe_list(get_settings().trading_timeframes)]


@dataclass
class TimeframeContext:
    """Everything readiness needs to judge one instrument/timeframe."""

    instrument_key: str
    timeframe: str
    candles: list[Candle]
    indicators: IndicatorSnapshot
    structure: MarketStructure
    coverage_status: str
    stored_candle_count: int
    suspicious_gap_count: int
    today_ready: bool
    today_missing_candles: int
    data_age_seconds: float | None

    @property
    def active_bars(self) -> int:
        return len(self.candles)

    @property
    def indicators_ready(self) -> bool:
        points = self.indicators.as_dict()
        return all(points[name].status == READY for name in CRITICAL_INDICATORS)

    @property
    def structure_ready(self) -> bool:
        return (
            self.active_bars >= MIN_STRUCTURE_BARS
            and self.structure.structure_state not in {"NO_DATA", "WARMING_UP"}
        )

    @property
    def history_ready(self) -> bool:
        return self.coverage_status == STATUS_COMPLETE

    @property
    def ready(self) -> bool:
        return (
            self.history_ready
            and self.today_ready
            and self.indicators_ready
            and self.structure_ready
            and self.suspicious_gap_count == 0
        )

    def indicator_flags(self) -> dict[str, bool]:
        points = self.indicators.as_dict()
        return {
            "ema9_ready": points["ema9"].status == READY,
            "ema21_ready": points["ema21"].status == READY,
            "ema50_ready": points["ema50"].status == READY,
            "ema200_ready": points["ema200"].status == READY,
            "rsi_ready": points["rsi"].status == READY,
            "atr_ready": points["atr"].status == READY,
            "vwap_ready": points["vwap"].status == READY,
            "structure_ready": self.structure_ready,
        }

    def as_dict(self) -> dict[str, Any]:
        points = self.indicators.as_dict()
        return {
            "timeframe": self.timeframe,
            "ready": self.ready,
            "coverage_status": self.coverage_status,
            "history_ready": self.history_ready,
            "today_ready": self.today_ready,
            "today_missing_candles": self.today_missing_candles,
            "active_bars": self.active_bars,
            "stored_candles": self.stored_candle_count,
            "suspicious_gap_count": self.suspicious_gap_count,
            "data_age_seconds": self.data_age_seconds,
            "indicators": {
                name: {
                    "value": point.value,
                    "status": point.status,
                    "required_bars": point.required_bars,
                    "available_bars": point.available_bars,
                }
                for name, point in points.items()
            },
            **self.indicator_flags(),
        }


@dataclass
class ReadinessReport:
    status: str
    can_trade: bool
    reason: str
    flags: dict[str, bool] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    timeframes: dict[str, dict[str, Any]] = field(default_factory=dict)
    feed: dict[str, Any] = field(default_factory=dict)
    session: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "can_trade": self.can_trade,
            "reason": self.reason,
            "session_phase": self.session,
            "flags": self.flags,
            "blockers": self.blockers,
            "timeframes": self.timeframes,
            "websocket": self.feed,
        }


def build_timeframe_context(
    db: Session,
    *,
    instrument_key: str,
    timeframe: str,
    now: datetime | None = None,
) -> TimeframeContext:
    """Load the active window for one series and derive indicators/structure."""
    settings = get_settings()
    spec = resolve_timeframe(timeframe)
    current = ensure_utc(now or datetime.now(timezone.utc))
    repo = CandleRepository(db)

    # A candle must never stay forming past its boundary, even on a read path.
    finalize_due_candles(
        db, instrument_key=instrument_key, timeframe=spec.id, now=current
    )

    lookback = max(MIN_STRUCTURE_BARS, int(settings.active_candle_lookback))
    candles = repo.get_range(
        instrument_key=instrument_key, timeframe=spec.id, limit=lookback
    )
    daily = repo.get_range(instrument_key=instrument_key, timeframe="1D", limit=30)

    cov = repo.get_or_create_coverage(instrument_key=instrument_key, timeframe=spec.id)
    indicators = compute_indicators(
        candles, warmup_multiplier=float(settings.indicator_warmup_multiplier)
    )
    structure = compute_structure(candles, daily=daily)

    data_age = None
    if candles:
        data_age = (current - ensure_utc(candles[-1].timestamp)).total_seconds()

    today_ready, missing = _today_session_state(
        repo,
        instrument_key=instrument_key,
        spec=spec,
        now=current,
    )

    return TimeframeContext(
        instrument_key=instrument_key,
        timeframe=spec.id,
        candles=candles,
        indicators=indicators,
        structure=structure,
        coverage_status=cov.status,
        stored_candle_count=cov.candle_count,
        suspicious_gap_count=int(cov.suspicious_gap_count or 0),
        today_ready=today_ready,
        today_missing_candles=missing,
        data_age_seconds=data_age,
    )


def _today_session_state(
    repo: CandleRepository,
    *,
    instrument_key: str,
    spec,
    now: datetime,
) -> tuple[bool, int]:
    """
    Has today's session been reconstructed up to the current interval?

    The candle currently forming is allowed to be absent (no tick yet); every
    earlier interval of the session must exist.
    """
    phase = session_phase(now)
    if spec.unit not in {"minutes", "hours"}:
        return True, 0
    if phase.phase in {"PRE_OPEN", "NON_TRADING_DAY"}:
        return True, 0

    expected = expected_candle_opens(phase.trading_day, spec)
    if phase.phase == "OPEN":
        active_open = candle_open(now, spec)
        expected = [ts for ts in expected if ts < active_open]
    if not expected:
        return True, 0

    stored = set(
        repo.get_timestamps(
            instrument_key=instrument_key,
            timeframe=spec.id,
            from_ts=expected[0],
            to_ts=expected[-1],
        )
    )
    missing = [ts for ts in expected if ts not in stored]
    return not missing, len(missing)


def evaluate_readiness(
    contexts: dict[str, TimeframeContext],
    *,
    feed: dict[str, Any] | None = None,
    now: datetime | None = None,
    require_market_open: bool = True,
) -> ReadinessReport:
    """Combine per-timeframe context and live feed health into one verdict."""
    current = ensure_utc(now or datetime.now(timezone.utc))
    phase: SessionPhase = session_phase(current)
    feed = feed or {}
    settings = get_settings()

    ws_connected = bool(feed.get("connected", False))
    data_fresh = bool(feed.get("data_fresh", False))

    flags: dict[str, bool] = {}
    blockers: list[str] = []

    history_ready = all(ctx.history_ready for ctx in contexts.values())
    today_ready = all(ctx.today_ready for ctx in contexts.values())
    indicators_ready = all(ctx.indicators_ready for ctx in contexts.values())
    structure_ready = all(ctx.structure_ready for ctx in contexts.values())
    no_gaps = all(ctx.suspicious_gap_count == 0 for ctx in contexts.values())
    market_open = phase.phase == "OPEN"

    flags["historical_coverage_ready"] = history_ready
    flags["today_session_ready"] = today_ready
    flags["required_indicators_ready"] = indicators_ready
    flags["market_structure_ready"] = structure_ready
    flags["no_critical_data_gaps"] = no_gaps
    flags["websocket_connected"] = ws_connected
    flags["live_data_fresh"] = data_fresh
    flags["market_open"] = market_open

    aggregate_indicators: dict[str, bool] = {}
    for timeframe, ctx in contexts.items():
        flags[f"{timeframe}_ready"] = ctx.ready
        if not ctx.ready:
            blockers.append(_timeframe_blocker(ctx))
        for name, value in ctx.indicator_flags().items():
            flags[f"{timeframe}_{name}"] = value
            # An indicator counts as ready only where it is ready everywhere.
            aggregate_indicators[name] = aggregate_indicators.get(name, True) and value
    flags.update(aggregate_indicators)

    if not history_ready:
        blockers.append("Historical coverage is not COMPLETE")
    if not today_ready:
        blockers.append("Today's session is not fully reconstructed")
    if not indicators_ready:
        blockers.append("One or more critical indicators are not READY")
    if not structure_ready:
        blockers.append("Market structure has insufficient history")
    if not no_gaps:
        blockers.append("Suspicious candle gaps detected inside trading sessions")
    if not ws_connected:
        blockers.append(f"WebSocket feed is {feed.get('status', 'DISCONNECTED')}")
    elif not data_fresh:
        staleness = feed.get("staleness_seconds")
        blockers.append(
            "Live feed is stale"
            + (f" ({int(staleness)}s > {settings.data_stale_threshold_seconds}s)" if staleness else "")
        )
    if require_market_open and not market_open:
        blockers.append(f"Market is {phase.phase} (IST {to_ist(current).strftime('%H:%M')})")

    # De-duplicate while preserving order.
    blockers = list(dict.fromkeys(b for b in blockers if b))

    critical = [
        history_ready,
        today_ready,
        indicators_ready,
        structure_ready,
        no_gaps,
        ws_connected,
        data_fresh,
    ]
    if require_market_open:
        critical.append(market_open)
    can_trade = all(critical)

    status = _status_for(
        can_trade=can_trade,
        history_ready=history_ready,
        today_ready=today_ready,
        indicators_ready=indicators_ready and structure_ready,
        ws_connected=ws_connected,
        data_fresh=data_fresh,
        contexts=contexts,
    )

    report = ReadinessReport(
        status=status,
        can_trade=can_trade,
        reason="All critical requirements satisfied" if can_trade else blockers[0],
        flags=flags,
        blockers=blockers,
        timeframes={tf: ctx.as_dict() for tf, ctx in contexts.items()},
        feed=feed,
        session=phase.phase,
    )
    _log_if_changed(contexts, report)
    return report


_last_reported: dict[str, tuple[str, bool]] = {}


def _log_if_changed(
    contexts: dict[str, TimeframeContext], report: ReadinessReport
) -> None:
    if not contexts:
        return
    instrument = next(iter(contexts.values())).instrument_key
    current = (report.status, report.can_trade)
    if _last_reported.get(instrument) == current:
        return
    _last_reported[instrument] = current
    logger.info(
        "readiness_changed instrument=%s status=%s can_trade=%s reason=%s",
        instrument,
        report.status,
        report.can_trade,
        report.reason,
    )


def _timeframe_blocker(ctx: TimeframeContext) -> str:
    if not ctx.history_ready:
        return f"{ctx.timeframe}: coverage is {ctx.coverage_status}"
    if not ctx.today_ready:
        return f"{ctx.timeframe}: {ctx.today_missing_candles} candle(s) missing from today"
    if ctx.suspicious_gap_count:
        return f"{ctx.timeframe}: {ctx.suspicious_gap_count} suspicious gap(s)"
    if not ctx.indicators_ready:
        points = ctx.indicators.as_dict()
        pending = [
            f"{name}={points[name].status}"
            for name in CRITICAL_INDICATORS
            if points[name].status != READY
        ]
        return f"{ctx.timeframe}: indicators {', '.join(pending)}"
    if not ctx.structure_ready:
        return f"{ctx.timeframe}: structure needs >= {MIN_STRUCTURE_BARS} bars (has {ctx.active_bars})"
    return f"{ctx.timeframe}: not ready"


def _status_for(
    *,
    can_trade: bool,
    history_ready: bool,
    today_ready: bool,
    indicators_ready: bool,
    ws_connected: bool,
    data_fresh: bool,
    contexts: dict[str, TimeframeContext],
) -> str:
    if can_trade:
        return STATUS_READY
    if not contexts or all(ctx.stored_candle_count == 0 for ctx in contexts.values()):
        return STATUS_INITIALIZING
    if not history_ready:
        return STATUS_HISTORICAL_LOADING
    if not today_ready:
        return STATUS_TODAY_LOADING
    if not indicators_ready:
        return STATUS_CALCULATING
    if not ws_connected or not data_fresh:
        return STATUS_DEGRADED
    return STATUS_CALCULATING


def evaluate_instrument_readiness(
    db: Session,
    *,
    instrument_key: str,
    timeframes: list[str] | None = None,
    feed: dict[str, Any] | None = None,
    now: datetime | None = None,
    require_market_open: bool = True,
) -> tuple[ReadinessReport, dict[str, TimeframeContext]]:
    tfs = timeframes or trading_timeframes()
    contexts = {
        tf: build_timeframe_context(
            db, instrument_key=instrument_key, timeframe=tf, now=now
        )
        for tf in tfs
    }
    report = evaluate_readiness(
        contexts, feed=feed, now=now, require_market_open=require_market_open
    )
    return report, contexts
