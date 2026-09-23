"""Risk: entry zone, SL berbasis ATR/struktur, TP1–TP3, R:R, ARA/ARB, tick rounding, sizing.

Semua harga ``Decimal``. Kebijakan (dikonfirmasi Tahap 3, nilai biaya CONTOH):

- ``R = entry_high − SL`` (entry paling konservatif).
- SL = min(entry_high − k×ATR, struktur − 1 tick) → dibulatkan KE BAWAH ke fraksi harga.
- TP1 = harga terendah pada grid fraksi yang memenuhi KEDUANYA: >= entry_high + m1×R (kotor) dan
  R:R bersih setelah biaya >= ``min_rr``. TP2 = TP1 + (m2−m1)×R, TP3 = TP1 + (m3−m1)×R
  (dibulatkan ke atas).
- Entry zone di-clamp ke [ARB, ARA] sesi berikutnya (referensi = close sesi lengkap terakhir).
  TP/SL tidak di-clamp (target multi-sesi) tetapi diberi catatan bila melampaui ARA/ARB.
- Setelah rounding/clamp divalidasi ulang: ``SL < entry_low <= entry_high < TP1 <= TP2 <= TP3``
  dan R:R kotor & bersih >= ``min_rr``. Gagal → setup DIBUANG, tidak dipaksakan.
- Sizing: ``shares = floor(risk_amount / R)``, ``lots = floor(shares / lot)`` dibatasi modal;
  0 lot berarti setup tidak dapat direkomendasikan untuk modal contoh tersebut.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal

from config.market_rules import MarketRules
from config.settings import Settings
from engine.models import RiskPlan, Sizing, StrategySignal

RoundMode = Literal["down", "up", "nearest"]
_ROUNDING = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}
_MAX_REROUND = 6


class RiskRejected(Exception):  # noqa: N818 - hasil kebijakan, bukan error program
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RiskConfig:
    atr_multiple: Decimal = Decimal("1.5")
    tp_multiples: tuple[Decimal, Decimal, Decimal] = (Decimal("2"), Decimal("3"), Decimal("4"))
    min_rr: Decimal = Decimal("2.0")
    fee_buy_pct: Decimal = Decimal("0.15")  # CONTOH / BELUM TERVERIFIKASI
    fee_sell_pct: Decimal = Decimal("0.25")  # CONTOH / BELUM TERVERIFIKASI (termasuk PPh final)
    apply_fees_to_rr: bool = True
    capital_example: Decimal = Decimal("100000000")  # CONTOH
    risk_pct: Decimal = Decimal("1.0")  # CONTOH
    max_entry_zone_pct: Decimal = Decimal("3.0")  # zona entry lebih lebar dari ini ditolak

    def __post_init__(self) -> None:
        m1, m2, m3 = self.tp_multiples
        if not (Decimal("0") < m1 <= m2 <= m3):
            raise ValueError("tp_multiples harus 0 < m1 <= m2 <= m3")
        if self.min_rr <= 0 or self.atr_multiple <= 0:
            raise ValueError("min_rr dan atr_multiple harus > 0")
        if self.fee_buy_pct < 0 or self.fee_sell_pct < 0 or self.fee_sell_pct >= 100:
            raise ValueError("biaya tidak valid")
        if self.capital_example <= 0 or not (Decimal("0") < self.risk_pct <= Decimal("100")):
            raise ValueError("capital_example > 0 dan 0 < risk_pct <= 100")

    @classmethod
    def from_settings(cls, settings: Settings) -> RiskConfig:
        return cls(
            atr_multiple=settings.risk_atr_multiple,
            min_rr=settings.risk_min_rr,
            fee_buy_pct=settings.risk_fee_buy_pct,
            fee_sell_pct=settings.risk_fee_sell_pct,
            apply_fees_to_rr=settings.risk_apply_fees_to_rr,
            capital_example=settings.sizing_capital_example,
            risk_pct=settings.sizing_risk_pct,
            max_entry_zone_pct=settings.risk_max_entry_zone_pct,
        )

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "atr_multiple": str(self.atr_multiple),
            "tp_multiples": ",".join(str(m) for m in self.tp_multiples),
            "min_rr": str(self.min_rr),
            "fee_buy_pct": str(self.fee_buy_pct),
            "fee_sell_pct": str(self.fee_sell_pct),
            "apply_fees_to_rr": self.apply_fees_to_rr,
            "capital_example": str(self.capital_example),
            "risk_pct": str(self.risk_pct),
            "max_entry_zone_pct": str(self.max_entry_zone_pct),
        }


# ----------------------------------------------------------------------------- tick rounding


def round_to_tick(price: Decimal, rules: MarketRules, mode: RoundMode) -> Decimal:
    """Bulatkan ke fraksi harga; bila hasil melompat ke rentang lain, bulatkan ulang dengan
    fraksi rentang tujuan (searah) sampai stabil."""
    if price <= 0:
        raise ValueError(f"harga harus > 0, diterima {price}")
    rounding = _ROUNDING[mode]
    current = price
    for _ in range(_MAX_REROUND):
        tick = rules.tick_for(current)
        rounded = (current / tick).to_integral_value(rounding=rounding) * tick
        if rounded <= 0:
            rounded = tick
        target_tick = rules.tick_for(rounded)
        if rounded % target_tick == 0:
            return (
                rounded.quantize(target_tick) if target_tick < 1 else rounded.quantize(Decimal("1"))
            )
        current = rounded
    raise ValueError(f"pembulatan fraksi tidak konvergen untuk {price}")


def is_on_tick(price: Decimal, rules: MarketRules) -> bool:
    return price > 0 and price % rules.tick_for(price) == 0


# ----------------------------------------------------------------------------- ARA / ARB


def price_limits(reference_price: Decimal, rules: MarketRules) -> tuple[Decimal, Decimal]:
    """(ARA, ARB) untuk sesi berikutnya dari harga referensi; ARA dibulatkan ke bawah,
    ARB ke atas agar keduanya berada di dalam batas resmi."""
    band = rules.price_limit_band_for(reference_price)
    ara_raw = reference_price * (1 + band.up_pct / 100)
    arb_raw = reference_price * (1 - band.down_pct / 100)
    ara = round_to_tick(ara_raw, rules, "down")
    arb = max(round_to_tick(arb_raw, rules, "up"), rules.price_limits.min_price)
    return ara, arb


# ----------------------------------------------------------------------------- R:R


def net_rr(entry: Decimal, stop: Decimal, target: Decimal, cfg: RiskConfig) -> Decimal:
    fb, fs = cfg.fee_buy_pct / 100, cfg.fee_sell_pct / 100
    cost_in = entry * (1 + fb)
    denom = cost_in - stop * (1 - fs)
    if denom <= 0:
        return Decimal("0")
    return ((target * (1 - fs) - cost_in) / denom).quantize(Decimal("0.01"))


def gross_rr(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal:
    r = entry - stop
    if r <= 0:
        return Decimal("0")
    return ((target - entry) / r).quantize(Decimal("0.01"))


def _min_target_for_net_rr(entry: Decimal, stop: Decimal, cfg: RiskConfig) -> Decimal:
    fb, fs = cfg.fee_buy_pct / 100, cfg.fee_sell_pct / 100
    cost_in = entry * (1 + fb)
    denom = cost_in - stop * (1 - fs)
    return (cfg.min_rr * denom + cost_in) / (1 - fs)


# ----------------------------------------------------------------------------- sizing


def position_size(
    entry_high: Decimal, stop_loss: Decimal, rules: MarketRules, cfg: RiskConfig
) -> Sizing:
    risk_per_share = entry_high - stop_loss
    if risk_per_share <= 0:
        raise ValueError("entry_high harus > stop_loss")
    risk_amount = (cfg.capital_example * cfg.risk_pct / 100).quantize(Decimal("1"))
    shares_by_risk = int((risk_amount / risk_per_share).to_integral_value(rounding=ROUND_FLOOR))
    lots_by_risk = shares_by_risk // rules.lot_size
    lots_by_capital = int(
        (cfg.capital_example / (entry_high * rules.lot_size)).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    lots = max(0, min(lots_by_risk, lots_by_capital))
    shares = lots * rules.lot_size
    note = ""
    if lots == 0:
        note = "0 lot: risiko per lot melebihi anggaran risiko/modal contoh; setup tidak dapat direkomendasikan untuk modal ini"
    elif lots_by_capital < lots_by_risk:
        note = "ukuran dibatasi oleh modal tersedia, bukan oleh anggaran risiko"
    return Sizing(
        capital_example=cfg.capital_example,
        risk_pct=cfg.risk_pct,
        risk_amount=risk_amount,
        lot_size=rules.lot_size,
        lots=lots,
        shares=shares,
        notional=(entry_high * shares).quantize(Decimal("1")),
        risk_per_share=risk_per_share,
        note=note,
    )


# ----------------------------------------------------------------------------- rencana


def validate_order(plan: RiskPlan, cfg: RiskConfig) -> None:
    p = plan
    if not (p.stop_loss < p.entry_low <= p.entry_high < p.tp1 <= p.tp2 <= p.tp3):
        raise RiskRejected(
            "urutan harga tidak valid setelah pembulatan/clamp: "
            f"SL {p.stop_loss} < entry {p.entry_low}..{p.entry_high} < TP {p.tp1}/{p.tp2}/{p.tp3}"
        )
    if p.rr_tp1_gross < cfg.min_rr:
        raise RiskRejected(f"R:R kotor TP1 {p.rr_tp1_gross} < minimum {cfg.min_rr}")
    if cfg.apply_fees_to_rr and p.rr_tp1_net < cfg.min_rr:
        raise RiskRejected(f"R:R bersih TP1 {p.rr_tp1_net} < minimum {cfg.min_rr} setelah biaya")


def build_risk_plan(
    signal: StrategySignal,
    *,
    atr: Decimal,
    reference_price: Decimal,
    rules: MarketRules,
    cfg: RiskConfig,
    with_sizing: bool = True,
) -> RiskPlan:
    """Bangun rencana risiko dari hint strategi. Melempar ``RiskRejected`` bila tidak layak."""
    if atr <= 0:
        raise RiskRejected(f"ATR tidak valid: {atr}")
    if reference_price < rules.price_limits.min_price:
        raise RiskRejected(
            f"harga referensi {reference_price} di bawah harga minimum {rules.price_limits.min_price}"
        )
    notes: list[str] = []

    entry_high = round_to_tick(signal.entry_high_hint, rules, "down")
    entry_low = round_to_tick(signal.entry_low_hint, rules, "down")
    entry_low = min(entry_low, entry_high)

    ara, arb = price_limits(reference_price, rules)
    if entry_low > ara or entry_high < arb:
        raise RiskRejected(
            f"zona entry {entry_low}..{entry_high} di luar batas ARA/ARB sesi berikutnya {arb}..{ara}"
        )
    if entry_high > ara:
        notes.append(f"entry_high di-clamp dari {entry_high} ke ARA {ara}")
        entry_high = ara
    if entry_low < arb:
        notes.append(f"entry_low di-clamp dari {entry_low} ke ARB {arb}")
        entry_low = arb
    zone_pct = (entry_high - entry_low) / entry_high * 100
    if zone_pct > cfg.max_entry_zone_pct:
        raise RiskRejected(
            f"zona entry {zone_pct:.2f}% lebih lebar dari batas {cfg.max_entry_zone_pct}%"
        )

    sl_candidates = [entry_high - cfg.atr_multiple * atr]
    if signal.structure_stop_hint is not None:
        struct = signal.structure_stop_hint
        sl_candidates.append(struct - rules.tick_for(struct) if struct > 0 else struct)
    sl_raw = min(sl_candidates)
    if sl_raw <= 0:
        raise RiskRejected(f"stop loss {sl_raw} tidak valid")
    stop_loss = round_to_tick(sl_raw, rules, "down")
    if stop_loss < rules.price_limits.min_price:
        raise RiskRejected(
            f"stop loss {stop_loss} di bawah harga minimum {rules.price_limits.min_price}"
        )
    if stop_loss >= entry_low:
        raise RiskRejected(f"stop loss {stop_loss} tidak berada di bawah entry_low {entry_low}")

    r_value = entry_high - stop_loss
    m1, m2, m3 = cfg.tp_multiples
    tp1_raw = entry_high + m1 * r_value
    if cfg.apply_fees_to_rr:
        tp1_raw = max(tp1_raw, _min_target_for_net_rr(entry_high, stop_loss, cfg))
    tp1 = round_to_tick(tp1_raw, rules, "up")
    tp2 = round_to_tick(tp1 + (m2 - m1) * r_value, rules, "up")
    tp3 = round_to_tick(tp1 + (m3 - m1) * r_value, rules, "up")

    if tp1 > ara:
        notes.append(f"TP1 {tp1} di atas ARA sesi berikutnya {ara}: target multi-sesi")
    if stop_loss < arb:
        notes.append(
            f"SL {stop_loss} di bawah ARB sesi berikutnya {arb}: dapat tersentuh lewat gap"
        )

    plan = RiskPlan(
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        r_value=r_value,
        rr_tp1_gross=gross_rr(entry_high, stop_loss, tp1),
        rr_tp1_net=net_rr(entry_high, stop_loss, tp1, cfg),
        reference_price=reference_price,
        ara=ara,
        arb=arb,
        atr=atr,
        sizing=position_size(entry_high, stop_loss, rules, cfg) if with_sizing else None,
        notes=tuple(notes),
    )
    validate_order(plan, cfg)
    return plan
