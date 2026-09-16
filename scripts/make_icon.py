"""Generate the app icon: a white broom on a pink-to-purple rounded square.

Writes assets/icon.ico (16 to 256 px, used by the exe) and
src/igcleanup/ui/static/icon.png (64 px, used as the page favicon).
Run from the repo root: python scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 512
TOP = (255, 77, 150)      # accent pink
BOTTOM = (120, 40, 200)   # purple


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), TOP)
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        color = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        for x in range(size):
            px[x, y] = color
    return img


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def draw_broom(draw: ImageDraw.ImageDraw, size: int) -> None:
    s = size / 512
    white = (255, 255, 255)
    # handle: a thick diagonal line from top-right to lower-left
    draw.line([(372 * s, 96 * s), (216 * s, 292 * s)], fill=white, width=int(34 * s))
    draw.ellipse([(372 * s - 17 * s, 96 * s - 17 * s), (372 * s + 17 * s, 96 * s + 17 * s)], fill=white)
    # binding: a band where the handle meets the bristles
    draw.polygon([(190 * s, 262 * s), (252 * s, 326 * s), (232 * s, 346 * s), (170 * s, 282 * s)], fill=white)
    # bristles: a fan opening down-left
    draw.polygon([(176 * s, 288 * s), (238 * s, 350 * s), (150 * s, 438 * s), (104 * s, 420 * s),
                  (92 * s, 372 * s)], fill=white)
    # grooves in the bristles, drawn in the background color for depth
    for i, (x1, y1, x2, y2) in enumerate([(150, 316, 116, 396), (176, 342, 138, 420), (204, 368, 164, 434)]):
        draw.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=(255, 77, 150), width=int(10 * s))


def render(size: int) -> Image.Image:
    base = gradient(size).convert("RGBA")
    base.putalpha(rounded_mask(size, radius=int(size * 0.22)))
    draw = ImageDraw.Draw(base)
    draw_broom(draw, size)
    return base


def main() -> None:
    big = render(SIZE)
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [big.resize((n, n), Image.LANCZOS) for n in sizes]
    ico = assets / "icon.ico"
    frames[-1].save(ico, format="ICO", sizes=[(n, n) for n in sizes], append_images=frames[:-1])
    png = ROOT / "src" / "igcleanup" / "ui" / "static" / "icon.png"
    big.resize((64, 64), Image.LANCZOS).save(png, format="PNG")
    print("wrote", ico, "and", png)


if __name__ == "__main__":
    main()
