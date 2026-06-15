#!/usr/bin/env python3
"""Generate MarginWatch favicon.ico (32×32 + 16×16).

Design: a mini positions table with the app's expiry colour rows,
a Bootstrap-style header bar, column dividers, and tiny text-line hints.
"""

from PIL import Image, ImageDraw


# ── Palette (matches app colours) ────────────────────────────────────────────

HEADER_BG  = (222, 226, 230)   # Bootstrap table-light
BORDER     = ( 90, 100, 110)   # slightly darker table border
TEXT_LINE  = ( 50,  50,  50)   # "text" stub colour
BG_WHITE   = (255, 255, 255)

# Expiry row colours cycling through the full range
ROW_COLORS = [
    (198, 239, 206),   # pale green  ≤  6 days
    (255, 235, 156),   # pale yellow ≤ 13 days
    (255, 199, 206),   # pale red    ≤ 20 days
    (189, 215, 238),   # pale blue   ≤ 27 days
    (220, 220, 220),   # gray        > 27 days
    (198, 239, 206),   # cycle
    (255, 235, 156),
    (255, 199, 206),
    (189, 215, 238),
    (220, 220, 220),
]


# ── Builder ───────────────────────────────────────────────────────────────────

def make_frame(size: int) -> Image.Image:
    w = h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer background (white, no border yet)
    draw.rectangle([0, 0, w - 1, h - 1], fill=BG_WHITE)

    # Header bar height: ~18% of size (min 3 px)
    hdr_h = max(3, round(h * 0.18))
    draw.rectangle([0, 0, w - 1, hdr_h], fill=HEADER_BG)
    draw.line([(0, hdr_h), (w - 1, hdr_h)], fill=BORDER)

    # Column dividers (two vertical lines splitting into 3 columns)
    # col1 ends at ~55%, col2 ends at ~78%
    cx1 = round(w * 0.55)
    cx2 = round(w * 0.78)
    for x in (cx1, cx2):
        draw.line([(x, 0), (x, h - 1)], fill=BORDER)

    # Data rows
    data_top = hdr_h + 1
    available = h - data_top
    n_visible = min(len(ROW_COLORS), available // 2)   # at least 2 px per row
    row_h = available / n_visible if n_visible else available

    for i in range(n_visible):
        y1 = round(data_top + i * row_h)
        y2 = round(data_top + (i + 1) * row_h) - 1
        y2 = min(y2, h - 1)
        draw.rectangle([0, y1, w - 1, y2], fill=ROW_COLORS[i])
        # Row separator
        if y2 < h - 1:
            draw.line([(0, y2), (w - 1, y2)], fill=BORDER)

        # "Text" stub lines — only when rows are tall enough
        row_pixel_h = y2 - y1 + 1
        if row_pixel_h >= 4:
            ty = y1 + row_pixel_h // 2 - 1
            draw.line([(2,      ty), (cx1 - 3, ty)], fill=TEXT_LINE)
            draw.line([(cx1 + 2, ty), (cx2 - 3, ty)], fill=TEXT_LINE)
            draw.line([(cx2 + 2, ty), (w - 3,  ty)], fill=TEXT_LINE)

    # Outer border (drawn last so it's on top)
    draw.rectangle([0, 0, w - 1, h - 1], outline=BORDER)

    return img


# ── Save ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os, sys

    out = os.path.join(os.path.dirname(__file__),
                       "..", "src", "ui_web", "static", "favicon.ico")

    img32 = make_frame(32)
    img16 = make_frame(16)

    img32.save(out, format="ICO", sizes=[(32, 32), (16, 16)],
               append_images=[img16])
    print(f"Wrote {out}")
