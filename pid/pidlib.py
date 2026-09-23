"""Drawing primitives for the P&ID generator.

Coordinates are inches, origin bottom-left (Visio page convention).
Every primitive becomes one Visio shape: local-coordinate geometry + style + text.
"""

C_PROC, C_RECIRC, C_BYPASS, C_SIG, C_STEAM, C_INK = "#1F3B73", "#B34700", "#2E7D32", "#7A1FA2", "#C62828", "#000000"
F_EQ, F_PUMP, F_VALVE, F_WHITE, F_NOTE = "#EEF3FF", "#FFE9C9", "#E6FFE6", "#FFFFFF", "#FFFBE6"
LAYERS = ["Equipment", "Process Piping", "Recirc-Bypass", "Valves", "Instruments", "Signal Lines", "Annotation"]
PT = 1 / 72.0  # inch per point

shapes = []
_id = [0]
STYLE = {"mono": False}  # mono: black ink, white fills (classic drawing-office look)
_MONO_FILL = {F_EQ: F_WHITE, F_PUMP: F_WHITE, F_VALVE: F_WHITE, F_NOTE: F_WHITE, "#FFE0E0": F_WHITE, C_PROC: C_INK}
instr_index, line_list, valve_list = [], [], []


def nid():
    _id[0] += 1
    return _id[0]


class Shape:
    def __init__(self, x0, y0, w, h, layer, text="", size=7, bold=False, halign=1, valign=1,
                 line=C_INK, weight=1.0, pattern=1, fill=None, end_arrow=0, begin_arrow=0,
                 props=None, text_color=C_INK, name=None):
        self.id = nid()
        self.x0, self.y0, self.w, self.h = x0, y0, max(w, 0.04), max(h, 0.04)
        self.layer, self.text, self.size, self.bold = layer, text, size, bold
        self.halign, self.valign = halign, valign
        if STYLE["mono"]:
            fill = _MONO_FILL.get(fill, fill)
            line = C_INK
            text_color = C_INK
        self.line, self.weight, self.pattern, self.fill = line, weight, pattern, fill
        self.end_arrow, self.begin_arrow = end_arrow, begin_arrow
        self.props = props or {}
        self.text_color = text_color
        self.name = name
        # geoms: list of (rows, nofill, noline); rows: ("M",x,y) ("L",x,y) ("A",x,y,bow) ("E",cx,cy,rx,ry)
        self.geoms = []
        shapes.append(self)

    def poly(self, pts, close=False, nofill=True):
        rows = [("M",) + tuple(pts[0])] + [("L",) + tuple(p) for p in pts[1:]]
        if close:
            rows.append(("L",) + tuple(pts[0]))
        self.geoms.append((rows, nofill, False))
        return self

    def rect(self, x, y, w, h, nofill=False):
        return self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True, nofill=nofill)

    def ellipse(self, cx, cy, rx, ry, nofill=False):
        self.geoms.append(([("E", cx, cy, rx, ry)], nofill, False))
        return self

    def arc(self, p0, p1, bow, nofill=True):
        self.geoms.append(([("M",) + tuple(p0), ("A",) + tuple(p1) + (bow,)], nofill, False))
        return self


_LINE_STYLE = {
    "process": (C_PROC, 2.2, 1), "recirc": (C_RECIRC, 1.6, 2), "bypass": (C_BYPASS, 1.6, 2),
    "steam": (C_STEAM, 1.6, 1), "signal": (C_SIG, 0.75, 2), "software": (C_SIG, 0.75, 3),
    "leader": (C_INK, 0.5, 1), "thin": (C_INK, 0.6, 1), "hidden": (C_INK, 0.8, 3),
    "blk": (C_INK, 0.9, 1), "dsh": (C_INK, 0.7, 2),
}
_LINE_LAYER = {"process": "Process Piping", "steam": "Process Piping", "recirc": "Recirc-Bypass",
               "bypass": "Recirc-Bypass", "signal": "Signal Lines", "software": "Signal Lines"}


