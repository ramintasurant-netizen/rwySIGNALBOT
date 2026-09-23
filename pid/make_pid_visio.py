#!/usr/bin/env python3
"""PID-STM-003 — reproduksi gaya & tata letak gambar Visio pengguna (siklus uap: boiler, HPT/IPT/LPT, condenser, tank, BCP).

Koordinat ditulis dalam piksel gambar referensi (865×671) dan dipetakan ke inci (65 px = 1 in).
Run: python3 make_pid_visio.py  ->  PID-STM-003_VisioStyle.vsdx / .svg
"""
import os

import pidlib
import pid_emit
from pidlib import *  # noqa: F401,F403
from pid_emit import write_vsdx, write_svg

pid_emit.FONT[0] = "Times New Roman"
pidlib.F_EQ = "#FFFFFF"
OUT = os.path.dirname(os.path.abspath(__file__))
NAME = "PID-STM-003_VisioStyle"
SC, IMG_H = 65.0, 671.0
W, H = 865 / SC + 0.4, IMG_H / SC + 0.4
RED, BLUE, BLK = "#C00000", "#1F5FBF", "#000000"
RB = 0.17  # bubble radius (in)


def P(px, py):
    return (px / SC + 0.2, (IMG_H - py) / SC + 0.2)


def LN(pts, kind="blk", arrow=False, color=None, ticks=True, weight=None):
    return line([P(*p) for p in pts], kind, arrow=arrow, color=color, ticks=ticks, weight=weight)


def SIG(pts, arrow=False):
    return LN(pts, "dsh", arrow=arrow, ticks=True)


def B(px, py, tag):
    x, y = P(px, py)
    return bubble(x, y, tag, r=RB, bold=False, size=6.5, weight=0.8)


def T(px, py, s, size=7.5, bold=True, color=BLK, halign=1):
    x, y = P(px, py)
    return text(x, y, s, size=size, bold=bold, color=color, halign=halign)


def CAP_H(px, py, wpx, hpx, label, stubs=()):
    x, y = P(px, py)
    return vessel_h(x, y, wpx / SC, hpx / SC, label, stubs=stubs, size=7.5, weight=1.1, e=hpx / SC * 0.4)


def CAP_V(px, py, wpx, hpx, label, stubs=()):
    x, y = P(px, py)
    return vessel_v(x, y, wpx / SC, hpx / SC, label, stubs=stubs, size=7.5, weight=1.1, e=wpx / SC * 0.4)


def VALVE(px, py, act="none", orient="h"):
    x, y = P(px, py)
    return valve(x, y, "", act=act, orient=orient, w=0.22, h=0.13)


def TRAP(x0, x1, yl0, yl1, yr0, yr1, label, lpx, lpy):
    """Turbine: left edge x0 spanning yl0..yl1, right edge x1 spanning yr0..yr1 (px)."""
    xs = [x0, x1]
    ys = [yl0, yl1, yr0, yr1]
    X0, Y0 = P(min(xs), max(ys))
    X1, Y1 = P(max(xs), min(ys))
    s = Shape(X0, Y0, X1 - X0, Y1 - Y0, "Equipment", weight=1.1, fill=F_WHITE, name=label)
    pts = [P(x0, yl0), P(x1, yr0), P(x1, yr1), P(x0, yl1)]
    s.poly([(a - X0, b - Y0) for a, b in pts], close=True, nofill=False)
    T(lpx, lpy, label)
    return s


def ARROW_BLUE(x0, y, x1, label):
    LN([(x0, y), (x1, y)], arrow=True, color=BLUE, ticks=False, weight=1.0)
    T(x1 + 42, y, label, size=7.5, bold=True, color=BLUE)


# ---------------------------------------------------------------- sheet boundary (dashed, as in the original)
LN([(90, 0), (90, 671)], "dsh", ticks=False)
LN([(665, 0), (665, 671)], "dsh", ticks=False)

