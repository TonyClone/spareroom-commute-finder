"""Regenerate assets/flatfinder.ico for the desktop shortcut."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets"
ICO = OUT / "flatfinder.ico"
PNG = OUT / "flatfinder_icon_256.png"


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    m = max(1, size // 32)
    pad = size // 16

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [pad + m, pad + m * 2, size - pad + m, size - pad + m * 2],
        radius=size // 5,
        fill=(0, 0, 0, 70),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(1, size // 18)))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [pad, pad, size - pad - 1, size - pad - 1],
        radius=size // 5,
        fill=(15, 23, 42, 255),
    )
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.rounded_rectangle(
        [pad + m, pad + m, size - pad - m - 1, size // 2],
        radius=size // 6,
        fill=(56, 189, 248, 40),
    )
    img = Image.alpha_composite(img, sheen)
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, int(size * 0.54)
    w, h = int(size * 0.40), int(size * 0.30)
    left, top = cx - w // 2, cy - h // 4
    right, bottom = cx + w // 2, cy + h // 2
    roof_top = top - int(size * 0.15)
    draw.polygon(
        [(cx, roof_top), (left - m * 2, top + m), (right + m * 2, top + m)],
        fill=(34, 211, 238, 255),
    )
    draw.rounded_rectangle(
        [left, top, right, bottom],
        radius=max(2, size // 24),
        fill=(248, 250, 252, 255),
    )
    dw, dh = int(w * 0.22), int(h * 0.48)
    dx = cx - dw // 2
    dy = bottom - dh
    draw.rounded_rectangle(
        [dx, dy, dx + dw, bottom],
        radius=max(1, size // 40),
        fill=(14, 165, 233, 255),
    )
    ww = max(2, int(w * 0.16))
    wy = top + int(h * 0.2)
    for wx in (left + int(w * 0.16), right - int(w * 0.16) - ww):
        draw.rounded_rectangle(
            [wx, wy, wx + ww, wy + ww],
            radius=max(1, size // 50),
            fill=(56, 189, 248, 230),
        )

    ring_r = int(size * 0.13)
    ring_cx, ring_cy = int(size * 0.78), int(size * 0.25)
    sw = max(2, size // 16)
    draw.ellipse(
        [ring_cx - ring_r, ring_cy - ring_r, ring_cx + ring_r, ring_cy + ring_r],
        outline=(220, 36, 31, 255),
        width=sw,
    )
    bar_h = max(2, size // 18)
    bw = int(ring_r * 1.65)
    draw.rounded_rectangle(
        [ring_cx - bw, ring_cy - bar_h // 2, ring_cx + bw, ring_cy + bar_h // 2 + 1],
        radius=bar_h // 2,
        fill=(220, 36, 31, 255),
    )
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    master = make_icon(256)
    master.save(PNG)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ICO, format="ICO", sizes=sizes)
    print(f"Wrote {ICO} ({ICO.stat().st_size} bytes)")
    print(f"Wrote {PNG}")


if __name__ == "__main__":
    main()