def line(pts, kind="process", label=None, lpos=None, arrow=True, weight=None, lsize=6.5, lalign=1, layer=None,
         color=None, ticks=False):
    col, wt, pat = _LINE_STYLE[kind]
    if STYLE["mono"]:
        col = C_INK
    if color:
        col = color
    if weight:
        wt = weight
    if ticks:
        tick_marks(pts, col)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    pad = 0.02  # keeps pure horizontal/vertical lines from collapsing to zero width/height
    s = Shape(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad, layer or _LINE_LAYER.get(kind, "Annotation"),
              line=col, weight=wt, pattern=pat, end_arrow=4 if arrow else 0, name=label or kind)
    s.poly([(x - s.x0, y - s.y0) for x, y in pts], nofill=True)
    if label:
        lx, ly = lpos if lpos else ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2 + 0.16)
        text(lx, ly, label, size=lsize, color=col, halign=lalign)
    return s


def tick_marks(pts, color=C_INK, size=0.055):
    """Visio-style '//' marks at the midpoint of each long segment (one shape, solid pattern)."""
    import math
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = min(xs) - 0.1, min(ys) - 0.1
    s = Shape(x0, y0, max(xs) - x0 + 0.1, max(ys) - y0 + 0.1, "Annotation", weight=0.7, line=color, name="ticks")
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        L = math.hypot(bx - ax, by - ay)
        if L < 0.55:
            continue
        dx, dy = (bx - ax) / L, (by - ay) / L
        mx, my = (ax + bx) / 2, (ay + by) / 2
        ux, uy = (dx - dy) / math.sqrt(2), (dy + dx) / math.sqrt(2)  # 45 deg to the segment
        for o in (-0.035, 0.035):
            cx, cy = mx + o * dx, my + o * dy
            s.poly([(cx - ux * size - x0, cy - uy * size - y0), (cx + ux * size - x0, cy + uy * size - y0)], nofill=True)
    if not s.geoms:
        shapes.remove(s)
    return s


def text(cx, cy, s, size=7, bold=False, color=C_INK, halign=1, w=None, h=None, layer="Annotation", valign=1):
    lines = s.split("\n")
    w = w or max(len(l) for l in lines) * size * PT * 0.62 + 0.12
    h = h or len(lines) * size * PT * 1.25 + 0.06
    return Shape(cx - w / 2, cy - h / 2, w, h, layer, text=s, size=size, bold=bold, halign=halign, valign=valign,
                 pattern=0, fill=None, text_color=color, name="label")


def ltext(x, cy, s, **kw):
    """Left-aligned text with its left edge at x."""
    w = kw.pop("w", None) or max(len(l) for l in s.split("\n")) * kw.get("size", 7) * PT * 0.62 + 0.12
    return text(x + w / 2, cy, s, halign=0, w=w, **kw)


def rtext(x, cy, s, **kw):
    """Right-aligned text with its right edge at x."""
    w = kw.pop("w", None) or max(len(l) for l in s.split("\n")) * kw.get("size", 7) * PT * 0.62 + 0.12
    return text(x - w / 2, cy, s, halign=2, w=w, **kw)


def box(cx, cy, w, h, label, fill=F_EQ, size=7.5, bold=True, layer="Equipment", pattern=1, weight=1.4, props=None, halign=1):
    s = Shape(cx - w / 2, cy - h / 2, w, h, layer, text=label, size=size, bold=bold, weight=weight, pattern=pattern,
              fill=fill, props=props, halign=halign, name=label.split("\n")[0] or "box")
    s.rect(0, 0, w, h)
    return s


def vessel_h(cx, cy, w, h, label, props=None, stubs=(), size=7.5, weight=1.4, e=None):
    """Horizontal capsule. stubs: list of (side 't'|'b'|'l'|'r', fraction along side) nozzle stubs."""
    s = Shape(cx - w / 2, cy - h / 2, w, h, "Equipment", text=label, size=size, bold=True, weight=weight, fill=F_EQ,
              props=props, name=label.split("\n")[0] or "vessel")
    e = e if e is not None else h * 0.18
    s.geoms.append(([("M", e, 0), ("L", w - e, 0), ("A", w - e, h, e), ("L", e, h), ("A", e, 0, e)], False, False))
    _stubs(s, w, h, stubs)
    return s


def _stubs(s, w, h, stubs, a=0.14, b=0.07):
    for side, f in stubs:
        if side == "t":
            s.rect(f * w - a / 2, h, a, b)
        elif side == "b":
            s.rect(f * w - a / 2, -b, a, b)
        elif side == "l":
            s.rect(-b, f * h - a / 2, b, a)
        elif side == "r":
            s.rect(w, f * h - a / 2, b, a)


