#!/usr/bin/env python3
"""Build the geothermal power plant P&ID as a native Visio drawing (.vsdx).

The drawing is described once as plain geometry (paths, ellipses, text) and then
emitted twice: as Visio ShapeSheet XML inside an OPC package, and as an SVG that
is only used to render the preview image.
"""
from __future__ import annotations

import datetime as dt
import math
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

PAGE_W, PAGE_H = 16.5354, 11.6929  # A3 landscape, inches
FONT = "Times New Roman"
PT = 1 / 72  # points to inches

BLACK = "#000000"
STEAM = "#A61B1B"
WATER = "#1F4E9A"
BRINE = "#2E7D32"
NCG = "#6B4C9A"
WHITE = "#FFFFFF"
FILL_STEAM = "#FBF1E8"
FILL_WATER = "#EAF1F8"
FILL_NEUTRAL = "#F2F2F2"

W_MAIN = 0.022
W_SEC = 0.015
W_EQUIP = 0.014
W_SYM = 0.011
W_SIGNAL = 0.008


@dataclass
class Item:
    kind: str  # path | ellipse | text
    segs: list = field(default_factory=list)
    cx: float = 0.0
    cy: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    stroke: str | None = BLACK
    fill: str | None = None
    weight: float = W_EQUIP
    pattern: int = 1
    begin_arrow: int = 0
    end_arrow: int = 0
    closed: bool = False
    text: str | None = None
    size: float = 7.0
    bold: bool = True
    color: str = BLACK
    angle: float = 0.0
    tw: float | None = None
    th: float | None = None
    margins: bool = True
    align: str = "center"


class Drawing:
    def __init__(self) -> None:
        self.items: list[Item] = []

    def path(self, segs, **kw) -> Item:
        it = Item(kind="path", segs=list(segs), **kw)
        self.items.append(it)
        return it

    def line(self, pts, **kw) -> Item:
        segs = [("M", *pts[0])] + [("L", *p) for p in pts[1:]]
        return self.path(segs, fill=None, closed=False, **kw)

    def polygon(self, pts, **kw) -> Item:
        segs = [("M", *pts[0])] + [("L", *p) for p in pts[1:]]
        return self.path(segs, closed=True, **kw)

    def rect(self, x, y, w, h, **kw) -> Item:
        return self.polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], **kw)

    def ellipse(self, cx, cy, rx, ry, **kw) -> Item:
        it = Item(kind="ellipse", cx=cx, cy=cy, rx=rx, ry=ry, **kw)
        self.items.append(it)
        return it

    def text(self, cx, cy, s, size=7.0, bold=True, angle=0.0, color=BLACK, tw=None, th=None, align="center") -> Item:
        """Free text centred on (cx, cy); with align='left' cx is the left edge instead."""
        if align == "left":
            w = tw if tw is not None else _text_width(s, size)
            cx = cx + w / 2
            tw = w
        it = Item(kind="text", cx=cx, cy=cy, text=s, size=size, bold=bold, angle=angle,
                  color=color, tw=tw, th=th, stroke=None, fill=None, align=align)
        self.items.append(it)
        return it

    # ---- P&ID symbols -------------------------------------------------
    def gate_valve(self, cx, cy, s=0.26, vertical=False, fill=WHITE):
        h = s * 0.62
        if vertical:
            pts = [(cx - h / 2, cy - s / 2), (cx + h / 2, cy + s / 2), (cx - h / 2, cy + s / 2), (cx + h / 2, cy - s / 2)]
        else:
            pts = [(cx - s / 2, cy - h / 2), (cx + s / 2, cy + h / 2), (cx + s / 2, cy - h / 2), (cx - s / 2, cy + h / 2)]
        self.polygon(pts, stroke=BLACK, fill=fill, weight=W_SYM)

    def control_valve(self, cx, cy, s=0.26):
        """Globe valve with diaphragm actuator; returns the actuator top for the signal line."""
        self.gate_valve(cx, cy, s)
        stem_top = cy + 0.17
        self.line([(cx, cy), (cx, stem_top)], weight=W_SYM)
        dome_r = 0.12
        self.path([("M", cx - dome_r, stem_top), ("A", cx + dome_r, stem_top, cx, stem_top + dome_r)],
                  stroke=BLACK, fill=WHITE, weight=W_SYM, closed=True)
        return (cx, stem_top + dome_r)

    def box_actuator_valve(self, cx, cy, letter, s=0.26):
        """Valve with a lettered box actuator (M = motor, S = solenoid)."""
        self.gate_valve(cx, cy, s)
        b = 0.18
        stem_top = cy + 0.14
        self.line([(cx, cy), (cx, stem_top)], weight=W_SYM)
        self.rect(cx - b / 2, stem_top, b, b, stroke=BLACK, fill=WHITE, weight=W_SYM,
                  text=letter, size=6, margins=False)
        return (cx, stem_top + b)

    def instrument(self, cx, cy, tag, num, dcs=False, r=0.18):
        if dcs:
            self.rect(cx - r - 0.03, cy - r - 0.03, 2 * r + 0.06, 2 * r + 0.06, stroke=BLACK, fill=WHITE, weight=W_SYM)
        self.ellipse(cx, cy, r, r, stroke=BLACK, fill=WHITE, weight=W_SYM,
                     text=f"{tag}\n{num}", size=5.5, margins=False)

    def flag(self, x, y, w, h, label, direction="right", size=6.5, fill=WHITE):
        """Off-sheet connector: pentagon pointing in the flow direction."""
        t = h / 2
        if direction == "right":
            pts = [(x, y - t), (x + w - t, y - t), (x + w, y), (x + w - t, y + t), (x, y + t)]
        else:
            pts = [(x + w, y - t), (x + t, y - t), (x, y), (x + t, y + t), (x + w, y + t)]
        self.polygon(pts, stroke=BLACK, fill=fill, weight=W_EQUIP, text=label, size=size, margins=False)

    def hvessel(self, x, y, w, h, fill, label=None, size=7):
        r = h / 2
        segs = [("M", x + r, y), ("L", x + w - r, y), ("A", x + w - r, y + h, x + w, y + r),
                ("L", x + r, y + h), ("A", x + r, y, x, y + r)]
        return self.path(segs, stroke=BLACK, fill=fill, weight=W_EQUIP, closed=True, text=label, size=size)

    def vvessel(self, cx, y0, w, body_h, fill):
        """Vertical vessel; y0 is the bottom of the straight body."""
        r = w / 2
        x = cx - r
        # heads are drawn as part of one closed outline so the fill is continuous
        segs = [("M", x, y0), ("A", x + w, y0, cx, y0 - r), ("L", x + w, y0 + body_h),
                ("A", x, y0 + body_h, cx, y0 + body_h + r)]
        self.path(segs, stroke=BLACK, fill=fill, weight=W_EQUIP, closed=True)

    def nozzle(self, cx, y, w=0.16, h=0.12, up=True):
        y0 = y if up else y - h
        self.rect(cx - w / 2, y0, w, h, stroke=BLACK, fill=WHITE, weight=W_SYM)

    def pump(self, cx, cy, r=0.17, direction="right"):
        self.ellipse(cx, cy, r, r, stroke=BLACK, fill=WHITE, weight=W_EQUIP)
        d = 1 if direction == "right" else -1
        self.polygon([(cx - d * 0.09, cy - 0.09), (cx + d * 0.1, cy), (cx - d * 0.09, cy + 0.09)],
                     stroke=BLACK, fill=BLACK, weight=W_SYM)


