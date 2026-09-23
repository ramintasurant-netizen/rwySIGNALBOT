"""Render mathtext formulas to PNG so they appear as proper equations in Word."""
import hashlib, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

matplotlib.rcParams["mathtext.fontset"] = "stix"
matplotlib.rcParams["font.family"] = "STIXGeneral"

DPI = 400
OUT = os.path.join(os.path.dirname(__file__), "img")

def render(tex, fontsize=12):
    """Return (path, width_in, height_in) of the rendered formula at the given point size."""
    os.makedirs(OUT, exist_ok=True)
    key = hashlib.md5(f"{tex}|{fontsize}".encode()).hexdigest()[:12]
    path = os.path.join(OUT, f"eq_{key}.png")
    if not os.path.exists(path):
        fig = plt.figure(figsize=(0.1, 0.1))
        fig.text(0, 0, f"${tex}$", fontsize=fontsize)
        fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0.02, transparent=False, facecolor="white")
        plt.close(fig)
    with Image.open(path) as im:
        w, h = im.size
    return path, w / DPI, h / DPI

if __name__ == "__main__":
    print(render(r"\bar{x} = \dfrac{3{,}98 + 4{,}00 + 3{,}97}{5} = 3{,}98\ \mathrm{cm}"))
    print(render(r"\Delta V = \sqrt{\left(\frac{1}{2}\pi \bar{D}\,\bar{T}\,\Delta D\right)^2 + \left(\frac{1}{4}\pi \bar{D}^2\,\Delta T\right)^2}"))
