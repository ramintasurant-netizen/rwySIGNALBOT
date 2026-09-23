#!/usr/bin/env python3
"""PID-STM-001 — P&ID SEDERHANA siklus uap PLTU dengan ECONOMIZER, STEAM DRUM & SUPERHEATER.

Gaya klasik hitam-putih (seperti sketsa referensi). Run: python3 make_pid_simple.py
Output: PID-STM-001_Simple.vsdx / .svg  (A3 landscape, inch, origin kiri-bawah)
"""
import os

import pidlib
from pidlib import *  # noqa: F401,F403
from pid_emit import write_vsdx, write_svg

pidlib.STYLE["mono"] = True
OUT = os.path.dirname(os.path.abspath(__file__))
NAME = "PID-STM-001_Simple"
W, H = 16.54, 11.69


# ---------------------------------------------------------------- extra symbols
def coil(cx, cy, w, h, label):
    """Tube bank (economizer / superheater): box with serpentine in the upper half, name below."""
    s = Shape(cx - w / 2, cy - h / 2, w, h, "Equipment", weight=1.2, fill=F_WHITE, name=label)
    s.rect(0, 0, w, h)
    n, x0, x1, ya, yb = 8, 0.15, w - 0.15, h * 0.6, h * 0.9
    pts = [(0, (ya + yb) / 2), (x0, (ya + yb) / 2)]
    step = (x1 - x0) / n
    for i in range(n):
        pts.append((x0 + step * (i + 0.5), yb if i % 2 == 0 else ya))
    pts += [(x1, (ya + yb) / 2), (w, (ya + yb) / 2)]
    s.poly(pts, nofill=True)
    text(cx, cy - h * 0.22, label, size=7, bold=True)
    return s


def turbine(x, y, w=1.2, h=1.4):
    s = Shape(x, y, w, h, "Equipment", weight=1.4, fill=F_WHITE, name="STEAM TURBINE")
    s.poly([(0, h * 0.25), (w, 0), (w, h), (0, h * 0.75)], close=True, nofill=False)
    return s


def generator(cx, cy, r=0.5):
    s = Shape(cx - r, cy - r, 2 * r, 2 * r, "Equipment", weight=1.4, fill=F_WHITE, name="GENERATOR")
    s.ellipse(r, r, r, r)
    # sine wave
    import math
    pts = [(r * 0.4 + i * (r * 1.2 / 16), r + 0.28 * r * math.sin(i * math.pi / 8)) for i in range(17)]
    s.poly(pts, nofill=True)
    return s


def condenser(cx, cy, w, h, label):
    s = Shape(cx - w / 2, cy - h / 2, w, h + 0.5, "Equipment", weight=1.4, fill=F_WHITE, name=label, text=label, size=7.5, bold=True)
    s.poly([(0, 0), (w, 0), (w, h), (w / 2 + 0.25, h + 0.5), (w / 2 - 0.25, h + 0.5), (0, h)], close=True, nofill=False)
    return s


def cooling_tower(cx, cy, w, h, label):
    s = Shape(cx - w / 2, cy - h / 2, w, h + 0.3, "Equipment", weight=1.4, fill=F_WHITE, name=label, text="\n" + label, size=6.5, bold=True)
    s.poly([(0, 0), (w, 0), (w - 0.15, h), (0.15, h)], close=True, nofill=False)
    s.rect(w / 2 - 0.3, h, 0.6, 0.3)
    s.poly([(w / 2 - 0.3, h), (w / 2 + 0.3, h + 0.3)], nofill=True)
    s.poly([(w / 2 - 0.3, h + 0.3), (w / 2 + 0.3, h)], nofill=True)
    return s


# ---------------------------------------------------------------- frame / title
Shape(0.4, 0.4, W - 0.8, H - 0.8, "Annotation", weight=1.8, name="frame").rect(0, 0, W - 0.8, H - 0.8, nofill=True)
box(14.05, 10.45, 4.1, 1.5, "", fill=F_WHITE, weight=1.4)
line([(12.0, 10.75), (16.1, 10.75)], "thin", arrow=False)
text(14.05, 10.98, "P&ID SEDERHANA — SIKLUS UAP PLTU\nDENGAN ECONOMIZER, STEAM DRUM & SUPERHEATER", size=8, bold=True)
text(12.75, 10.35, "Dwg. No.\nPID-STM-001", size=6.5, bold=True)
text(14.05, 10.35, "Rev. A\nIssued for Review", size=6.5)
text(15.35, 10.35, "A3 — NTS\nTag: ISA-5.1", size=6.5)
text(14.05, 9.85, "Solid = proses (uap / air)     Dashed = sinyal instrumen     ⬡ = sistem luar / logika", size=6)