# ---------------------------------------------------------------------------
# Drawing content
# ---------------------------------------------------------------------------
def build() -> Drawing:
    d = Drawing()

    # Sheet frame, legend, notes and title block
    d.rect(0.35, 0.35, PAGE_W - 0.7, PAGE_H - 0.7, stroke=BLACK, fill=None, weight=0.02)
    d.line([(0.35, 1.45), (PAGE_W - 0.35, 1.45)], weight=0.016)
    d.line([(6.3, 0.35), (6.3, 1.45)], weight=0.012)
    d.line([(11.0, 0.35), (11.0, 1.45)], weight=0.012)
    legend(d)
    notes(d)
    title_block(d)
    equipment_list(d)

    steam_supply(d)
    steam_header_and_turbine(d)
    condenser_and_gas_removal(d)
    cooling_water(d)
    return d


def legend(d: Drawing) -> None:
    d.text(0.55, 1.32, "LEGEND", size=7, align="left")
    rows = [
        (STEAM, W_MAIN, 1, "MAIN STEAM"),
        (WATER, W_SEC, 1, "CONDENSATE / COOLING WATER"),
        (BRINE, W_SEC, 1, "BRINE / DRAIN"),
        (NCG, W_SEC, 1, "NON-CONDENSABLE GAS / VENT"),
        (BLACK, W_SIGNAL, 2, "INSTRUMENT SIGNAL"),
    ]
    y = 1.14
    for color, w, pat, name in rows:
        d.line([(0.55, y), (1.1, y)], stroke=color, weight=w, pattern=pat)
        d.text(1.2, y, name, size=6, bold=False, align="left")
        y -= 0.17
    d.gate_valve(3.15, 1.12, s=0.24)
    d.text(3.35, 1.12, "GATE VALVE", size=6, bold=False, align="left")
    d.control_valve(3.15, 0.55, s=0.24)
    d.text(3.35, 0.62, "CONTROL VALVE", size=6, bold=False, align="left")
    d.instrument(4.65, 1.12, "XX", "000")
    d.text(4.9, 1.12, "FIELD MOUNTED\nINSTRUMENT", size=5.5, bold=False, align="left")
    d.instrument(4.65, 0.58, "XX", "000", dcs=True)
    d.text(4.9, 0.58, "DCS / SHARED\nDISPLAY", size=5.5, bold=False, align="left")


def notes(d: Drawing) -> None:
    d.text(6.45, 1.32, "NOTES", size=7, align="left")
    lines = [
        "1.  DIAGRAM IS SCHEMATIC AND NOT TO SCALE.",
        "2.  INSTRUMENT IDENTIFICATION PER ISA-5.1.",
        "3.  LINE SIZES, RATINGS AND SPECIFICATIONS TO BE",
        "     CONFIRMED DURING DETAILED ENGINEERING.",
        "4.  ALL DRAINS AND BRINE ROUTED TO REINJECTION SYSTEM.",
    ]
    y = 1.14
    for s in lines:
        d.text(6.45, y, s, size=6, bold=False, align="left")
        y -= 0.165


def title_block(d: Drawing) -> None:
    x0, x1 = 11.0, PAGE_W - 0.35
    d.line([(x0, 1.05), (x1, 1.05)], weight=0.012)
    d.line([(x0, 0.7), (x1, 0.7)], weight=0.012)
    d.line([(13.4, 0.35), (13.4, 1.05)], weight=0.012)
    d.line([(14.6, 0.35), (14.6, 1.05)], weight=0.012)
    d.line([(15.4, 0.35), (15.4, 1.05)], weight=0.012)
    d.text((x0 + x1) / 2, 1.25, "GEOTHERMAL POWER PLANT", size=9)
    d.text(12.2, 0.93, "PIPING AND INSTRUMENTATION", size=6.5)
    d.text(12.2, 0.80, "DIAGRAM", size=6.5)
    d.text(12.2, 0.60, "STEAM, CONDENSATE AND", size=6, bold=False)
    d.text(12.2, 0.48, "COOLING WATER SYSTEM", size=6, bold=False)
    d.text(14.0, 0.95, "DRAWING NO.", size=5.5, bold=False)
    d.text(14.0, 0.80, "GPP-PID-001", size=7)
    d.text(14.0, 0.60, "REV.", size=5.5, bold=False)
    d.text(14.0, 0.46, "A", size=7)
    d.text(15.0, 0.60, "SCALE", size=5.5, bold=False)
    d.text(15.0, 0.46, "NTS", size=7)
    d.text(15.8, 0.60, "SHEET", size=5.5, bold=False)
    d.text(15.8, 0.46, "1 OF 1", size=7)
    d.text(15.0, 0.95, "DRAWN", size=5.5, bold=False)
    d.text(15.8, 0.95, "APPROVED", size=5.5, bold=False)


