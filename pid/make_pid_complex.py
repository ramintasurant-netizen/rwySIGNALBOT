#!/usr/bin/env python3
"""PID-STM-002 — P&ID siklus uap PLTU (gaya klasik hitam-putih, cukup kompleks).

Economizer → Mixer → Evaporator → Steam Drum → Superheater → HPT → IPT → LPT → Condenser → CEP →
Feedwater Tank/Deaerator → BCP → Economizer.  Run: python3 make_pid_complex.py
Output: PID-STM-002_Complex.vsdx / .svg  (A3 landscape, inch, origin kiri-bawah)
"""
import math
import os

import pidlib
from pidlib import *  # noqa: F401,F403
from pid_emit import write_vsdx, write_svg

pidlib.STYLE["mono"] = True
OUT = os.path.dirname(os.path.abspath(__file__))
NAME = "PID-STM-002_Complex"
W, H = 16.54, 11.69
R = 0.2  # instrument bubble radius


# ---------------------------------------------------------------- local symbols
def turbine(x, y, w, h, label, lpos):
    s = Shape(x, y, w, h, "Equipment", weight=1.4, fill=F_WHITE, name=label)
    s.poly([(0, h * 0.25), (w, 0), (w, h), (0, h * 0.75)], close=True, nofill=False)
    text(lpos[0], lpos[1], label, size=7, bold=True)
    return s


def pump_l(cx, cy, r, label, ldy=None):
    """Centrifugal pump, discharge to the left (flow right→left)."""
    s = Shape(cx - r, cy - r, 2 * r, 2 * r, "Equipment", weight=1.4, fill=F_WHITE, name=label.split("\n")[0])
    s.ellipse(r, r, r, r)
    s.poly([(r * 1.55, r * 0.55), (r * 0.4, r), (r * 1.55, r * 1.45)], close=True, nofill=True)
    text(cx, cy - r - (ldy or 0.1 + 0.1 * len(label.split("\n"))), label, size=6.5, bold=True)
    return s


def pump_v(cx, cy, r, label):
    """Pump on a vertical line, discharge downward."""
    s = Shape(cx - r, cy - r, 2 * r, 2 * r, "Equipment", weight=1.4, fill=F_WHITE, name=label)
    s.ellipse(r, r, r, r)
    s.poly([(r * 0.55, r * 1.55), (r, r * 0.4), (r * 1.45, r * 1.55)], close=True, nofill=True)
    ltext(cx + r + 0.06, cy, label, size=6.5, bold=True)
    return s


def power_arrow(x0, y, x1, label):
    line([(x0, y), (x1, y)], "thin", arrow=True, weight=1.6)
    ltext(x1 + 0.08, y, label, size=7, bold=True)


# ---------------------------------------------------------------- frame & title block
Shape(0.4, 0.4, W - 0.8, H - 0.8, "Annotation", weight=1.8, name="frame").rect(0, 0, W - 0.8, H - 0.8, nofill=True)
box(14.05, 10.55, 4.1, 1.3, "", fill=F_WHITE, weight=1.4)
line([(12.0, 10.75), (16.1, 10.75)], "thin", arrow=False)
line([(12.0, 10.3), (16.1, 10.3)], "thin", arrow=False)
text(14.05, 10.98, "P&ID SIKLUS UAP PLTU — BOILER (ECONOMIZER / EVAPORATOR / STEAM DRUM / SUPERHEATER),\nTURBIN HP-IP-LP, KONDENSOR & SISTEM AIR PENGISI", size=7, bold=True)
text(12.75, 10.52, "Dwg. No. PID-STM-002", size=6.5, bold=True)
text(14.2, 10.52, "Rev. A — Issued for Review", size=6.5)
text(15.5, 10.52, "A3 — NTS  |  ISA-5.1", size=6.5)
line([(12.15, 10.1), (12.75, 10.1)], "process", arrow=True); ltext(12.8, 10.1, "Proses", size=5.5)
line([(13.3, 10.1), (13.9, 10.1)], "signal", arrow=True); ltext(13.95, 10.1, "Sinyal", size=5.5)
hexflag(14.85, 10.1, 0.7, 0.26, "XX", size=5); ltext(15.25, 10.1, "Sistem luar / logika", size=5.5)

text(3.3, 10.6, "B O I L E R", size=8, bold=True)
text(10.9, 9.35, "STEAM TURBINE", size=7, bold=True)
text(12.3, 0.8, "CONDENSING & FEEDWATER SYSTEM", size=7, bold=True)