# ---------------------------------------------------------------- BOILER (economizer -> drum -> evaporator -> superheater)
bx0, bx1, by0, by1 = 1.45, 4.7, 2.65, 9.9
Shape(bx0, by0, bx1 - bx0, by1 - by0, "Annotation", weight=1.0, pattern=2, name="BOILER").rect(0, 0, bx1 - bx0, by1 - by0, nofill=True)
text(3.1, 9.7, "BOILER", size=8, bold=True)
coil(2.9, 3.2, 2.0, 0.9, "ECONOMIZER")
box(2.9, 4.85, 2.0, 0.8, "EVAPORATOR\n(WATER WALL)", fill=F_WHITE, size=6.5)
vessel_h(2.9, 6.4, 2.6, 0.9, "STEAM DRUM")
coil(2.9, 8.9, 2.0, 0.9, "SUPERHEATER")

# feedwater -> economizer -> drum
line([(4.4, 1.4), (4.4, 3.2), (3.9, 3.2)], "process", arrow=True)
line([(1.9, 3.2), (1.6, 3.2), (1.6, 6.4)], "process", arrow=True)
# downcomer / riser
line([(2.1, 5.95), (2.1, 5.25)], "process", arrow=True); text(1.75, 5.6, "DOWN-\nCOMER", size=5.5)
line([(3.7, 5.25), (3.7, 5.95)], "process", arrow=True); text(4.05, 5.6, "RISER", size=5.5)
# saturated steam -> superheater (spray joins at the elbow)
line([(2.9, 6.85), (2.9, 7.6), (1.8, 7.6), (1.8, 8.9), (1.9, 8.9)], "process", arrow=True)
text(2.55, 7.78, "SAT. STEAM", size=5.5)
# superheater outlet -> main steam
line([(3.9, 8.9), (8.6, 8.9), (8.6, 7.4), (10.7, 7.4)], "process", arrow=True)
text(7.9, 9.1, "MAIN STEAM", size=6.5, bold=True)

# drum level control (LT -> LIC -> FCV) + boiler trip
bubble(5.3, 6.4, "LT\n01", r=0.22); leader(4.2, 6.4, 5.08, 6.4)
bubble(5.3, 5.4, "LIC\n01", "dcs", r=0.22)
line([(5.3, 6.18), (5.3, 5.62)], "signal", arrow=False)
line([(5.3, 5.18), (5.3, 2.45), (4.06, 2.45), (4.06, 2.36)], "signal")
valve(4.4, 2.2, "FCV-01\nFO", act="D", orient="v", w=0.3, h=0.16)
line([(5.52, 6.4), (5.85, 6.4)], "signal")
hexflag(6.9, 6.4, 2.1, 0.36, "LSLL / LSHH → BOILER TRIP (MFT)", size=6)
bubble(4.95, 3.0, "TT\n02", r=0.22); leader(4.4, 3.0, 4.73, 3.0)

# superheater outlet temperature control (TT -> TIC -> spray TCV)
bubble(5.3, 9.5, "TT\n01", r=0.22); leader(5.3, 8.9, 5.3, 9.28)
bubble(5.3, 10.3, "TIC\n01", "dcs", r=0.22)
line([(5.3, 9.72), (5.3, 10.08)], "signal", arrow=False)
line([(5.08, 10.3), (0.55, 10.3), (0.55, 5.0), (0.6, 5.0)], "signal")
bubble(6.1, 9.5, "PT\n01", r=0.22); leader(6.1, 8.9, 6.1, 9.28)
# spray water (from BFP discharge, upstream of FCV)
tee(5.2, 1.4)
line([(5.2, 1.4), (5.2, 1.0), (0.9, 1.0), (0.9, 7.6), (1.8, 7.6)], "process", arrow=True, weight=1.2)
valve(0.9, 5.0, "TCV-01\nFC", act="D", orient="v", w=0.3, h=0.16)
ltext(1.1, 0.85, "SPRAY WATER (ATTEMPERATOR)", size=5.5)

# safety valve -> silencer
line([(7.2, 8.9), (7.2, 9.2)], "process", arrow=False, weight=1.2)
valve(7.2, 9.35, "PSV-01", act="none", orient="v", w=0.3, h=0.16)
line([(7.2, 9.5), (7.2, 9.7)], "process", arrow=True, weight=1.2)
vessel_v(7.2, 10.3, 0.5, 1.2, "")
ltext(7.55, 10.3, "SILENCER", size=6.5, bold=True)
line([(7.2, 10.9), (7.2, 11.15)], "process", arrow=True, weight=1.0); ltext(7.3, 11.1, "VENT", size=5.5)