# ---------------------------------------------------------------- BOILER
CAP_H(200, 70, 110, 44, "SUPERHEATER", stubs=[("t", 0.5), ("l", 0.5), ("r", 0.5)])
CAP_H(200, 283, 110, 40, "EVAPORATOR", stubs=[("b", 0.5), ("l", 0.5), ("r", 0.5)])
CAP_H(200, 460, 110, 40, "ECONOMIZER", stubs=[("t", 0.5), ("b", 0.5), ("l", 0.5)])
CAP_V(325, 195, 45, 85, "STEAM\nDRUM", stubs=[("t", 0.5), ("b", 0.5), ("l", 0.5)])
# mixer: cylinder with conical bottom
mx, my = P(200, 355)
m = Shape(mx - 0.23, my - 0.33, 0.46, 0.66, "Equipment", weight=1.1, fill=F_WHITE, name="MIXER")
m.poly([(0, 0.22), (0, 0.66), (0.46, 0.66), (0.46, 0.22), (0.23, 0)], close=True, nofill=False)
m.poly([(0.07, 0.42), (0.39, 0.42)], nofill=True); m.poly([(0.23, 0.42), (0.23, 0.22)], nofill=True)
T(155, 355, "MIXER")

# flue gas → superheater (red)
T(130, 6, "FLUE GAS", color=RED)
LN([(112, 14), (112, 70), (145, 70)], arrow=True, color=RED)
VALVE(112, 32, orient="v")
B(170, 32, "TC"); B(222, 46, "TT")
SIG([(222, 35), (222, 22), (190, 22), (190, 32), (181, 32)])
SIG([(159, 32), (123, 32)])

# main steam: SH top → valve → HPT
LN([(200, 48), (200, 8), (330, 8), (330, 30), (352, 30)], arrow=True)
VALVE(292, 8)
B(245, 24, "PT"); SIG([(245, 13), (245, 8)])

# HPT / IPT / LPT
TRAP(352, 385, 18, 42, 8, 52, "HPT", 345, 64)
ARROW_BLUE(395, 32, 575, "POWER 1")
LN([(368, 52), (368, 88), (485, 88), (485, 130), (470, 130)], arrow=True)        # HP exhaust → IPT
LN([(485, 88), (588, 88)])                                                       # to TC
B(513, 58, "PT"); B(570, 58, "TT"); B(600, 88, "TC"); B(627, 72, "PG")
SIG([(513, 69), (513, 88)]); SIG([(570, 69), (570, 88)]); SIG([(524, 58), (559, 58)]); SIG([(581, 62), (616, 72)])
TRAP(432, 470, 100, 145, 108, 135, "IPT", 425, 158)
ARROW_BLUE(478, 112, 585, "POWER 2")
LN([(450, 145), (450, 178), (495, 178)], arrow=True)                             # IP exhaust → LPT
TRAP(495, 535, 165, 192, 155, 200, "LPT", 493, 213)
ARROW_BLUE(545, 178, 600, "POWER 3")
LN([(515, 200), (515, 225), (575, 225), (575, 283)], arrow=True)                 # LP exhaust → condenser

# ---------------------------------------------------------------- CONDENSER
CAP_H(575, 303, 110, 38, "CONDENSER", stubs=[("t", 0.5), ("b", 0.5), ("r", 0.5), ("b", 0.28)])
T(690, 262, "WATER FROM\nCOOLING WATER", color=RED, size=7)
LN([(700, 303), (633, 303)], arrow=True, color=RED)
VALVE(652, 303, act="D")
LN([(545, 322), (545, 378), (658, 378)], arrow=True, color=RED)                  # CW return
B(540, 340, "TC"); B(540, 366, "TT"); B(640, 366, "TT")
SIG([(540, 351), (540, 355)]); SIG([(540, 377), (540, 378)]); SIG([(640, 377), (640, 378)])
SIG([(551, 340), (610, 340), (610, 283), (652, 283), (652, 288)])                # TC → TCV