def equipment_list(d: Drawing) -> None:
    rows = [
        ("V-101", "STEAM COLLECTOR"),
        ("V-102", "DEMISTER"),
        ("S-101", "ATMOSPHERIC SILENCER"),
        ("ST-101", "STEAM TURBINE"),
        ("G-101", "GENERATOR"),
        ("E-201", "DIRECT CONTACT CONDENSER"),
        ("EJ-201", "STEAM JET EJECTOR"),
        ("AC-201", "AFTER CONDENSER"),
        ("P-201", "HOT WELL PUMP"),
        ("P-301", "CIRCULATING WATER PUMP"),
        ("CT-301", "COOLING TOWER"),
    ]
    x0, x1, xm = 3.6, 7.6, 4.5
    rh = 0.2
    top = 6.3
    d.text(x0, top + 0.14, "EQUIPMENT LIST", size=7, align="left")
    h = rh * (len(rows) + 1)
    d.rect(x0, top - h, x1 - x0, h, stroke=BLACK, fill=None, weight=W_SYM)
    d.line([(xm, top - h), (xm, top)], weight=W_SYM)
    d.line([(x0, top - rh), (x1, top - rh)], weight=W_SYM)
    d.text((x0 + xm) / 2, top - rh / 2, "TAG", size=6)
    d.text((xm + x1) / 2, top - rh / 2, "DESCRIPTION", size=6)
    y = top - rh
    for tag, desc in rows:
        d.line([(x0, y), (x1, y)], weight=0.006)
        d.text((x0 + xm) / 2, y - rh / 2, tag, size=6, bold=False)
        d.text(xm + 0.1, y - rh / 2, desc, size=6, bold=False, align="left")
        y -= rh


def steam_supply(d: Drawing) -> None:
    # Steam collector V-101 with inlet from production wells, vent to silencer, outlet to demister
    d.flag(0.6, 9.6, 1.6, 0.4, "FROM PRODUCTION\nWELLS", "right")
    d.line([(2.2, 9.6), (2.88, 9.6)], stroke=STEAM, weight=W_MAIN, end_arrow=4)
    d.box_actuator_valve(2.54, 9.6, "M")
    d.text(2.54, 9.3, "MOV-101", size=5.5)

    d.hvessel(3.0, 9.2, 3.4, 0.8, FILL_STEAM, label="STEAM COLLECTOR\nV-101")
    d.rect(2.88, 9.51, 0.12, 0.18, stroke=BLACK, fill=WHITE, weight=W_SYM)   # inlet nozzle
    d.rect(6.4, 9.51, 0.12, 0.18, stroke=BLACK, fill=WHITE, weight=W_SYM)    # outlet nozzle
    for nx in (3.9, 4.8):
        d.nozzle(nx, 10.0)
    for nx in (3.9, 4.9):
        d.nozzle(nx, 9.2, up=False)
    for sx in (3.35, 5.45):
        d.rect(sx, 8.98, 0.3, 0.22, stroke=BLACK, fill=WHITE, weight=W_SYM)  # saddles

    # Pressure transmitter on the collector, controller in DCS, I/P and vent control valve
    d.line([(4.8, 10.12), (4.8, 10.32)], weight=W_SYM)
    d.instrument(4.8, 10.5, "PT", "101")
    d.line([(4.8, 10.68), (4.8, 11.05), (4.1, 11.05)], pattern=2, weight=W_SIGNAL)
    d.instrument(3.9, 11.05, "PIC", "101", dcs=True)
    d.line([(3.7, 11.05), (3.38, 11.05)], pattern=2, weight=W_SIGNAL)
    d.instrument(3.2, 11.05, "PY", "101")
    d.line([(3.02, 11.05), (2.6, 11.05), (2.6, 10.97)], pattern=2, weight=W_SIGNAL)

    d.line([(3.9, 10.12), (3.9, 10.68), (1.35, 10.68)], stroke=STEAM, weight=W_SEC, end_arrow=4)
    d.control_valve(2.6, 10.68)
    d.text(2.6, 10.5, "PCV-101", size=5.5)

    # Atmospheric silencer S-101
    d.rect(0.85, 10.35, 0.5, 0.8, stroke=BLACK, fill=FILL_STEAM, weight=W_EQUIP)
    d.polygon([(0.85, 10.35), (1.35, 10.35), (1.18, 10.17), (1.02, 10.17)], stroke=BLACK, fill=FILL_STEAM, weight=W_EQUIP)
    d.line([(1.1, 10.17), (1.1, 10.0)], stroke=BRINE, weight=W_SEC, end_arrow=4)
    d.text(1.1, 10.75, "SILENCER  S-101", size=5, angle=90)
    d.line([(1.1, 11.15), (1.1, 11.3)], stroke=NCG, weight=W_SEC, end_arrow=4)
    d.text(1.2, 11.24, "TO ATM", size=5.5, bold=False, align="left")
    d.text(1.25, 10.04, "TO DRAIN", size=5.5, bold=False, align="left")

    # Drains from collector to brine header
    for nx in (3.9, 4.9):
        d.line([(nx, 9.08), (nx, 8.2)], stroke=BRINE, weight=W_SEC)
        d.gate_valve(nx, 8.6, s=0.24, vertical=True)

    # Demister V-102
    d.line([(6.52, 9.6), (7.0, 9.6)], stroke=STEAM, weight=W_MAIN, end_arrow=4)
    d.vvessel(7.25, 8.75, 0.5, 1.0, FILL_STEAM)
    d.text(7.25, 9.25, "DEMISTER  V-102", size=5.5, angle=90)
    d.line([(7.0, 9.0), (6.83, 9.0)], weight=W_SYM)
    d.instrument(6.65, 9.0, "LT", "102")
    d.line([(7.25, 8.5), (7.25, 8.2)], stroke=BRINE, weight=W_SEC)
    d.gate_valve(7.25, 8.36, s=0.22, vertical=True)

    # Brine / drain header to reinjection
    d.line([(7.25, 8.2), (2.9, 8.2), (2.9, 2.3)], stroke=BRINE, weight=W_SEC)
    d.text(6.1, 8.32, "BRINE / DRAIN HEADER", size=5.5, bold=False)


