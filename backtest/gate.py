"""Gate kelayakan produksi berbasis hasil backtest out-of-sample.

Kebijakan (threshold CONTOH, harus disepakati pemilik):
- Periode dibagi berdasar WAKTU: in-sample (awal) dan out-of-sample (akhir) dengan rasio
  ``oos_fraction``. Gate hanya menilai OOS; IS dilaporkan untuk konteks.
- Lulus bila pada OOS: trades ≥ ``min_trades``, expectancy_r > ``min_expectancy_r``,
  profit_factor ≥ ``min_profit_factor`` (PF tidak terdefinisi karena tanpa rugi DITERIMA hanya
  bila trades ≥ min_trades), max_drawdown_pct ≤ ``max_drawdown_pct``, dan tidak ada trade
  strategi yang tidak dievaluasi.
- Hasil terikat ``engine_version`` + ``config_hash`` + ``strategy_versions`` + hash data; perubahan
  material membatalkan kelayakan (dicek oleh ``bot/gates.py`` saat runtime).
- Hasil backtest tidak menjamin keuntungan masa depan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from backtest.runner import BacktestResult


@dataclass(frozen=True, slots=True)
class GateThresholds:
    min_trades: int = 30
    min_expectancy_r: Decimal = Decimal("0")
    min_profit_factor: Decimal = Decimal("1.3")
    max_drawdown_pct: Decimal = Decimal("15")
    oos_fraction: Decimal = Decimal("0.3")
    label: str = "CONTOH / BELUM DISEPAKATI"

    def __post_init__(self) -> None:
        if self.min_trades < 1 or not (Decimal("0") < self.oos_fraction < Decimal("1")):
            raise ValueError("threshold gate tidak valid")

    def as_dict(self) -> dict[str, str | int]:
        return {
            "min_trades": self.min_trades,
            "min_expectancy_r": str(self.min_expectancy_r),
            "min_profit_factor": str(self.min_profit_factor),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "oos_fraction": str(self.oos_fraction),
            "label": self.label,
        }


def split_period(
    start: date, end: date, oos_fraction: Decimal
) -> tuple[tuple[date, date], tuple[date, date]]:
    """Bagi [start, end] berdasar waktu; OOS = bagian akhir."""
    total_days = (end - start).days + 1
    if total_days < 2:
        raise ValueError("periode terlalu pendek untuk dibagi")
    oos_days = max(1, int(Decimal(total_days) * oos_fraction))
    oos_start = end - timedelta(days=oos_days - 1)
    is_end = oos_start - timedelta(days=1)
    if is_end < start:
        raise ValueError("periode in-sample kosong")
    return (start, is_end), (oos_start, end)


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    passed: bool
    checks: dict[str, tuple[bool, str]]
    thresholds: GateThresholds
    oos_result_hash: str
    in_sample_summary: dict[str, Any] | None

    @property
    def failures(self) -> list[str]:
        return [msg for ok, msg in self.checks.values() if not ok]


def evaluate_gate(
    oos: BacktestResult, thresholds: GateThresholds, *, in_sample: BacktestResult | None = None
) -> GateEvaluation:
    m = oos.metrics
    checks: dict[str, tuple[bool, str]] = {}
    checks["min_trades"] = (
        m.trades >= thresholds.min_trades,
        f"trade OOS {m.trades} {'≥' if m.trades >= thresholds.min_trades else '<'} minimum {thresholds.min_trades}",
    )
    exp_ok = m.expectancy_r is not None and m.expectancy_r > thresholds.min_expectancy_r
    checks["expectancy"] = (
        exp_ok,
        f"expectancy {m.expectancy_r}R vs minimum > {thresholds.min_expectancy_r}R",
    )
    if m.profit_factor is None:
        pf_ok = m.trades >= thresholds.min_trades and m.gross_loss == 0 and m.trades > 0
        pf_msg = f"profit factor tidak terdefinisi ({m.profit_factor_note})"
    else:
        pf_ok = m.profit_factor >= thresholds.min_profit_factor
        pf_msg = f"profit factor {m.profit_factor} vs minimum {thresholds.min_profit_factor}"
    checks["profit_factor"] = (pf_ok, pf_msg)
    checks["max_drawdown"] = (
        m.max_drawdown_pct <= thresholds.max_drawdown_pct,
        f"max drawdown {m.max_drawdown_pct}% vs batas {thresholds.max_drawdown_pct}%",
    )
    checks["strategies_covered"] = (
        bool(oos.strategy_versions),
        "versi strategi tercatat" if oos.strategy_versions else "versi strategi tidak tercatat",
    )
    passed = all(ok for ok, _ in checks.values())
    return GateEvaluation(
        passed=passed,
        checks=checks,
        thresholds=thresholds,
        oos_result_hash=oos.result_hash,
        in_sample_summary=in_sample.summary()["metrics"] if in_sample is not None else None,
    )


def gate_record(
    oos: BacktestResult, evaluation: GateEvaluation, *, now: datetime | None = None
) -> dict[str, Any]:
    """Payload untuk ``app_state.backtest_gate`` (dibaca ``bot/gates.py``)."""
    return {
        "passed": evaluation.passed,
        "evaluated_at": (now or datetime.now(UTC)).isoformat(),
        "engine_version": oos.engine_version,
        "config_hash": oos.config_hash,
        "strategy_versions": dict(oos.strategy_versions),
        "oos_period": {"start": oos.config.start.isoformat(), "end": oos.config.end.isoformat()},
        "oos_result_hash": oos.result_hash,
        "universe": list(oos.universe),
        "data": {"provider": oos.data_provider, "origin": oos.data_origin},
        "thresholds": evaluation.thresholds.as_dict(),
        "checks": {k: {"ok": ok, "detail": msg} for k, (ok, msg) in evaluation.checks.items()},
        "metrics": {
            "trades": oos.metrics.trades,
            "win_rate_pct": str(oos.metrics.win_rate) if oos.metrics.win_rate is not None else None,
            "expectancy_r": str(oos.metrics.expectancy_r)
            if oos.metrics.expectancy_r is not None
            else None,
            "profit_factor": str(oos.metrics.profit_factor)
            if oos.metrics.profit_factor is not None
            else None,
            "max_drawdown_pct": str(oos.metrics.max_drawdown_pct),
        },
        "disclaimer": "Hasil backtest tidak menjamin keuntungan masa depan.",
    }