def vessel_v(cx, cy, w, h, label, props=None, stubs=(), size=7.5, weight=1.4, e=None):
    s = Shape(cx - w / 2, cy - h / 2, w, h, "Equipment", text=label, size=size, bold=True, weight=weight, fill=F_EQ,
              props=props, name=label.split("\n")[0] or "vessel")
    e = e if e is not None else w * 0.18
    s.geoms.append(([("M", 0, e), ("L", 0, h - e), ("A", w, h - e, -e), ("L", w, e), ("A", 0, e, -e)], False, False))
    _stubs(s, w, h, stubs)
    return s


def exchanger(cx, cy, w, h, label, props=None, label_dy=None):
    """Shell & tube: rounded shell + tube-side zigzag."""
    s = Shape(cx - w / 2, cy - h / 2, w, h, "Equipment", text="", weight=1.4, fill=F_EQ, props=props, name=label.split("\n")[0])
    e = h * 0.2
    s.geoms.append(([("M", e, 0), ("L", w - e, 0), ("A", w - e, h, e), ("L", e, h), ("A", e, 0, e)], False, False))
    z = [(0, h / 2), (e, h / 2)]
    n = 6
    step = (w - 2 * e) / n
    for i in range(n):
        z.append((e + step * (i + 0.5), h / 2 + (h * 0.28 if i % 2 == 0 else -h * 0.28)))
    z += [(w - e, h / 2), (w, h / 2)]
    s.poly(z, nofill=True)
    text(cx, cy + (label_dy if label_dy is not None else h / 2 + 0.28), label, size=7.5, bold=True)
    return s


def pump(cx, cy, r, label, props=None):
    s = Shape(cx - r, cy - r, 2 * r, 2 * r, "Equipment", text="", weight=1.4, fill=F_PUMP, props=props, name=label.split("\n")[0])
    s.ellipse(r, r, r, r)
    s.poly([(r * 0.45, r * 0.55), (r * 1.6, r), (r * 0.45, r * 1.45)], close=True, nofill=True)
    text(cx, cy + r + 0.05 + 0.09 * len(label.split("\n")), label, size=6.5, bold=True)
    return s


def valve(cx, cy, tag, act="none", fo="", w=0.36, h=0.2, orient="h", size_txt="", props=None, label_side="r"):
    """Bow-tie body. act: none | M motor | D diaphragm control | C check. Actuator scales with body width."""
    k = w / 0.36
    top = (0.34 if act in ("M", "D") else 0.0) * k
    if orient == "v" and act == "D":
        top = 0.26 * k
    stem, arc_r, arc_b = 0.16 * k, 0.17 * k, 0.15 * k
    if orient == "h":
        s = Shape(cx - w / 2, cy - h / 2, w, h + top, "Valves", weight=1.2, fill=F_VALVE, props=props, name=tag or "valve")
        s.poly([(0, 0), (w, h), (w, 0), (0, h)], close=True, nofill=False)
        if act == "M":
            s.poly([(w / 2, h / 2), (w / 2, h / 2 + stem)], nofill=True)
            s.rect(w / 2 - 0.11 * k, h / 2 + stem, 0.22 * k, 0.18 * k)
            text(cx, cy + h / 2 + 0.15 * k, "M", size=5.5 * max(k, 0.7), bold=True)
        elif act == "D":
            s.poly([(w / 2, h / 2), (w / 2, h / 2 + stem)], nofill=True)
            s.arc((w / 2 - arc_r, h / 2 + stem), (w / 2 + arc_r, h / 2 + stem), -arc_b)
            s.poly([(w / 2 - arc_r, h / 2 + stem), (w / 2 + arc_r, h / 2 + stem)], nofill=True)
        elif act == "C":
            s.poly([(w * 0.55, h * 0.15), (w * 0.9, h / 2), (w * 0.55, h * 0.85)], close=True, nofill=False)
        ty = cy - h / 2 - 0.13
    else:
        s = Shape(cx - h / 2 - top, cy - w / 2, h + top, w, "Valves", weight=1.2, fill=F_VALVE, props=props, name=tag or "valve")
        ox = top
        s.poly([(ox, 0), (ox + h, w), (ox + h, 0), (ox, w)], close=True, nofill=False)
        if act == "D":
            stem, arc_r, arc_b = 0.12 * k, 0.14 * k, 0.12 * k
            s.poly([(ox + h / 2, w / 2), (ox + h / 2 - stem, w / 2)], nofill=True)
            s.arc((ox + h / 2 - stem, w / 2 - arc_r), (ox + h / 2 - stem, w / 2 + arc_r), -arc_b)
            s.poly([(ox + h / 2 - stem, w / 2 - arc_r), (ox + h / 2 - stem, w / 2 + arc_r)], nofill=True)
        ty = cy
    lbl = tag + (("\n" + fo) if fo else "") + (("\n" + size_txt) if size_txt else "")
    if lbl:
        if orient == "h":
            text(cx, ty - 0.06 * lbl.count("\n"), lbl, size=6, bold=True)
        elif label_side == "r":
            ltext(cx + h / 2 + 0.08, cy, lbl, size=6, bold=True)
        else:
            rtext(cx - h / 2 - top - 0.06, cy, lbl, size=6, bold=True)
    return s