def steam_header_and_turbine(d: Drawing) -> None:
    # Main steam from demister top to turbine inlet header
    d.line([(7.25, 10.0), (7.25, 10.3), (8.0, 10.3), (8.0, 9.3), (11.7, 9.3)], stroke=STEAM, weight=W_MAIN, end_arrow=4)
    d.text(7.62, 10.42, "MAIN STEAM", size=5.5, bold=False)

    d.line([(8.35, 9.3), (8.35, 9.57)], weight=W_SYM)
    d.instrument(8.35, 9.75, "PT", "103")
    d.line([(8.75, 9.3), (8.75, 9.57)], weight=W_SYM)
    d.instrument(8.75, 9.75, "TT", "101")

    # Pressure control loop on the header
    top = d.control_valve(9.3, 9.3)
    d.text(9.3, 9.02, "PCV-102", size=5.5)
    d.line([(8.35, 9.93), (8.35, 10.6), (9.07, 10.6)], pattern=2, weight=W_SIGNAL)
    d.instrument(9.3, 10.6, "PIC", "102", dcs=True)
    d.line([(9.3, 10.39), (9.3, 10.18)], pattern=2, weight=W_SIGNAL)
    d.instrument(9.3, 10.0, "PY", "102")
    d.line([(9.3, 9.82), top], pattern=2, weight=W_SIGNAL)

    # Emergency stop valve and governor valve
    top = d.box_actuator_valve(10.0, 9.3, "S")
    d.text(10.0, 9.02, "ESV-101", size=5.5)
    d.line([top, (10.0, 10.9)], pattern=2, weight=W_SIGNAL)
    d.flag(10.0, 10.9, 1.3, 0.36, "TURBINE TRIP", "left", size=6)
    top = d.control_valve(10.85, 9.3)
    d.text(10.85, 9.02, "GV-101", size=5.5)
    d.line([top, (10.85, 10.3)], pattern=2, weight=W_SIGNAL)
    d.flag(10.85, 10.3, 1.25, 0.36, "GOVERNOR", "left", size=6)

    # Steam turbine ST-101, shaft and generator G-101
    d.polygon([(11.7, 9.05), (13.1, 8.75), (13.1, 9.85), (11.7, 9.55)], stroke=BLACK, fill=FILL_NEUTRAL, weight=W_EQUIP)
    d.text(12.4, 9.98, "STEAM TURBINE  ST-101", size=6.5)
    d.line([(13.1, 9.26), (13.8, 9.26)], weight=W_SYM)
    d.line([(13.1, 9.34), (13.8, 9.34)], weight=W_SYM)
    d.ellipse(14.3, 9.3, 0.5, 0.5, stroke=BLACK, fill=FILL_NEUTRAL, weight=W_EQUIP)
    d.path([("M", 13.95, 9.3), ("A", 14.3, 9.3, 14.125, 9.45), ("A", 14.65, 9.3, 14.475, 9.15)],
           stroke=BLACK, fill=None, weight=W_SYM)
    d.text(14.3, 10.05, "GENERATOR  G-101", size=6.5)
    d.line([(14.8, 9.3), (15.05, 9.3)], weight=W_SEC)
    d.flag(15.05, 9.3, 0.95, 0.4, "TO\nSWITCHYARD", "right", size=5.5)

    # Motive steam to ejector
    d.line([(9.7, 9.3), (9.7, 8.6), (10.17, 8.6), (10.17, 7.8)], stroke=STEAM, weight=W_SEC, end_arrow=4)
    d.text(9.38, 8.72, "MOTIVE\nSTEAM", size=5, bold=False)


def condenser_and_gas_removal(d: Drawing) -> None:
    # Turbine exhaust to direct-contact condenser E-201
    d.line([(12.6, 8.857), (12.6, 7.5)], stroke=STEAM, weight=W_MAIN, end_arrow=4)
    d.text(12.7, 8.2, "EXHAUST", size=5.5, bold=False, align="left")
    d.polygon([(11.8, 5.6), (13.4, 5.6), (13.4, 7.0), (12.85, 7.5), (12.35, 7.5), (11.8, 7.0)],
              stroke=BLACK, fill=FILL_WATER, weight=W_EQUIP)
    d.text(12.6, 6.35, "CONDENSER\nE-201", size=6.5)
    d.line([(11.8, 6.0), (11.58, 6.0)], weight=W_SYM)
    d.instrument(11.4, 6.0, "LT", "201")

    # Non-condensable gas to steam-jet ejector EJ-201 and after-condenser AC-201
    y_top = 7.0 + (11.95 - 11.8) / 0.55 * 0.5  # on the sloped condenser hood
    d.line([(11.95, y_top), (11.95, 7.7), (10.3, 7.7)], stroke=NCG, weight=W_SEC, end_arrow=4)
    d.line([(11.95, 7.4), (11.73, 7.4)], weight=W_SYM)
    d.instrument(11.55, 7.4, "PT", "201")
    d.text(11.15, 7.85, "NCG", size=5.5, bold=False)
    # ejector: suction chamber + diverging diffuser
    d.rect(10.05, 7.6, 0.25, 0.2, stroke=BLACK, fill=WHITE, weight=W_EQUIP)
    d.polygon([(10.05, 7.62), (9.7, 7.55), (9.7, 7.85), (10.05, 7.78)], stroke=BLACK, fill=WHITE, weight=W_EQUIP)
    d.text(10.0, 7.35, "EJECTOR\nEJ-201", size=5.5)
    d.line([(9.7, 7.7), (9.3, 7.7)], stroke=NCG, weight=W_SEC, end_arrow=4)

    d.vvessel(9.1, 7.15, 0.4, 0.8, FILL_WATER)
    d.text(8.35, 7.95, "AFTER\nCONDENSER\nAC-201", size=5.5)
    d.line([(9.1, 8.15), (9.1, 8.5)], stroke=NCG, weight=W_SEC, end_arrow=4)
    d.text(8.55, 8.4, "NCG TO ATM", size=5.5, bold=False)
    # cooling water to after-condenser and drain back to condenser inlet line
    d.line([(11.0, 5.8), (8.5, 5.8), (8.5, 7.5), (8.9, 7.5)], stroke=WATER, weight=W_SEC, end_arrow=4)
    d.line([(9.1, 6.95), (9.1, 6.6), (11.0, 6.6)], stroke=WATER, weight=W_SEC)