# ---------------------------------------------------------------- BOILER
vessel_h(3.3, 2.5, 2.4, 0.7, "ECONOMIZER")
vessel_v(2.0, 4.1, 0.5, 0.9, "")
rtext(1.7, 4.4, "MIXER", size=6.5, bold=True)
vessel_h(3.3, 5.5, 2.4, 0.7, "EVAPORATOR")
vessel_v(5.6, 6.7, 0.8, 1.8, "STEAM\nDRUM")
vessel_h(3.3, 9.3, 2.4, 0.7, "SUPERHEATER")

# flue gas duct (furnace → SH → EVAP → ECO → stack)
text(1.0, 10.55, "FLUE GAS\n(FURNACE)", size=6, bold=True)
line([(1.0, 10.35), (1.0, 2.0)], "thin", arrow=True, weight=1.0)
for yy in (9.5, 5.7, 2.3):
    line([(1.0, yy), (2.2, yy)], "thin", arrow=True, weight=1.0)
text(1.0, 1.75, "TO AIR HEATER\n/ STACK", size=5.5)
valve(1.0, 7.0, "", act="none", orient="v", w=0.26, h=0.14)
ltext(1.16, 7.0, "DAMPER\nFD-06", size=5.5, bold=True)
bubble(1.45, 3.15, "TT\n06", r=R); leader(1.0, 3.15, 1.25, 3.15)
bubble(1.45, 2.65, "AT\n06", r=R); leader(1.0, 2.65, 1.25, 2.65)
ltext(1.68, 2.65, "O₂", size=5.5)

# water / steam path inside boiler
line([(2.1, 2.5), (1.7, 2.5), (1.7, 4.1), (1.75, 4.1)], "process", arrow=True)          # ECO → MIXER
line([(2.0, 4.55), (2.0, 5.5), (2.1, 5.5)], "process", arrow=True)                     # MIXER → EVAP
line([(4.5, 5.5), (5.6, 5.5), (5.6, 5.8)], "process", arrow=True); text(5.05, 5.7, "RISER", size=5.5)
line([(6.0, 6.1), (6.2, 6.1), (6.2, 3.6), (2.0, 3.6), (2.0, 3.65)], "process", arrow=True)  # DOWNCOMER → MIXER
text(4.1, 3.8, "DOWNCOMER", size=5.5, w=1.0)
line([(5.6, 7.6), (5.6, 8.3), (1.5, 8.3), (1.5, 9.3), (2.1, 9.3)], "process", arrow=True)  # DRUM → SH
text(3.5, 8.5, "SATURATED STEAM", size=5.5)
line([(4.5, 9.3), (7.2, 9.3), (7.2, 9.85), (7.5, 9.85)], "process", arrow=True)        # SH → HPT
text(3.3, 9.92, "MAIN STEAM  540 °C / 170 bar", size=5.5, bold=True)

# drum blowdown & safety
line([(5.2, 6.2), (4.6, 6.2)], "process", arrow=True, weight=1.2)
valve(4.9, 6.2, "", act="none", w=0.26, h=0.14); rtext(4.55, 6.2, "CBD →\nBLOWDOWN", size=5.5, w=0.75)
line([(5.6, 7.9), (4.75, 7.9)], "process", arrow=True, weight=1.2)
valve(5.2, 7.9, "PSV-01", act="none", w=0.26, h=0.14); rtext(4.7, 7.9, "VENT", size=5.5)
bubble(4.95, 7.35, "PT\n01", r=R); leader(5.22, 7.35, 5.15, 7.35)

# drum level: LT → LIC (3-element with FT-01) → FCV-01 ; LSLL/LSHH → MFT
bubble(6.45, 7.0, "LT\n01", r=R); leader(6.0, 7.0, 6.25, 7.0)
bubble(6.45, 6.5, "LIC\n01", "dcs", r=R)
line([(6.45, 6.8), (6.45, 6.7)], "signal", arrow=False)
line([(6.65, 7.0), (7.1, 7.0)], "signal")
hexflag(7.9, 7.0, 1.6, 0.34, "BOILER TRIP (MFT)", size=5.5)
line([(6.45, 6.3), (6.45, 3.4), (5.6, 3.4), (5.6, 2.94)], "signal")

# superheater outlet temperature: TT-04 → TIC-04 → TCV-04 (spray attemperator)
bubble(5.0, 8.95, "TT\n04", r=R); leader(5.0, 9.3, 5.0, 9.15)
bubble(6.6, 8.65, "TIC\n04", "dcs", r=R)
line([(5.2, 8.95), (5.45, 8.95), (5.45, 8.65), (6.4, 8.65)], "signal", arrow=False)
line([(6.6, 8.45), (6.6, 8.41), (6.38, 8.41)], "signal")
tee(6.8, 2.5)
line([(6.8, 2.5), (6.8, 8.1), (5.6, 8.1)], "process", arrow=True, weight=1.2)
valve(6.2, 8.1, "TCV-04\nFC", act="D", w=0.3, h=0.16)
ltext(6.9, 5.0, "SPRAY\nWATER", size=5.5)