# condensate → tank
LN([(575, 322), (575, 458)], arrow=True)
tx, ty = P(575, 490)
tank = Shape(tx - 0.62, ty - 0.46, 1.24, 0.92, "Equipment", weight=1.1, fill=F_WHITE, name="TANK")
tank.poly([(0, 0.92), (0, 0), (1.24, 0), (1.24, 0.92)], nofill=True)
T(575, 530, "TANK")
B(622, 462, "LW"); B(648, 462, "LC"); SIG([(633, 462), (637, 462)]); SIG([(622, 473), (615, 478)])
LN([(655, 592), (655, 500), (615, 500)], arrow=True)                             # make-up water
VALVE(655, 548, act="D", orient="v")
SIG([(648, 473), (648, 541)])
T(645, 605, "MAKE UP\nWATER", size=7)

# tank → BCP → economizer
tee(*P(575, 440), color=BLK)
LN([(575, 440), (460, 440), (460, 575), (423, 575)], arrow=True)                 # suction
px_, py_ = P(405, 575)
pmp = Shape(px_ - 0.28, py_ - 0.28, 0.56, 0.62, "Equipment", weight=1.1, fill=F_WHITE, name="BCP")
pmp.ellipse(0.28, 0.28, 0.26, 0.26)
pmp.rect(0.2, 0.54, 0.16, 0.08)                                                  # discharge nozzle
pmp.poly([(0.06, 0.02), (0.5, 0.02)], nofill=True)                               # base
T(405, 612, "BCP")
LN([(405, 555), (405, 500), (200, 500), (200, 482)], arrow=True)                 # discharge → ECO
T(305, 518, "WATER")
B(370, 470, "PT"); B(340, 470, "FT"); B(455, 610, "TC")
SIG([(370, 481), (370, 500)]); SIG([(340, 481), (340, 500)]); SIG([(359, 470), (351, 470)]); SIG([(444, 610), (424, 590)])
T(150, 426, "FLUE GAS", color=RED)
LN([(112, 436), (112, 460), (145, 460)], arrow=True, color=RED)

# economizer → mixer → evaporator, downcomer, riser
LN([(200, 440), (200, 377)], arrow=True)
LN([(200, 333), (200, 303)], arrow=True)
LN([(325, 238), (325, 355), (217, 355)], arrow=True)                             # downcomer
LN([(255, 283), (265, 283), (265, 195), (300, 195)], arrow=True, color=RED)      # riser
VALVE(265, 230, act="D", orient="v"); VALVE(285, 195)
B(125, 212, "TIC"); B(140, 175, "TT"); B(178, 175, "TF")
SIG([(151, 175), (167, 175)]); SIG([(178, 186), (178, 263)]); SIG([(140, 186), (140, 198), (125, 201)])
SIG([(136, 212), (250, 212), (250, 230), (257, 230)])                            # TIC → riser valve
# mixer bypass with temperature control (left cluster)
tee(*P(200, 410), color=BLK)
LN([(200, 410), (70, 410), (70, 325), (190, 325)], arrow=True)
VALVE(130, 325, act="D")
B(45, 380, "TT"); B(45, 455, "TIC")
SIG([(56, 380), (70, 380)]); SIG([(45, 391), (45, 444)]); SIG([(34, 455), (26, 455), (26, 296), (130, 296), (130, 305)])

# saturated steam: drum top → superheater right; drum instruments
LN([(325, 152), (325, 105), (280, 105), (280, 70), (258, 70)], arrow=True)
B(298, 128, "TT"); B(352, 128, "TF"); SIG([(309, 128), (325, 128)]); SIG([(341, 128), (325, 128)])
B(375, 150, "LW"); B(375, 240, "LC"); SIG([(375, 161), (375, 229)]); SIG([(364, 150), (347, 160)]); SIG([(364, 240), (347, 232)])

if __name__ == "__main__":
    base = os.path.join(OUT, NAME)
    write_vsdx(base + ".vsdx", "PID-STM-003", W, H, "PID-STM-003 steam cycle P&ID (Visio style reproduction)")
    write_svg(base + ".svg", W, H, scale=90.0)
    print(f"shapes={len(shapes)}")