def cooling_water(d: Drawing) -> None:
    # Hot well pump P-201 to cooling tower CT-301
    d.line([(12.6, 5.6), (12.6, 5.0), (13.18, 5.0)], stroke=WATER, weight=W_SEC)
    d.pump(13.35, 5.0)
    d.text(13.35, 5.35, "P-201", size=5.5)
    d.line([(13.52, 5.0), (13.95, 5.0), (13.95, 4.8)], stroke=WATER, weight=W_SEC, end_arrow=4)
    d.line([(12.9, 5.0), (12.9, 4.78)], weight=W_SYM)
    d.instrument(12.9, 4.6, "TT", "201")

    d.polygon([(13.3, 3.0), (15.7, 3.0), (15.3, 4.8), (13.7, 4.8)], stroke=BLACK, fill=FILL_WATER, weight=W_EQUIP)
    d.rect(13.2, 2.75, 2.6, 0.25, stroke=BLACK, fill=FILL_WATER, weight=W_EQUIP)
    d.rect(14.1, 4.8, 0.8, 0.25, stroke=BLACK, fill=WHITE, weight=W_EQUIP)
    d.ellipse(14.5, 4.925, 0.3, 0.085, stroke=BLACK, fill=WHITE, weight=W_SYM)
    d.line([(14.2, 4.84), (14.8, 5.01)], weight=W_SYM)
    d.line([(14.2, 5.01), (14.8, 4.84)], weight=W_SYM)
    d.text(14.5, 4.1, "COOLING TOWER\nCT-301", size=6.5)

    # Cold water from basin: circulating pump P-301, riser to condenser, blowdown to reinjection
    d.line([(14.5, 2.75), (14.5, 2.3), (13.77, 2.3)], stroke=WATER, weight=W_SEC)
    d.pump(13.6, 2.3, direction="left")
    d.text(13.6, 1.95, "P-301", size=5.5)
    d.line([(13.43, 2.3), (11.0, 2.3), (11.0, 6.6), (11.8, 6.6)], stroke=WATER, weight=W_SEC, end_arrow=4)
    d.gate_valve(11.0, 4.2, vertical=True)
    d.text(11.4, 3.0, "COOLING\nWATER", size=5.5, bold=False)

    d.line([(11.0, 2.3), (2.9, 2.3), (2.2, 2.3)], stroke=WATER, weight=W_SEC, end_arrow=4)
    d.gate_valve(8.0, 2.3)
    d.text(8.0, 2.55, "BLOWDOWN", size=5.5, bold=False)
    d.flag(0.6, 2.3, 1.6, 0.4, "TO REINJECTION\nWELLS", "left")


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def bbox(it: Item):
    if it.kind == "ellipse":
        return it.cx - it.rx, it.cy - it.ry, it.cx + it.rx, it.cy + it.ry
    if it.kind == "text":
        w, h = text_box(it)
        if abs(it.angle) > 1:
            w, h = h, w
        return it.cx - w / 2, it.cy - h / 2, it.cx + w / 2, it.cy + h / 2
    xs, ys = [], []
    for s in it.segs:
        xs.append(s[1]); ys.append(s[2])
        if s[0] == "A":
            xs.append(s[3]); ys.append(s[4])
    return min(xs), min(ys), max(xs), max(ys)


def _text_width(s: str, size: float) -> float:
    return max(len(line) for line in s.split("\n")) * size * PT * 0.72 + 0.12


def text_box(it: Item):
    lines = it.text.split("\n")
    em = it.size * PT
    w = it.tw if it.tw is not None else _text_width(it.text, it.size)
    h = it.th if it.th is not None else len(lines) * em * 1.3 + 0.06
    return w, h


# ---------------------------------------------------------------------------
# Visio (VSDX) emitter
# ---------------------------------------------------------------------------
NS_MAIN = "http://schemas.microsoft.com/office/visio/2012/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def f(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") if v else "0"


def cell(n, v, formula=None, unit=None):
    s = f'<Cell N="{n}" V="{escape(str(v))}"'
    if unit:
        s += f' U="{unit}"'
    if formula:
        s += f' F="{escape(formula)}"'
    return s + "/>"


def rel_formula(val, size, name):
    if size <= 1e-9:
        return "0", None
    ratio = val / size
    return f(val), f"{name}*{f(ratio)}"


def geometry_xml(it: Item, x0, y0, w, h) -> str:
    rows = []
    ix = 1

    def pt_cells(x, y):
        vx, fx = rel_formula(x - x0, w, "Width")
        vy, fy = rel_formula(y - y0, h, "Height")
        return cell("X", vx, fx) + cell("Y", vy, fy)

    if it.kind == "ellipse":
        rows.append(
            '<Row T="Ellipse" IX="1">'
            + cell("X", f(w / 2), "Width*0.5") + cell("Y", f(h / 2), "Height*0.5")
            + cell("A", f(w), "Width*1") + cell("B", f(h / 2), "Height*0.5")
            + cell("C", f(w / 2), "Width*0.5") + cell("D", f(h), "Height*1")
            + "</Row>"
        )
    else:
        for s in it.segs:
            if s[0] == "M":
                rows.append(f'<Row T="MoveTo" IX="{ix}">' + pt_cells(s[1], s[2]) + "</Row>")
            elif s[0] == "L":
                rows.append(f'<Row T="LineTo" IX="{ix}">' + pt_cells(s[1], s[2]) + "</Row>")
            elif s[0] == "A":
                vax, fax = rel_formula(s[3] - x0, w, "Width")
                vay, fay = rel_formula(s[4] - y0, h, "Height")
                rows.append(
                    f'<Row T="EllipticalArcTo" IX="{ix}">' + pt_cells(s[1], s[2])
                    + cell("A", vax, fax) + cell("B", vay, fay) + cell("C", "0") + cell("D", "1") + "</Row>"
                )
            ix += 1
        if it.closed:
            first = it.segs[0]
            rows.append(f'<Row T="LineTo" IX="{ix}">' + pt_cells(first[1], first[2]) + "</Row>")
    no_fill = 0 if it.fill else 1
    return (
        '<Section N="Geometry" IX="0">'
        + cell("NoFill", no_fill) + cell("NoLine", 0) + cell("NoShow", 0) + cell("NoSnap", 0) + cell("NoQuickDrag", 0)
        + "".join(rows) + "</Section>"
    )