# ---------------------------------------------------------------- TURBINE / GENERATOR
valve(9.2, 7.4, "MSV-01\nSTOP", act="D", w=0.32, h=0.18)
valve(10.0, 7.4, "GV-01\nCONTROL", act="D", w=0.32, h=0.18)
hexflag(9.2, 8.6, 1.3, 0.36, "TURBINE TRIP")
hexflag(10.6, 8.6, 1.1, 0.36, "GOVERNOR")
line([(9.2, 8.42), (9.2, 7.84)], "signal")
line([(10.6, 8.42), (10.6, 8.1), (10.0, 8.1), (10.0, 7.84)], "signal")
turbine(10.7, 6.5)
text(10.2, 6.35, "STEAM\nTURBINE", size=6.5, bold=True)
line([(11.9, 7.13), (12.8, 7.13)], "thin", arrow=False, weight=1.2)
line([(11.9, 7.27), (12.8, 7.27)], "thin", arrow=False, weight=1.2)
generator(13.3, 7.2)
text(13.3, 6.5, "GENERATOR", size=6.5, bold=True)
# exhaust
line([(11.3, 6.5), (11.3, 4.6)], "process", arrow=True)
bubble(11.85, 5.8, "PT\n02", r=0.22); leader(11.3, 5.8, 11.63, 5.8)
bubble(11.85, 5.2, "TT\n03", r=0.22); leader(11.3, 5.2, 11.63, 5.2)

# ---------------------------------------------------------------- CONDENSER / COOLING
condenser(11.3, 3.4, 1.6, 1.4, "CONDENSER")
bubble(10.1, 3.4, "LT\n02", r=0.22); leader(10.5, 3.4, 10.32, 3.4)
cooling_tower(14.6, 3.6, 1.4, 1.2, "COOLING\nTOWER")
line([(14.6, 3.0), (14.6, 1.1), (14.0, 1.1)], "process", arrow=False)
pump(13.7, 1.1, 0.3, "CW PUMP")
line([(13.4, 1.1), (12.9, 1.1), (12.9, 3.1), (12.1, 3.1)], "process", arrow=True)
line([(12.1, 3.7), (13.4, 3.7), (13.4, 4.0), (13.9, 4.0)], "process", arrow=True)
text(12.75, 3.88, "CW", size=5.5)

# condensate -> deaerator -> BFP -> economizer
line([(11.3, 2.7), (11.3, 1.1), (9.9, 1.1)], "process", arrow=False)
pump(9.6, 1.1, 0.3, "CONDENSATE\nPUMP")
line([(9.3, 1.1), (8.5, 1.1), (8.5, 3.0), (8.1, 3.0)], "process", arrow=True)
vessel_v(7.8, 2.6, 0.6, 1.2, "")
rtext(7.42, 2.3, "DEAERATOR", size=6.5, bold=True)
bubble(7.1, 2.8, "LT\n03", r=0.22); leader(7.5, 2.8, 7.32, 2.8)
hexflag(7.8, 4.3, 1.5, 0.36, "AUX / EXTRACTION STEAM", size=6)
line([(7.8, 4.12), (7.8, 3.2)], "process", arrow=True, weight=1.2)
line([(7.8, 2.0), (7.8, 1.4), (7.0, 1.4)], "process", arrow=False)
pump(6.7, 1.4, 0.3, "BOILER FEED\nPUMP")
line([(6.4, 1.4), (4.4, 1.4)], "process", arrow=False)
fe = Shape(5.72, 1.24, 0.16, 0.32, "Instruments", weight=1.0, fill=F_WHITE, name="FE-01"); fe.rect(0, 0, 0.16, 0.32)
bubble(5.8, 1.95, "FT\n01", r=0.22); leader(5.8, 1.56, 5.8, 1.73)
text(3.5, 1.22, "FEEDWATER  →  ECONOMIZER", size=6, bold=True)

# ---------------------------------------------------------------- notes
NOTES = ("URUTAN PROSES: Kondensat → Deaerator → BFP → Economizer (pemanas awal air) → Steam Drum → Evaporator/Riser → Drum → "
         "Superheater → Main Steam → MSV/GV → Turbin → Generator; uap bekas → Kondensor (pendingin dari Cooling Tower) → kembali ke Deaerator.\n"
         "KONTROL: LIC-01 level drum → FCV-01 air pengisi;  TIC-01 suhu uap keluar superheater → TCV-01 spray attemperator;  GOVERNOR → GV-01 (beban/putaran).\n"
         "PROTEKSI: LSLL/LSHH drum → Boiler Trip (MFT);  TURBINE TRIP → MSV-01 menutup (fail close);  PSV-01 melepas uap lebih ke Silencer.")
Shape(8.3, 9.3, 3.5, 1.9, "Annotation", text=NOTES, size=5.6, halign=0, valign=0, weight=0.8, fill=F_WHITE, name="notes").rect(0, 0, 3.5, 1.9)

if __name__ == "__main__":
    base = os.path.join(OUT, NAME)
    write_vsdx(base + ".vsdx", "PID-STM-001", W, H, "PID-STM-001 Simple steam cycle P&ID with economizer and superheater")
    write_svg(base + ".svg", W, H, scale=60.0)
    print(f"shapes={len(shapes)}")