# main steam valves & instruments
bubble(6.2, 9.75, "PT\n02", r=R); leader(6.2, 9.3, 6.2, 9.55)
valve(5.8, 9.3, "MSV-01\nSTOP FC", act="D", w=0.3, h=0.16)
valve(6.6, 9.3, "GV-01\nCONTROL", act="D", w=0.3, h=0.16)
hexflag(9.7, 10.95, 1.4, 0.34, "TURBINE TRIP", size=6)
hexflag(9.7, 10.6, 1.4, 0.34, "GOVERNOR", size=6)
line([(9.0, 10.95), (5.8, 10.95), (5.8, 9.73)], "signal")
line([(9.0, 10.6), (6.6, 10.6), (6.6, 9.73)], "signal")

# ---------------------------------------------------------------- TURBINES
turbine(7.5, 9.4, 1.2, 0.9, "HPT", (8.35, 9.85))
power_arrow(8.7, 9.85, 10.0, "POWER 1")
bubble(9.3, 10.15, "ST\n02", r=R); leader(9.3, 9.85, 9.3, 9.95)
line([(9.3, 10.35), (9.3, 10.43)], "signal")
line([(8.1, 9.4), (8.1, 8.55), (9.0, 8.55)], "process", arrow=True)                 # HP exhaust → IPT
bubble(8.5, 8.95, "PT\n07", r=R); leader(8.5, 8.55, 8.5, 8.75)
bubble(8.5, 8.15, "TT\n07", r=R); leader(8.5, 8.55, 8.5, 8.35)
turbine(9.0, 8.0, 1.2, 1.1, "IPT", (9.85, 8.55))
power_arrow(10.2, 8.55, 11.3, "POWER 2")
line([(9.6, 8.0), (9.6, 7.5), (10.4, 7.5)], "process", arrow=True)                  # IP exhaust → LPT
turbine(10.4, 6.9, 1.3, 1.2, "LPT", (11.3, 7.5))
power_arrow(11.7, 7.5, 12.8, "POWER 3")
line([(11.05, 6.9), (11.05, 5.9), (12.3, 5.9), (12.3, 5.1)], "process", arrow=True)  # LP exhaust → condenser
bubble(11.5, 6.45, "PT\n08", r=R); leader(11.05, 6.45, 11.3, 6.45)
bubble(10.6, 6.2, "TT\n08", r=R); leader(11.05, 6.2, 10.8, 6.2)
text(11.7, 6.05, "0.09 bar(a)", size=5.5)

# extraction steam IPT/LPT crossover → feedwater tank (deaerating steam)
tee(10.0, 7.5)
line([(10.0, 7.5), (10.0, 2.85), (11.7, 2.85), (11.7, 2.5)], "process", arrow=True, weight=1.2)
valve(10.0, 5.0, "PCV-15\nFC", act="D", orient="v", w=0.3, h=0.16)
bubble(9.55, 3.8, "PT\n15", r=R); leader(10.0, 3.8, 9.75, 3.8)
bubble(9.55, 4.3, "PIC\n15", "dcs", r=R)
line([(9.55, 4.0), (9.55, 4.1)], "signal", arrow=False)
line([(9.55, 4.5), (9.55, 5.0), (9.66, 5.0)], "signal")
text(9.75, 7.3, "EXTRACTION", size=5.5)

# ---------------------------------------------------------------- CONDENSER
vessel_h(12.3, 4.7, 2.4, 0.8, "CONDENSER")
line([(13.0, 5.1), (13.0, 5.5), (15.7, 5.5)], "process", arrow=True)               # CW out
ltext(15.0, 5.33, "CW TO COOLING TOWER", size=5.5)
line([(15.7, 4.55), (13.5, 4.55)], "process", arrow=True)                           # CW in
ltext(14.75, 4.3, "CW FROM\nCOOLING TOWER", size=5.5)
valve(14.3, 4.55, "TCV-09\nFO", act="D", w=0.3, h=0.16)
bubble(13.9, 4.2, "TT\n09", r=R); leader(13.9, 4.55, 13.9, 4.4)
bubble(14.2, 6.05, "TT\n10", r=R); leader(14.2, 5.5, 14.2, 5.85)
bubble(14.9, 6.05, "TIC\n10", "dcs", r=R)
line([(14.4, 6.05), (14.7, 6.05)], "signal", arrow=False)
line([(14.9, 5.85), (14.9, 5.15), (14.3, 5.15), (14.3, 4.98)], "signal")
line([(11.6, 5.1), (11.6, 5.5)], "process", arrow=True, weight=1.0)
rtext(11.55, 5.5, "TO VACUUM\nSYSTEM", size=5)
bubble(10.7, 4.55, "LT\n11", r=R); leader(11.1, 4.55, 10.9, 4.55)
bubble(10.7, 4.05, "LIC\n11", "dcs", r=R)
line([(10.7, 4.35), (10.7, 4.25)], "signal", arrow=False)
line([(10.7, 3.85), (10.7, 3.3), (11.96, 3.3)], "signal")
line([(12.3, 4.3), (12.3, 2.5)], "process", arrow=True)                            # condensate → tank
pump_v(12.3, 3.85, 0.25, "CEP")
valve(12.3, 3.3, "LCV-11\nFC", act="D", orient="v", w=0.3, h=0.16)