def shape_xml(sid: int, it: Item) -> str:
    x0, y0, x1, y1 = bbox(it)
    if it.kind == "text" and abs(it.angle) > 1:
        # store the unrotated box; Visio applies Angle about the pin
        w, h = text_box(it)
    else:
        w, h = x1 - x0, y1 - y0
    pinx, piny = (x0 + x1) / 2, (y0 + y1) / 2
    parts = [f'<Shape ID="{sid}" Type="Shape" LineStyle="3" FillStyle="3" TextStyle="3">']
    parts += [
        cell("PinX", f(pinx)), cell("PinY", f(piny)), cell("Width", f(w)), cell("Height", f(h)),
        cell("LocPinX", f(w / 2), "Width*0.5"), cell("LocPinY", f(h / 2), "Height*0.5"),
        cell("Angle", f(math.radians(it.angle))), cell("FlipX", 0), cell("FlipY", 0), cell("ResizeMode", 0),
    ]
    if it.kind == "text":
        parts += [cell("LinePattern", 0), cell("FillPattern", 0)]
    else:
        parts += [
            cell("LineWeight", f(it.weight)), cell("LineColor", it.stroke or BLACK), cell("LinePattern", it.pattern),
            cell("LineCap", 0), cell("Rounding", 0),
            cell("BeginArrow", it.begin_arrow), cell("EndArrow", it.end_arrow),
            cell("BeginArrowSize", 1), cell("EndArrowSize", 1),
            cell("FillForegnd", it.fill or WHITE), cell("FillPattern", 1 if it.fill else 0),
        ]
    if it.text is not None:
        m = 0.0 if (not it.margins or it.kind == "text") else 0.03
        parts += [cell("LeftMargin", f(m)), cell("RightMargin", f(m)), cell("TopMargin", f(m)),
                  cell("BottomMargin", f(m)), cell("VerticalAlign", 1), cell("TextBkgnd", 0)]
    if it.kind != "text":
        parts.append(geometry_xml(it, x0, y0, w, h))
    if it.text is not None:
        parts.append(
            '<Section N="Character"><Row IX="0">'
            + cell("Font", FONT) + cell("Color", it.color) + cell("Style", 1 if it.bold else 0)
            + cell("Case", 0) + cell("Pos", 0) + cell("FontScale", 1) + cell("Size", f(it.size * PT), unit="PT")
            + cell("AsianFont", FONT) + cell("ComplexScriptFont", FONT) + cell("LangID", "en-US")
            + "</Row></Section>"
            '<Section N="Paragraph"><Row IX="0">' + cell("HorzAlign", 0 if it.align == "left" else 1)
            + cell("SpLine", -1.1) + "</Row></Section>"
        )
        parts.append(f'<Text><cp IX="0"/><pp IX="0"/>{escape(it.text)}</Text>')
    parts.append("</Shape>")
    return "".join(parts)


def page_xml(d: Drawing) -> str:
    shapes = "".join(shape_xml(i + 1, it) for i, it in enumerate(d.items))
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<PageContents xmlns="{NS_MAIN}" xmlns:r="{NS_R}" xml:space="preserve">'
        f"<Shapes>{shapes}</Shapes></PageContents>"
    )


def pages_xml() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<Pages xmlns="{NS_MAIN}" xmlns:r="{NS_R}" xml:space="preserve">'
        '<Page ID="0" NameU="P&amp;ID" Name="P&amp;ID" ViewScale="0.6" '
        f'ViewCenterX="{f(PAGE_W / 2)}" ViewCenterY="{f(PAGE_H / 2)}">'
        '<PageSheet LineStyle="0" FillStyle="0" TextStyle="0">'
        + cell("PageWidth", f(PAGE_W)) + cell("PageHeight", f(PAGE_H))
        + cell("ShdwOffsetX", 0.125) + cell("ShdwOffsetY", -0.125)
        + cell("PageScale", 1, unit="IN_F") + cell("DrawingScale", 1, unit="IN_F")
        + cell("DrawingSizeType", 3) + cell("DrawingScaleType", 0) + cell("InhibitSnap", 0)
        + cell("PageLockReplace", 0, unit="BOOL") + cell("PageLockDuplicate", 0, unit="BOOL")
        + cell("UIVisibility", 0) + cell("ShdwType", 0) + cell("ShdwObliqueAngle", 0)
        + cell("ShdwScaleFactor", 1) + cell("DrawingResizeType", 2) + cell("PageShapeSplit", 1)
        + cell("PaperKind", 8) + cell("PrintPageOrientation", 2)
        + cell("PageLeftMargin", 0.25) + cell("PageRightMargin", 0.25)
        + cell("PageTopMargin", 0.25) + cell("PageBottomMargin", 0.25)
        + '</PageSheet><Rel r:id="rId1"/></Page></Pages>'
    )


