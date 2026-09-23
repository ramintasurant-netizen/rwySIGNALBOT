"""Money flow PROXY dari harga & volume (data Yahoo). BUKAN data broker/asing.

Definisi (jendela ``window`` sesi lengkap terakhir, semua dari kolom IndicatorSet):
- ``cmf20`` Chaikin Money Flow 20 sesi; ``cmf_prev`` = nilai ``window`` sesi sebelumnya.
- ``obv_slope_days`` = (OBV_t − OBV_{t−window}) / rata-rata volume 20 sesi → berapa "hari volume" netto
  masuk/keluar selama jendela (ternormalisasi antar-saham; batas teoretis ±window).
- ``acc_days`` = sesi dengan close di 30 % teratas range harian DAN volume > rata-rata 20 sesi;
  ``dist_days`` = close di 30 % terbawah DAN volume > rata-rata.
- ``updown_ratio`` = Σ volume sesi naik / Σ volume sesi turun dalam jendela.
- ``price_change_pct`` selama jendela; ``quiet`` bila |Δ| ≤ ``quiet_pct`` (akumulasi belum mengangkat harga).
- ``label``: akumulasi / distribusi / netral dari skor komposit sederhana yang dilaporkan apa adanya.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

import numpy as np

from engine.indicators import IndicatorSet, to_decimal


@dataclass(frozen=True, slots=True)
class MoneyFlowStats:
    symbol: str
    window: int
    cmf20: Decimal
    cmf_prev: Decimal
    mfi14: Decimal
    obv_slope_days: Decimal
    acc_days: int
    dist_days: int
    updown_ratio: Decimal
    price_change_pct: Decimal
    quiet: bool
    score: int  # −100..100 (positif = akumulasi)
    label: str  # akumulasi | distribusi | netral

    def as_dict(self) -> dict[str, object]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


def compute_money_flow(
    ind: IndicatorSet, symbol: str, *, window: int = 10, quiet_pct: float = 5.0
) -> MoneyFlowStats | None:
    df = ind.frame
    if len(ind.usable) < window + 1:
        return None
    w = df.iloc[-window:]
    prev_close = df["close"].shift(1).iloc[-window:]
    cmf_now = float(df["cmf20"].iloc[-1])
    cmf_prev = float(df["cmf20"].iloc[-1 - window])
    mfi_now = float(df["mfi14"].iloc[-1])
    vol_avg = float(df["vol_avg20"].iloc[-1])
    if any(np.isnan(v) for v in (cmf_now, cmf_prev, mfi_now, vol_avg)) or vol_avg <= 0:
        return None
    obv_delta = float(df["obv"].iloc[-1] - df["obv"].iloc[-1 - window])
    obv_slope_days = obv_delta / vol_avg
    rng = (w["high"] - w["low"]).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        pos = np.where(rng > 0, (w["close"] - w["low"]).to_numpy() / rng, 0.5)
    hi_vol = w["volume"].to_numpy() > vol_avg
    acc_days = int(((pos >= 0.7) & hi_vol).sum())
    dist_days = int(((pos <= 0.3) & hi_vol).sum())
    ups = w["close"].to_numpy() > prev_close.to_numpy()
    downs = w["close"].to_numpy() < prev_close.to_numpy()
    up = float(w["volume"].to_numpy()[ups].sum())
    down = float(w["volume"].to_numpy()[downs].sum())
    if up > 0 and down > 0:
        updown = up / down
    elif ups.sum() == 0 or downs.sum() == 0:
        updown = 1.0  # semua sesi searah: rasio tidak informatif, dianggap netral
    else:
        updown = 9.99 if up > 0 else 0.0
    price_change = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-1 - window]) - 1) * 100
    quiet = abs(price_change) <= quiet_pct

    score = 0.0
    score += max(-30.0, min(30.0, cmf_now * 150.0))  # CMF ±0,2 → ±30
    score += max(-25.0, min(25.0, obv_slope_days / 4.0 * 25.0))  # ±4 hari volume → ±25
    score += (acc_days - dist_days) * 6.0  # ±6 per hari
    score += max(-15.0, min(15.0, (updown - 1.0) * 15.0))  # rasio 2 → +15
    score += 5.0 if cmf_now > cmf_prev else -5.0
    score_i = int(max(-100, min(100, round(score))))
    label = "akumulasi" if score_i >= 25 else "distribusi" if score_i <= -25 else "netral"
    return MoneyFlowStats(
        symbol=symbol,
        window=window,
        cmf20=to_decimal(cmf_now, 3),
        cmf_prev=to_decimal(cmf_prev, 3),
        mfi14=to_decimal(mfi_now, 1),
        obv_slope_days=to_decimal(obv_slope_days, 2),
        acc_days=acc_days,
        dist_days=dist_days,
        updown_ratio=to_decimal(updown, 2),
        price_change_pct=to_decimal(price_change, 2),
        quiet=quiet,
        score=score_i,
        label=label,
    )
