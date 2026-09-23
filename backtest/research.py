"""Riset kalibrasi yang disiplin: grid varian HANYA pada in-sample; OOS dievaluasi sekali untuk
satu varian pilihan (``--oos-for``), dan itulah hasil yang jujur.

Varian mengubah komponen yang sudah ada (bobot strategi, filter rezim, threshold, masa tahan) —
tidak ada strategi baru yang "disetel ke data". Semua varian memakai engine/lifecycle produksi
melalui ``BacktestRunner``. Data (saham + indeks) diunduh sekali dan disimpan sebagai CSV+JSON di
``var/research/cache`` agar setiap varian membaca data yang identik.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.runner import BacktestConfig, BacktestResult, BacktestRunner
from config.market_rules import MarketRules
from data.providers.base import DataOrigin, OHLCVFrame, PriceBasis, Timeframe
from engine.lifecycle import LifecycleConfig
from engine.pipeline import SignalEngine
from engine.regime import RegimeConfig
from engine.risk import RiskConfig
from engine.scorer import ScorerConfig


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    description: str
    weights: dict[str, Decimal] = field(default_factory=dict)
    threshold: int = 70
    regime_enabled: bool = True
    regime_block_neutral: bool = False
    max_hold_sessions: int = 20
    entry_valid_sessions: int = 3

    def engine(self, rules: MarketRules, risk: RiskConfig) -> SignalEngine:
        return SignalEngine(
            rules,
            risk=risk,
            scorer=ScorerConfig(weights=dict(self.weights), threshold=self.threshold),
            regime=RegimeConfig(
                enabled=self.regime_enabled, block_neutral=self.regime_block_neutral
            ),
        )

    def lifecycle(self) -> LifecycleConfig:
        return LifecycleConfig(
            entry_valid_sessions=self.entry_valid_sessions, max_hold_sessions=self.max_hold_sessions
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "weights": {k: str(v) for k, v in self.weights.items()},
            "threshold": self.threshold,
            "regime_enabled": self.regime_enabled,
            "regime_block_neutral": self.regime_block_neutral,
            "max_hold_sessions": self.max_hold_sessions,
            "entry_valid_sessions": self.entry_valid_sessions,
        }


BASE = Variant("base", "konfigurasi saat ini (filter rezim aktif, semua strategi bobot 1)")

# Grid kecil dan bermotivasi (bukan pencarian buta): setiap varian menguji satu hipotesis.
DEFAULT_VARIANTS: tuple[Variant, ...] = (
    BASE,
    replace(
        BASE, name="no_regime", description="tanpa filter rezim (pembanding)", regime_enabled=False
    ),
    replace(
        BASE,
        name="block_neutral",
        description="rezim: netral juga ditahan",
        regime_block_neutral=True,
    ),
    replace(
        BASE,
        name="no_breakout",
        description="breakout dinonaktifkan (negatif konsisten IS & OOS)",
        weights={"breakout": Decimal("0")},
    ),
    replace(
        BASE,
        name="no_breakout_reversal",
        description="breakout & reversal dinonaktifkan",
        weights={"breakout": Decimal("0"), "reversal": Decimal("0")},
    ),
    replace(
        BASE,
        name="hold10",
        description="masa tahan 10 sesi (memotong posisi menggantung)",
        max_hold_sessions=10,
    ),
    replace(BASE, name="thr80", description="threshold confidence 80", threshold=80),
    replace(
        BASE,
        name="combo",
        description="no_breakout + block_neutral + hold10",
        weights={"breakout": Decimal("0")},
        regime_block_neutral=True,
        max_hold_sessions=10,
    ),
)


# ----------------------------------------------------------------------------- cache data


def cache_dir(var_dir: Path) -> Path:
    path = var_dir / "research" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_frame(frame: OHLCVFrame, folder: Path) -> Path:
    path = folder / f"{frame.symbol.replace('^', 'IDX_')}.csv"
    df = frame.frame.copy()
    df["session_date"] = df["session_date"].astype(str)
    df.index.name = "ts_utc"
    df.to_csv(path, date_format="%Y-%m-%dT%H:%M:%S%z")
    meta = {
        "symbol": frame.symbol,
        "provider": frame.provider,
        "fetched_at": frame.fetched_at.isoformat(),
        "price_basis": frame.price_basis.value,
        "origin": frame.origin.value,
    }
    path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def load_frame(path: Path) -> OHLCVFrame:
    df = pd.read_csv(path, index_col="ts_utc")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
    df["session_date"] = [date.fromisoformat(s) for s in df["session_date"]]
    df["complete"] = df["complete"].astype(bool)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype("float64")
    meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    return OHLCVFrame(
        symbol=meta["symbol"],
        timeframe=Timeframe.D1,
        price_basis=PriceBasis(meta["price_basis"]),
        provider=meta["provider"],
        fetched_at=datetime.fromisoformat(meta["fetched_at"]),
        frame=df,
        origin=DataOrigin(meta["origin"]),
        notes=("dimuat dari cache riset",),
    )


def load_cached(folder: Path) -> tuple[dict[str, OHLCVFrame], OHLCVFrame | None]:
    frames: dict[str, OHLCVFrame] = {}
    index: OHLCVFrame | None = None
    for path in sorted(folder.glob("*.csv")):
        frame = load_frame(path)
        if frame.symbol.startswith("^"):
            index = frame
        else:
            frames[frame.symbol] = frame
    return frames, index


# ----------------------------------------------------------------------------- eksekusi grid


@dataclass(frozen=True, slots=True)
class VariantRow:
    variant: Variant
    result: BacktestResult

    def as_row(self) -> dict[str, Any]:
        m = self.result.metrics
        return {
            "variant": self.variant.name,
            "trades": m.trades,
            "win_rate_pct": m.win_rate,
            "expectancy_r": m.expectancy_r,
            "profit_factor": m.profit_factor,
            "max_drawdown_pct": m.max_drawdown_pct,
            "return_pct": m.return_pct,
            "signals": m.signals_generated,
            "config_hash": self.result.config_hash,
            "by_strategy": {k: (v["trades"], v["expectancy_r"]) for k, v in m.by_strategy.items()},
        }


def run_variants(
    variants: tuple[Variant, ...],
    frames: dict[str, OHLCVFrame],
    index_frame: OHLCVFrame | None,
    *,
    rules: MarketRules,
    risk: RiskConfig,
    cfg: BacktestConfig,
) -> list[VariantRow]:
    rows: list[VariantRow] = []
    for variant in variants:
        engine = variant.engine(rules, risk)
        runner = BacktestRunner(engine, rules, cfg, lifecycle=variant.lifecycle())
        try:
            result = runner.run(frames, index_frame=index_frame)
        except ValueError as exc:
            raise ValueError(f"varian {variant.name}: {exc}") from exc
        rows.append(VariantRow(variant, result))
    return rows


def format_table(rows: list[VariantRow]) -> str:
    header = f"{'variant':22}{'trades':>7}{'win%':>7}{'expR':>7}{'PF':>7}{'MDD%':>7}{'ret%':>7}{'sig':>5}"
    lines = [header, "-" * len(header)]
    for r in rows:
        m = r.result.metrics

        def f(v: Decimal | None) -> str:
            return "-" if v is None else f"{v}"

        lines.append(
            f"{r.variant.name:22}{m.trades:>7}{f(m.win_rate):>7}{f(m.expectancy_r):>7}{f(m.profit_factor):>7}"
            f"{f(m.max_drawdown_pct):>7}{f(m.return_pct):>7}{m.signals_generated:>5}"
        )
    return "\n".join(lines)


def now_utc() -> datetime:
    return datetime.now(UTC)
