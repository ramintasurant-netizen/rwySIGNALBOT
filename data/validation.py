"""Validasi kualitas data: struktur OHLCV, kesegaran, panjang histori, cross-validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

import numpy as np

from core.timeutil import to_utc
from data.providers.base import OHLCVFrame, PriceBasis, QualityStatus, Quote


@dataclass(frozen=True, slots=True)
class ValidationReport:
    status: QualityStatus
    issues: tuple[str, ...] = ()
    bars_total: int = 0
    bars_complete: int = 0
    last_complete_session: date | None = None

    @property
    def ok(self) -> bool:
        return self.status is QualityStatus.OK


def validate_ohlcv(
    frame: OHLCVFrame,
    *,
    min_bars: int,
    expected_last_session: date | None = None,
    now: datetime | None = None,
    max_age: timedelta | None = None,
) -> ValidationReport:
    """Periksa OHLCVFrame. Urutan: struktur (INVALID) → histori (MISSING) → kesegaran (STALE) → OK.

    Pemeriksaan struktur (NaN/relasi OHLC/volume) hanya berlaku untuk bar LENGKAP: bar hari berjalan
    yang belum selesai boleh parsial (mis. close kosong) karena tidak pernah dipakai engine; bar itu
    tetap dilaporkan sebagai catatan.
    """
    df_all = frame.frame
    if df_all.empty:
        return ValidationReport(QualityStatus.MISSING, ("tidak ada bar",))

    issues: list[str] = []
    if df_all.index.has_duplicates:
        issues.append(f"{int(df_all.index.duplicated().sum())} timestamp duplikat")
    if not df_all.index.is_monotonic_increasing:
        issues.append("index tidak terurut")
    df = df_all.loc[df_all["complete"].astype(bool)]
    if df.empty:
        return ValidationReport(
            QualityStatus.MISSING, ("tidak ada bar lengkap",), bars_total=len(df_all)
        )

    ohlc = df[["open", "high", "low", "close"]]
    nan_rows = int(ohlc.isna().any(axis=1).sum())
    if nan_rows:
        issues.append(f"{nan_rows} bar dengan OHLC kosong")
    vol_nan = int(df["volume"].isna().sum())
    if vol_nan:
        issues.append(f"{vol_nan} bar dengan volume kosong")

    valid = ohlc.dropna()
    if not valid.empty:
        nonpositive = int((valid <= 0).any(axis=1).sum())
        if nonpositive:
            issues.append(f"{nonpositive} bar dengan harga <= 0")
        body_low = np.minimum(valid["open"], valid["close"])
        body_high = np.maximum(valid["open"], valid["close"])
        broken = int(((valid["low"] > body_low) | (valid["high"] < body_high)).sum())
        if broken:
            issues.append(
                f"{broken} bar melanggar low <= min(open,close) <= max(open,close) <= high"
            )
    neg_vol = int((df["volume"].dropna() < 0).sum())
    if neg_vol:
        issues.append(f"{neg_vol} bar dengan volume negatif")

    complete = df
    last_complete_session = complete["session_date"].iloc[-1] if not complete.empty else None
    base = {
        "bars_total": len(df_all),
        "bars_complete": len(complete),
        "last_complete_session": last_complete_session,
    }
    if issues:
        return ValidationReport(QualityStatus.INVALID, tuple(issues), **base)

    if len(complete) < min_bars:
        return ValidationReport(
            QualityStatus.MISSING,
            (f"histori {len(complete)} bar lengkap < minimum {min_bars}",),
            **base,
        )

    if frame.timeframe.is_intraday:
        if now is None or max_age is None:
            return ValidationReport(
                QualityStatus.INVALID, ("validasi intraday membutuhkan now dan max_age",), **base
            )
        last_time = df_all.index[-1].to_pydatetime()
        age = to_utc(now) - last_time
        if age > max_age:
            return ValidationReport(
                QualityStatus.STALE,
                (
                    f"bar terakhir berumur {int(age.total_seconds() // 60)} menit > {int(max_age.total_seconds() // 60)}",
                ),
                **base,
            )
    elif expected_last_session is not None and last_complete_session is not None:
        if last_complete_session < expected_last_session:
            return ValidationReport(
                QualityStatus.STALE,
                (
                    f"sesi lengkap terakhir {last_complete_session} < sesi yang diharapkan {expected_last_session}",
                ),
                **base,
            )

    return ValidationReport(QualityStatus.OK, (), **base)


def validate_quote(quote: Quote, *, now: datetime, max_age: timedelta) -> ValidationReport:
    if quote.price <= 0:
        return ValidationReport(QualityStatus.INVALID, (f"harga {quote.price} <= 0",))
    age = to_utc(now) - to_utc(quote.market_time)
    if age > max_age:
        return ValidationReport(
            QualityStatus.STALE,
            (
                f"quote berumur {int(age.total_seconds() // 60)} menit > {int(max_age.total_seconds() // 60)}",
            ),
        )
    return ValidationReport(QualityStatus.OK)


@dataclass(frozen=True, slots=True)
class CrossValidation:
    status: Literal["ok", "suspect", "not_comparable"]
    primary: str
    secondary: str
    session_date: date | None = None
    diff_pct: Decimal | None = None
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "ok"


def cross_validate_close(
    primary: OHLCVFrame,
    secondary: OHLCVFrame,
    *,
    max_diff_pct: Decimal,
    session_date: date | None = None,
) -> CrossValidation:
    """Bandingkan close RAW dua provider pada SESI YANG SAMA. Basis/sesi berbeda = tidak sebanding."""
    names = {"primary": primary.provider, "secondary": secondary.provider}
    if primary.price_basis is not PriceBasis.RAW or secondary.price_basis is not PriceBasis.RAW:
        return CrossValidation(
            "not_comparable", reason="basis harga bukan raw pada salah satu provider", **names
        )
    if primary.symbol != secondary.symbol or primary.timeframe != secondary.timeframe:
        return CrossValidation("not_comparable", reason="simbol/timeframe berbeda", **names)

    session = session_date or primary.last_complete_session
    if session is None:
        return CrossValidation(
            "not_comparable", reason="tidak ada sesi lengkap pada provider utama", **names
        )

    def _complete_close(frame: OHLCVFrame) -> Decimal | None:
        rows = frame.frame.loc[
            (frame.frame["session_date"] == session) & frame.frame["complete"].astype(bool)
        ]
        return Decimal(repr(float(rows["close"].iloc[-1]))) if not rows.empty else None

    a, b = _complete_close(primary), _complete_close(secondary)
    if a is None or b is None:
        return CrossValidation(
            "not_comparable",
            session_date=session,
            reason=f"bar lengkap sesi {session} tidak ada di kedua provider",
            **names,
        )
    if b == 0:
        return CrossValidation(
            "not_comparable", session_date=session, reason="close pembanding nol", **names
        )
    diff = (abs(a - b) / b * 100).quantize(Decimal("0.0001"))
    if diff > max_diff_pct:
        return CrossValidation(
            "suspect",
            session_date=session,
            diff_pct=diff,
            reason=f"selisih close {diff}% > {max_diff_pct}% ({a} vs {b})",
            **names,
        )
    return CrossValidation("ok", session_date=session, diff_pct=diff, **names)
