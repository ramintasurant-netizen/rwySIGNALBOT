"""Draw the experiment flowchart with standard flowchart symbols."""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Polygon, FancyArrowPatch

OUT = os.path.join(os.path.dirname(__file__), "assets", "flowchart.png")
FS = 8.5
W = 5.6      # default box width
H = 0.75     # default box height
EDGE = "#1f3b73"
FILL = {"term": "#dfe9f7", "proc": "#ffffff", "io": "#fff4d6", "dec": "#e8f5e9"}

def terminal(ax, x, y, text, w=2.6, h=0.6):
    ax.add_patch(FancyBboxPatch((x - w/2, y - h/2), w, h, boxstyle="round,pad=0,rounding_size=0.3",
                                fc=FILL["term"], ec=EDGE, lw=1.3))
    ax.text(x, y, text, ha="center", va="center", fontsize=FS, fontweight="bold")

def process(ax, x, y, text, w=W, h=H):
    ax.add_patch(Rectangle((x - w/2, y - h/2), w, h, fc=FILL["proc"], ec=EDGE, lw=1.3))
    ax.text(x, y, text, ha="center", va="center", fontsize=FS)

def io(ax, x, y, text, w=W, h=H, skew=0.3):
    pts = [(x - w/2 + skew, y + h/2), (x + w/2 + skew, y + h/2), (x + w/2 - skew, y - h/2), (x - w/2 - skew, y - h/2)]
    ax.add_patch(Polygon(pts, closed=True, fc=FILL["io"], ec=EDGE, lw=1.3))
    ax.text(x, y, text, ha="center", va="center", fontsize=FS)

def decision(ax, x, y, text, w=4.2, h=1.35):
    pts = [(x, y + h/2), (x + w/2, y), (x, y - h/2), (x - w/2, y)]
    ax.add_patch(Polygon(pts, closed=True, fc=FILL["dec"], ec=EDGE, lw=1.3))
    ax.text(x, y, text, ha="center", va="center", fontsize=FS)

def arrow(ax, p, q, label=None, lpos=None):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=12, lw=1.2, color="black",
                                 shrinkA=0, shrinkB=0))
    if label:
        ax.text(*(lpos or ((p[0]+q[0])/2, (p[1]+q[1])/2)), label, fontsize=FS-0.5, ha="left", va="bottom",
                fontstyle="italic")

def line(ax, pts):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color="black", lw=1.2)

def main():
    fig, ax = plt.subplots(figsize=(6.6, 11.4))
    ax.set_xlim(-4.9, 4.9); ax.set_ylim(-16.0, 0.5); ax.axis("off"); ax.set_aspect("equal")

    x = 0
    ys = {}
    y = 0
    terminal(ax, x, y, "Mulai"); ys["mulai"] = (y, 0.6)
    y -= 1.3; process(ax, x, y, "Menyiapkan alat dan bahan:\njangka sorong, mistar, silinder, balok, plat"); ys["siap"] = (y, H)
    y -= 1.3; process(ax, x, y, "Memeriksa titik nol (kalibrasi) alat ukur\ndan menentukan NST alat"); ys["kal"] = (y, H)
    y -= 1.35; process(ax, x, y, "Mengukur dimensi benda uji pada posisi berbeda\n(silinder: D, T ; balok & plat: P, L, T)"); ys["ukur"] = (y, H)
    y -= 1.3; io(ax, x, y, "Mencatat hasil pengukuran\npada tabel data"); ys["catat"] = (y, H)
    y -= 1.65; decision(ax, x, y, "Pengukuran\nsudah 5 kali?"); ys["d1"] = (y, 1.35)
    y -= 1.75; decision(ax, x, y, "Semua benda uji\nselesai diukur?"); ys["d2"] = (y, 1.35)
    y -= 1.7; process(ax, x, y, "Menghitung rata-rata ($\\bar{x}$) dan deviasi standar\n($\\Delta x$) setiap besaran"); ys["stat"] = (y, H)
    y -= 1.3; process(ax, x, y, "Menghitung volume dan ketidakpastiannya\n(perambatan ketidakpastian)"); ys["vol"] = (y, H)
    y -= 1.3; io(ax, x, y, "Menuliskan hasil $x = \\bar{x} \\pm \\Delta x$\ndalam aturan baku"); ys["tulis"] = (y, H)
    y -= 1.3; process(ax, x, y, "Menganalisis hasil dan\nmenyusun kesimpulan"); ys["analisa"] = (y, H)
    y -= 1.2; terminal(ax, x, y, "Selesai"); ys["selesai"] = (y, 0.6)

    order = ["mulai", "siap", "kal", "ukur", "catat", "d1", "d2", "stat", "vol", "tulis", "analisa", "selesai"]
    for a, b in zip(order, order[1:]):
        ya, ha = ys[a]; yb, hb = ys[b]
        label = "Ya" if a in ("d1", "d2") else None
        arrow(ax, (x, ya - ha/2), (x, yb + hb/2), label, (x + 0.1, (ya - ha/2 + yb + hb/2)/2 - 0.05))

    # Loop "Tidak" from decision 1 back to measuring step (right side)
    yd1, hd1 = ys["d1"]; yu, hu = ys["ukur"]
    line(ax, [(x + 2.1, yd1), (x + 4.2, yd1), (x + 4.2, yu)])
    arrow(ax, (x + 4.2, yu), (x + W/2, yu))
    ax.text(x + 2.2, yd1 + 0.08, "Tidak", fontsize=FS-0.5, fontstyle="italic", va="bottom")

    # Loop "Tidak" from decision 2: change object, back to measuring step (left side)
    yd2, hd2 = ys["d2"]
    line(ax, [(x - 2.1, yd2), (x - 4.2, yd2), (x - 4.2, yu)])
    arrow(ax, (x - 4.2, yu), (x - W/2, yu))
    ax.text(x - 2.2, yd2 + 0.08, "Tidak", fontsize=FS-0.5, fontstyle="italic", va="bottom", ha="right")
    ax.text(x - 4.3, (yd2 + yu)/2, "ganti\nbenda uji", fontsize=FS-1, ha="right", va="center", fontstyle="italic")

    fig.savefig(OUT, dpi=300, bbox_inches="tight", pad_inches=0.05, facecolor="white")
    print(OUT)

if __name__ == "__main__":
    main()
