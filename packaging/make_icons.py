"""Generate the application icon set from one drawing.

    python packaging/make_icons.py

Tauri's bundler needs a fixed set of sizes and two container formats. There was
no icon set at all until the first honest check of "is this installable?" --
the MSI and DMG jobs would have failed on the first run, because nothing here
had ever compiled the shell.

The mark is drawn in code rather than checked in as a binary blob so it can be
regenerated at any size, and so a reviewer can see what it is without opening an
image editor. Three ascending bars on a dark ground: demand rising against a
baseline, which is what the product is about, and which stays legible at 16px
where anything more detailed turns to mud.
"""

import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "desktop" / "src-tauri" / "icons"

#: Deep indigo ground, warm amber bars. High contrast at small sizes, and
#: distinguishable from the blue-grey most desktop icons default to.
GROUND = (26, 31, 54)
BAR = (240, 173, 78)
BASELINE = (108, 122, 168)

#: What Tauri's bundler looks for, plus the Windows and macOS containers.
PNG_SIZES = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
    "Square30x30Logo.png": 30,
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square89x89Logo.png": 89,
    "Square107x107Logo.png": 107,
    "Square142x142Logo.png": 142,
    "Square150x150Logo.png": 150,
    "Square284x284Logo.png": 284,
    "Square310x310Logo.png": 310,
    "StoreLogo.png": 50,
}

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: ICNS chunk types that accept a PNG payload, and the size each expects.
ICNS_TYPES = (
    (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
    (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512),
)


def draw(size: int) -> Image.Image:
    """The mark, at any size. Proportional, so 16px and 1024px agree."""
    scale = 4  # supersample, then downsample -- edges stay clean at 16px
    px = size * scale
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)

    radius = int(px * 0.22)
    pen.rounded_rectangle([0, 0, px - 1, px - 1], radius=radius, fill=GROUND)

    # Three ascending bars over a baseline.
    margin = px * 0.22
    width = px - 2 * margin
    bar_w = width / 4.6
    gap = (width - 3 * bar_w) / 2
    base_y = px - margin
    heights = (0.34, 0.60, 0.92)

    line_h = max(1, int(px * 0.028))
    pen.rectangle([margin, base_y, px - margin, base_y + line_h], fill=BASELINE)

    corner = max(1, int(bar_w * 0.18))
    for index, fraction in enumerate(heights):
        left = margin + index * (bar_w + gap)
        top = base_y - width * fraction
        pen.rounded_rectangle(
            [left, top, left + bar_w, base_y], radius=corner, fill=BAR
        )

    return image.resize((size, size), Image.LANCZOS)


def write_icns(path: Path) -> None:
    """A minimal ICNS: the magic, a length, then PNG-payload chunks.

    Written by hand because `iconutil` is macOS-only and this has to be
    generated on whatever runner happens to build.
    """
    import io

    chunks = []
    for kind, size in ICNS_TYPES:
        buffer = io.BytesIO()
        draw(size).save(buffer, format="PNG")
        payload = buffer.getvalue()
        chunks.append(kind + struct.pack(">I", len(payload) + 8) + payload)

    body = b"".join(chunks)
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    for name, size in PNG_SIZES.items():
        draw(size).save(OUT / name, format="PNG")

    # Pillow writes a genuine multi-resolution ICO when given sizes.
    draw(256).save(OUT / "icon.ico", format="ICO",
                   sizes=[(s, s) for s in ICO_SIZES])

    write_icns(OUT / "icon.icns")

    written = sorted(OUT.iterdir())
    total = sum(f.stat().st_size for f in written)
    print(f"wrote {len(written)} icon files, {total / 1024:.0f} KB, to {OUT}")
    for f in written:
        print(f"  {f.stat().st_size / 1024:7.1f} KB  {f.name}")

    missing = [n for n in ("32x32.png", "128x128.png", "icon.ico", "icon.icns")
               if not (OUT / n).exists()]
    if missing:
        # Tauri's bundler fails on a missing icon with a message that does not
        # name the file, so it is named here instead.
        print(f"MISSING required icon(s): {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