def bubble(cx, cy, tag, kind="field", r=0.24, props=None, bold=True, size=5.6, weight=0.9):
    """ISA-5.1: field = circle; dcs = circle in square + bar; sis = diamond in square; logic = hexagon."""
    s = Shape(cx - r, cy - r, 2 * r, 2 * r, "Instruments", text=tag, size=size, bold=bold, weight=weight, fill=F_WHITE,
              props=props or {"Tag": tag.replace("\n", "-")}, name=tag.replace("\n", "-"))
    if kind == "field":
        s.ellipse(r, r, r, r)
    elif kind == "dcs":
        s.rect(0, 0, 2 * r, 2 * r)
        s.ellipse(r, r, r, r)
        s.poly([(0.05, r), (2 * r - 0.05, r)], nofill=True)
    elif kind == "sis":
        s.rect(0, 0, 2 * r, 2 * r)
        s.poly([(r, 0), (2 * r, r), (r, 2 * r), (0, r)], close=True)
    elif kind == "logic":
        s.poly([(r * 0.5, 0), (r * 1.5, 0), (2 * r, r), (r * 1.5, 2 * r), (r * 0.5, 2 * r), (0, r)], close=True)
    return s


def flag(cx, cy, txt):
    r = 0.2
    s = Shape(cx - r, cy - r, 2 * r, 2 * r, "Signal Lines", text=txt, size=5.5, bold=True, weight=0.9, fill="#FFE0E0", name=txt)
    s.poly([(r, 0), (2 * r, r), (r, 2 * r), (0, r)], close=True)
    return s


def offpage(cx, cy, w, h, label, direction="in"):
    s = Shape(cx - w / 2, cy - h / 2, w, h, "Annotation", text=label, size=6.5, weight=1.0, fill=F_WHITE, name="OPC-" + label)
    a = 0.22
    if direction == "in":
        s.poly([(0, 0), (w - a, 0), (w, h / 2), (w - a, h), (0, h)], close=True)
    else:
        s.poly([(a, 0), (w, 0), (w, h), (a, h), (0, h / 2)], close=True)
    return s


def leader(x, y, tx, ty):
    return line([(x, y), (tx, ty)], "leader", arrow=False)


def tee(x, y, color=C_PROC):
    s = Shape(x - 0.05, y - 0.05, 0.1, 0.1, "Process Piping", weight=0.5, fill=color, line=color, name="tee")
    s.ellipse(0.05, 0.05, 0.05, 0.05)
    return s


def I(tag, kind, service, location, range_, io, note=""):
    instr_index.append([tag, kind, service, location, range_, io, note])


def L(no, frm, to, size, cls, fluid, cond, note=""):
    line_list.append([no, frm, to, size, cls, fluid, cond, note])


def V(tag, typ, size, fail, service, note=""):
    valve_list.append([tag, typ, size, fail, service, note])


def hexflag(cx, cy, w, h, txt, size=6.5):
    """Pointed-both-ends flag for external systems / logic (as in classic P&IDs)."""
    s = Shape(cx - w / 2, cy - h / 2, w, h, "Annotation", text=txt, size=size, bold=True, weight=1.0, fill=F_WHITE, name=txt)
    a = h * 0.5
    s.poly([(0, h / 2), (a, 0), (w - a, 0), (w, h / 2), (w - a, h), (a, h)], close=True)
    return s