def document_xml() -> str:
    no_style = "".join([
        cell("EnableLineProps", 1), cell("EnableFillProps", 1), cell("EnableTextProps", 1), cell("HideForApply", 0),
        cell("LineWeight", 0.01), cell("LineColor", "#000000"), cell("LinePattern", 1), cell("Rounding", 0),
        cell("EndArrowSize", 2), cell("BeginArrow", 0), cell("EndArrow", 0), cell("LineCap", 0),
        cell("BeginArrowSize", 2), cell("LineColorTrans", 0), cell("CompoundType", 0),
        cell("FillForegnd", "#ffffff"), cell("FillBkgnd", "#000000"), cell("FillPattern", 1),
        cell("ShdwForegnd", "#000000"), cell("ShdwPattern", 0), cell("FillForegndTrans", 0), cell("FillBkgndTrans", 0),
        cell("ShdwForegndTrans", 0), cell("ShapeShdwType", 0), cell("ShapeShdwOffsetX", 0), cell("ShapeShdwOffsetY", 0),
        cell("ShapeShdwObliqueAngle", 0), cell("ShapeShdwScaleFactor", 1), cell("ShapeShdwBlur", 0), cell("ShapeShdwShow", 0),
        cell("LeftMargin", 0.0555555555555556), cell("RightMargin", 0.0555555555555556),
        cell("TopMargin", 0.0555555555555556), cell("BottomMargin", 0.0555555555555556),
        cell("VerticalAlign", 1), cell("TextBkgnd", 0), cell("DefaultTabStop", 0.5), cell("TextDirection", 0),
        cell("TextBkgndTrans", 0), cell("LockWidth", 0), cell("LockHeight", 0), cell("LockMoveX", 0), cell("LockMoveY", 0),
        cell("LockAspect", 0), cell("LockDelete", 0), cell("LockBegin", 0), cell("LockEnd", 0), cell("LockRotate", 0),
        cell("LockCrop", 0), cell("LockVtxEdit", 0), cell("LockTextEdit", 0), cell("LockFormat", 0), cell("LockGroup", 0),
        cell("LockCalcWH", 0), cell("LockSelect", 0), cell("LockCustProp", 0), cell("LockFromGroupFormat", 0),
        cell("LockThemeColors", 0), cell("LockThemeEffects", 0), cell("LockThemeConnectors", 0), cell("LockThemeFonts", 0),
        cell("LockThemeIndex", 0), cell("LockReplace", 0), cell("LockVariation", 0),
        cell("NoObjHandles", 0), cell("NonPrinting", 0), cell("NoCtlHandles", 0), cell("NoAlignBox", 0),
        cell("UpdateAlignBox", 0), cell("HideText", 0), cell("DynFeedback", 0), cell("GlueType", 0), cell("WalkPreference", 0),
        cell("BegTrigger", 0), cell("EndTrigger", 0), cell("ObjType", 0), cell("Comment", ""), cell("IsDropSource", 0),
        cell("NoLiveDynamics", 0), cell("LocalizeMerge", 0), cell("NoProofing", 0), cell("Calendar", 0), cell("LangID", "en-US"),
        cell("ShapeKeywords", ""), cell("DropOnPageScale", 1), cell("TheData", 0), cell("TheText", 0), cell("EventDblClick", 0),
        cell("EventXFMod", 0), cell("EventDrop", 0), cell("EventMultiDrop", 0), cell("HelpTopic", ""), cell("Copyright", ""),
        cell("LayerMember", ""), cell("Gamma", 1), cell("Contrast", 0.5), cell("Brightness", 0.5), cell("Sharpen", 0),
        cell("Blur", 0), cell("Denoise", 0), cell("Transparency", 0), cell("SelectMode", 1), cell("DisplayMode", 2),
        cell("IsTextEditTarget", 1), cell("IsSnapTarget", 1), cell("IsDropTarget", 0), cell("DontMoveChildren", 0),
        cell("ShapePermeableX", 0), cell("ShapePermeableY", 0), cell("ShapePermeablePlace", 0), cell("ShapeFixedCode", 0),
        cell("ShapePlowCode", 0), cell("ShapeRouteStyle", 0), cell("ConFixedCode", 0), cell("ConLineJumpCode", 0),
        cell("ConLineJumpStyle", 0), cell("ConLineJumpDirX", 0), cell("ConLineJumpDirY", 0), cell("ShapePlaceFlip", 0),
        cell("ConLineRouteExt", 0), cell("ShapePlaceStyle", 0), cell("ShapeSplit", 0), cell("ShapeSplittable", 0),
        cell("DisplayLevel", 0), cell("Relationships", 0),
        cell("ImageOffsetX", 0), cell("ImageOffsetY", 0), cell("ImageWidth", 0), cell("ImageHeight", 0),
        '<Section N="Character"><Row IX="0">'
        + cell("Font", FONT) + cell("Color", "#000000") + cell("Style", 0) + cell("Case", 0) + cell("Pos", 0)
        + cell("FontScale", 1) + cell("Size", 0.1666666666666667) + cell("DblUnderline", 0) + cell("Overline", 0)
        + cell("Strikethru", 0) + cell("Highlight", 0) + cell("DoubleStrikethrough", 0) + cell("RTLText", 0)
        + cell("UseVertical", 0) + cell("Letterspace", 0) + cell("ColorTrans", 0) + cell("AsianFont", FONT)
        + cell("ComplexScriptFont", FONT) + cell("LocalizeFont", 0) + cell("ComplexScriptSize", -1) + cell("LangID", "en-US")
        + "</Row></Section>",
        '<Section N="Paragraph"><Row IX="0">'
        + cell("IndFirst", 0) + cell("IndLeft", 0) + cell("IndRight", 0) + cell("SpLine", -1.2) + cell("SpBefore", 0)
        + cell("SpAfter", 0) + cell("HorzAlign", 1) + cell("Bullet", 0) + cell("BulletStr", "") + cell("BulletFont", 0)
        + cell("LocalizeBulletFont", 0) + cell("BulletFontSize", -1) + cell("TextPosAfterBullet", 0) + cell("Flags", 0)
        + "</Row></Section>",
        '<Section N="Tabs"><Row IX="0"/></Section>',
    ])
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<VisioDocument xmlns="{NS_MAIN}" xmlns:r="{NS_R}" xml:space="preserve">'
        '<DocumentSettings TopPage="0" DefaultTextStyle="3" DefaultLineStyle="3" DefaultFillStyle="3" DefaultGuideStyle="4">'
        "<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings><SnapExtensions>34</SnapExtensions>"
        "<SnapAngles/><DynamicGridEnabled>1</DynamicGridEnabled><ProtectStyles>0</ProtectStyles>"
        "<ProtectShapes>0</ProtectShapes><ProtectMasters>0</ProtectMasters><ProtectBkgnds>0</ProtectBkgnds>"
        "</DocumentSettings>"
        "<Colors>" + "".join(
            f'<ColorEntry IX="{i}" RGB="{c}"/>' for i, c in enumerate([
                "#000000", "#FFFFFF", "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF",
                "#800000", "#008000", "#000080", "#808000", "#800080", "#008080", "#C0C0C0", "#E6E6E6",
                "#CDCDCD", "#B3B3B3", "#9A9A9A", "#808080", "#666666", "#4D4D4D", "#333333", "#1A1A1A"])
        ) + "</Colors>"
        f'<FaceNames><FaceName NameU="{FONT}" UnicodeRanges="-536859905 -1073711039 9 0" '
        'CharSets="1073742335 -65536" Panos="2 2 6 3 5 4 5 2 3 4" Flags="325"/></FaceNames>'
        "<StyleSheets>"
        f'<StyleSheet ID="0" NameU="No Style" Name="No Style">{no_style}</StyleSheet>'
        '<StyleSheet ID="1" NameU="Text Only" Name="Text Only" LineStyle="3" FillStyle="3" TextStyle="3">'
        + cell("LinePattern", 0) + cell("FillPattern", 0) + cell("HideForApply", 0) + "</StyleSheet>"
        '<StyleSheet ID="2" NameU="None" Name="None" LineStyle="3" FillStyle="3" TextStyle="3">'
        + cell("LinePattern", 0) + cell("FillPattern", 0) + cell("HideForApply", 0) + "</StyleSheet>"
        '<StyleSheet ID="3" NameU="Normal" Name="Normal" LineStyle="0" FillStyle="0" TextStyle="0">'
        + cell("HideForApply", 0) + "</StyleSheet>"
        '<StyleSheet ID="4" NameU="Guide" Name="Guide" LineStyle="3" FillStyle="3" TextStyle="3">'
        + cell("LineWeight", 0) + cell("LineColor", "#7f7f7f") + cell("LinePattern", 23) + cell("FillPattern", 0)
        + cell("HideForApply", 0) + cell("NoObjHandles", 1) + cell("NonPrinting", 1) + cell("NoCtlHandles", 1)
        + cell("NoAlignBox", 1) + "</StyleSheet>"
        "</StyleSheets>"
        '<DocumentSheet NameU="TheDoc" Name="TheDoc" LineStyle="0" FillStyle="0" TextStyle="0">'
        + cell("OutputFormat", 0) + cell("LockPreview", 0) + cell("AddMarkup", 0) + cell("ViewMarkup", 0)
        + cell("PreviewQuality", 0) + cell("PreviewScope", 0) + cell("DocLangID", "en-US")
        + "</DocumentSheet></VisioDocument>"
    )


