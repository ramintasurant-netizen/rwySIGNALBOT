"""Screener statistik untuk gaya jangka pendek: BSJP (beli sore, jual pagi) dan BPJS (beli pagi,
jual sore).

Ini STATISTIK HISTORIS, bukan prediksi dan bukan sinyal: peringkat hanya menunjukkan saham mana yang
secara historis punya perilaku gap overnight / pergerakan intraday yang lebih konsisten, dengan
syarat likuiditas. Semua angka dari bar lengkap; tidak ada lookahead.

Definisi per simbol (lookback N sesi lengkap terakhir):
- ``overnight_pct_i`` = open_i / close_{i−1} − 1 (gap yang ditangkap BSJP: beli di close, jual di open).
- ``intraday_pct_i`` = close_i / open_i − 1 (pergerakan yang ditangkap BPJS).
- ``win_rate`` = proporsi > 0; ``avg``/``std`` dalam %; ``t_stat`` = avg / (std/√N) sebagai ukuran
  konsistensi (bukan uji statistik formal; hanya untuk mengurutkan).
- ``atr_pct`` = ATR14 / close; ``avg_value_20d`` untuk filter likuiditas; ``close_near_high_freq`` =
  frekuensi close di 30 % teratas range harian (indikasi tekanan beli menjelang tutup).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

import numpy as np

from data.providers.base import OHLCVFrame
from engine.indicators import atr as atr_indicator

Style = Literal["bsjp", "bpjs"]
DISCLAIMER = (
    "Statistik historis, bukan prediksi dan bukan sinyal. Gap overnight dapat berbalik; "
    "BSJP menanggung risiko gap turun tanpa stop loss semalam."
)


@dataclass(frozen=True, slots=True)
class ShortTermStats:
    symbol: str
    sessions: int
    avg_value_20d: Decimal
    atr_pct: Decimal
    overnight_avg_pct: Decimal
    overnight_win_rate: Decimal
    overnight_std_pct: Decimal
    overnight_t: Decimal
    overnight_worst_pct: Decimal
    intraday_avg_pct: Decimal
    intraday_win_rate: Decimal
    intraday_std_pct: Decimal
    intraday_t: Decimal
    intraday_worst_pct: Decimal
    close_near_high_freq: Decimal
    liquid: bool

    def as_dict(self) -> dict[str, object]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


def _q(x: float, places: str = "0.01") -> Decimal:
    if math.isnan(x) or math.isinf(x):
        return Decimal("0")
    return Decimal(repr(round(x, 4))).quantize(Decimal(places))


def _series_stats(values: np.ndarray) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    n = len(values)
    if n < 5:
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
    avg = float(values.mean()) * 100
    std = float(values.std(ddof=1)) * 100
    win = float((values > 0).mean()) * 100
    t = avg / (std / math.sqrt(n)) if std > 0 else 0.0
    worst = float(values.min()) * 100
    return _q(avg, "0.001"), _q(win, "0.1"), _q(std, "0.001"), _q(t), _q(worst)


def compute_short_term_stats(
    frame: OHLCVFrame, *, lookback: int = 60, min_avg_value: Decimal = Decimal("0")
) -> ShortTermStats | None:
    df = frame.frame.loc[frame.frame["complete"].astype(bool)]
    if len(df) < lookback + 15:
        return None
    window = df.iloc[-lookback:]
    prev_close = df["close"].shift(1).iloc[-lookback:]
    overnight = (window["open"].to_numpy() / prev_close.to_numpy()) - 1.0
    intraday = (window["close"].to_numpy() / window["open"].to_numpy()) - 1.0
    rng = (window["high"] - window["low"]).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        pos_in_range = np.where(rng > 0, (window["close"] - window["low"]).to_numpy() / rng, 0.5)
    near_high = float((pos_in_range >= 0.7).mean()) * 100
    atr_series = atr_indicator(df["high"], df["low"], df["close"], 14)
    atr_pct = (
        float(atr_series.iloc[-1] / df["close"].iloc[-1]) * 100
        if not np.isnan(atr_series.iloc[-1])
        else 0.0
    )
    value_col = df["value"] if "value" in df.columns else df["close"] * df["volume"]
    avg_value = Decimal(repr(round(float(value_col.iloc[-20:].mean()), 0)))
    o_avg, o_win, o_std, o_t, o_worst = _series_stats(overnight[~np.isnan(overnight)])
    i_avg, i_win, i_std, i_t, i_worst = _series_stats(intraday[~np.isnan(intraday)])
    return ShortTermStats(
        symbol=frame.symbol,
        sessions=lookback,
        avg_value_20d=avg_value,
        atr_pct=_q(atr_pct),
        overnight_avg_pct=o_avg,
        overnight_win_rate=o_win,
        overnight_std_pct=o_std,
        overnight_t=o_t,
        overnight_worst_pct=o_worst,
        intraday_avg_pct=i_avg,
        intraday_win_rate=i_win,
        intraday_std_pct=i_std,
        intraday_t=i_t,
        intraday_worst_pct=i_worst,
        close_near_high_freq=_q(near_high, "0.1"),
        liquid=avg_value >= min_avg_value,
    )


def rank_short_term(
    stats: list[ShortTermStats], style: Style, *, top: int = 5
) -> list[ShortTermStats]:
    """Urutkan: hanya yang likuid; BSJP memakai konsistensi gap overnight (t) lalu win rate;
    BPJS memakai konsistensi intraday (t) lalu win rate; tie-break simbol."""
    liquid = [s for s in stats if s.liquid]
    if style == "bsjp":
        key = lambda s: (-s.overnight_t, -s.overnight_win_rate, -s.overnight_avg_pct, s.symbol)  # noqa: E731
        liquid = [s for s in liquid if s.overnight_avg_pct > 0]
    else:
        key = lambda s: (-s.intraday_t, -s.intraday_win_rate, -s.intraday_avg_pct, s.symbol)  # noqa: E731
        liquid = [s for s in liquid if s.intraday_avg_pct > 0]
    return sorted(liquid, key=key)[:top]