# ---------------------------------------------------------------- FEEDWATER TANK
vessel_h(12.6, 2.1, 2.4, 0.8, "FEEDWATER TANK\n(DEAERATOR)")
line([(13.3, 2.5), (13.3, 2.8)], "process", arrow=True, weight=1.0); ltext(13.38, 2.75, "VENT", size=5)
text(15.7, 1.15, "MAKE-UP\nWATER", size=6, bold=True)
line([(15.7, 1.4), (15.7, 2.1), (13.8, 2.1)], "process", arrow=True)
valve(14.6, 2.1, "LCV-12\nFC", act="D", w=0.3, h=0.16)
bubble(14.4, 2.9, "LT\n12", r=R); leader(13.75, 2.35, 14.25, 2.75)
bubble(15.2, 2.9, "LIC\n12", "dcs", r=R)
line([(14.6, 2.9), (15.0, 2.9)], "signal", arrow=False)
line([(15.2, 2.7), (15.2, 2.62), (14.6, 2.62), (14.6, 2.54)], "signal")

# tank → BCP → economizer
line([(12.6, 1.7), (12.6, 1.2), (8.2, 1.2)], "process", arrow=True)
text(10.4, 1.38, "FEEDWATER", size=5.5)
pump_l(7.9, 1.2, 0.3, "BCP\nBOILER FEED PUMP")
line([(7.6, 1.2), (7.4, 1.2), (7.4, 2.5), (4.5, 2.5)], "process", arrow=True)
text(5.0, 2.72, "FEEDWATER → ECO", size=5.5, bold=True)
bubble(7.9, 1.85, "PT\n14", r=R); leader(7.4, 1.85, 7.7, 1.85)
fe = Shape(5.92, 2.34, 0.16, 0.32, "Instruments", weight=1.0, fill=F_WHITE, name="FE-01"); fe.rect(0, 0, 0.16, 0.32)
bubble(6.0, 3.05, "FT\n01", r=R); leader(6.0, 2.66, 6.0, 2.85)
line([(6.0, 3.25), (6.0, 3.4)], "software", arrow=False)
valve(5.6, 2.5, "FCV-01\nFO", act="D", w=0.3, h=0.16)
# BCP minimum-flow recirculation → tank
tee(7.4, 2.2)
line([(7.4, 2.2), (9.6, 2.2), (9.6, 2.0), (11.4, 2.0)], "process", arrow=True, weight=1.2)
valve(8.6, 2.2, "FV-13\nMIN-FLOW  FO", act="D", w=0.3, h=0.16)

# ---------------------------------------------------------------- notes
NOTES = ("KONTROL:  LIC-01 (3-element: LT-01 + FT-01) → FCV-01 air pengisi drum  |  TIC-04 (TT-04 keluar superheater) → TCV-04 spray attemperator  |  "
         "GOVERNOR (ST-02) → GV-01  |  PIC-15 → PCV-15 uap ekstraksi ke tangki  |  TIC-10 → TCV-09 air pendingin  |  LIC-11 → LCV-11 hotwell  |  LIC-12 → LCV-12 make-up.\n"
         "PROTEKSI:  LSLL/LSHH drum → BOILER TRIP (MFT)  |  TURBINE TRIP → MSV-01 menutup (FC)  |  PSV-01 drum → vent  |  FV-13 min-flow BCP (FO).  "
         "Flue gas: furnace → superheater → evaporator → economizer → air heater/stack (TT-06, AT-06 O₂, damper FD-06).")
Shape(1.9, 0.5, 4.9, 1.0, "Annotation", text=NOTES, size=5.6, halign=0, valign=0, weight=0.8, fill=F_WHITE, name="notes").rect(0, 0, 4.9, 1.0)

if __name__ == "__main__":
    base = os.path.join(OUT, NAME)
    write_vsdx(base + ".vsdx", "PID-STM-002", W, H, "PID-STM-002 Steam cycle P&ID (boiler, HP/IP/LP turbine, condenser, feedwater)")
    write_svg(base + ".svg", W, H, scale=60.0)
    print(f"shapes={len(shapes)}")