def write_vsdx(d: Drawing, path: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>'
            '<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>'
            '<Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>"
        ),
        "docProps/core.xml": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>Geothermal Power Plant P&amp;ID</dc:title><dc:subject>GPP-PID-001</dc:subject><dc:creator></dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            "</cp:coreProperties>"
        ),
        "docProps/app.xml": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Application>Microsoft Visio</Application><Template></Template><Company></Company></Properties>"
        ),
        "visio/document.xml": document_xml(),
        "visio/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>'
            "</Relationships>"
        ),
        "visio/pages/pages.xml": pages_xml(),
        "visio/pages/_rels/pages.xml.rels": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page" Target="page1.xml"/>'
            "</Relationships>"
        ),
        "visio/pages/page1.xml": page_xml(d),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data.encode("utf-8"))


# ---------------------------------------------------------------------------
# SVG emitter (preview only)
# ---------------------------------------------------------------------------
def write_svg(d: Drawing, path: str, dpi: float = 96.0) -> None:
    def X(x):
        return f"{x * dpi:.2f}"

    def Y(y):
        return f"{(PAGE_H - y) * dpi:.2f}"

    markers = {}
    out = []
    for it in d.items:
        if it.kind == "text":
            w, h = text_box(it)
            lines = it.text.split("\n")
            fs = it.size * dpi / 72
            weight = "bold" if it.bold else "normal"
            tr = f' transform="rotate({-it.angle} {X(it.cx)} {Y(it.cy)})"' if it.angle else ""
            total = len(lines)
            ax = it.cx - w / 2 if it.align == "left" else it.cx
            anchor = "start" if it.align == "left" else "middle"
            spans = "".join(
                f'<tspan x="{X(ax)}" dy="{(0 if i == 0 else 1.15)}em">{escape(s)}</tspan>' for i, s in enumerate(lines)
            )
            y0 = float(Y(it.cy)) - (total - 1) * 1.15 * fs / 2
            out.append(
                f'<text x="{X(ax)}" y="{y0:.2f}" font-size="{fs:.2f}" font-weight="{weight}" fill="{it.color}" '
                f'text-anchor="{anchor}" dominant-baseline="central"{tr}>{spans}</text>'
            )
            continue
        stroke = it.stroke or "none"
        fill = it.fill or "none"
        sw = it.weight * dpi
        dash = f' stroke-dasharray="{sw * 5:.2f} {sw * 3:.2f}"' if it.pattern == 2 else ""
        mk = ""
        if it.end_arrow:
            mid = "m" + it.stroke.strip("#")
            markers[mid] = it.stroke
            mk = f' marker-end="url(#{mid})"'
        if it.kind == "ellipse":
            out.append(
                f'<ellipse cx="{X(it.cx)}" cy="{Y(it.cy)}" rx="{it.rx * dpi:.2f}" ry="{it.ry * dpi:.2f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}"/>'
            )
        else:
            dd = []
            cur = None
            for s in it.segs:
                if s[0] == "M":
                    dd.append(f"M{X(s[1])} {Y(s[2])}")
                elif s[0] == "L":
                    dd.append(f"L{X(s[1])} {Y(s[2])}")
                else:
                    dd.append(arc_svg(cur, (s[1], s[2]), (s[3], s[4]), dpi))
                cur = (s[1], s[2])
            if it.closed:
                dd.append("Z")
            out.append(
                f'<path d="{" ".join(dd)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}" '
                f'stroke-linejoin="round" stroke-linecap="butt"{dash}{mk}/>'
            )
        if it.text is not None:
            x0, y0, x1, y1 = bbox(it)
            lines = it.text.split("\n")
            fs = it.size * dpi / 72
            weight = "bold" if it.bold else "normal"
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            total = len(lines)
            spans = "".join(
                f'<tspan x="{X(cx)}" dy="{(0 if i == 0 else 1.1)}em">{escape(s)}</tspan>' for i, s in enumerate(lines)
            )
            yy = float(Y(cy)) - (total - 1) * 1.1 * fs / 2
            out.append(
                f'<text x="{X(cx)}" y="{yy:.2f}" font-size="{fs:.2f}" font-weight="{weight}" fill="{it.color}" '
                f'text-anchor="middle" dominant-baseline="central">{spans}</text>'
            )
    defs = "".join(
        f'<marker id="{mid}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse">'
        f'<path d="M0 0.5L8 4 0 7.5Z" fill="{c}"/></marker>' for mid, c in markers.items()
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_W * dpi:.0f}" height="{PAGE_H * dpi:.0f}" '
        f'viewBox="0 0 {PAGE_W * dpi:.2f} {PAGE_H * dpi:.2f}" font-family="Times New Roman, Liberation Serif, DejaVu Serif, serif">'
        f"<defs>{defs}</defs><rect width=\"100%\" height=\"100%\" fill=\"white\"/>{''.join(out)}</svg>"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)


def arc_svg(p0, p1, ctrl, dpi):
    """Circular arc from p0 to p1 passing through ctrl, as an SVG A command."""
    (x0, y0), (x1, y1), (xc, yc) = p0, p1, ctrl
    ax, ay, bx, by, cx, cy = x0, y0, xc, yc, x1, y1
    dd = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / dd
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / dd
    r = math.hypot(ax - ux, ay - uy)
    cross_ctrl = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    cross_center = (ux - ax) * (cy - ay) - (uy - ay) * (cx - ax)
    # positive cross => control point right of the chord (page space, y up) => counter-clockwise on a y-down screen
    sweep = 0 if cross_ctrl > 0 else 1
    large = 1 if abs(cross_center) > 1e-9 and (cross_ctrl > 0) == (cross_center > 0) else 0
    return f"A{r * dpi:.2f} {r * dpi:.2f} 0 {large} {sweep} {x1 * dpi:.2f} {(PAGE_H - y1) * dpi:.2f}"


if __name__ == "__main__":
    drawing = build()
    write_vsdx(drawing, "Geothermal_Power_Plant_PID.vsdx")
    write_svg(drawing, "Geothermal_Power_Plant_PID_preview.svg")
    print(f"{len(drawing.items)} shapes written")
