"""Draws the Claude Tickets app icon (1024x1024 PNG): an orange rounded tile with a white ticket stub —
a check mark on the stub, progress lines on the body. Run: python make_icon.py <out.png>"""
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024
SS = 4  # supersample for smooth edges
W = S * SS


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_mask(box, radius):
    m = Image.new("L", (W, W), 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return m


# --- tile: macOS-style rounded square with a warm vertical gradient ---
pad, radius = 100 * SS, 186 * SS
tile_box = (pad, pad, W - pad, W - pad)
grad = Image.new("RGB", (1, W))
top, bottom = (240, 140, 82), (196, 84, 40)
for y in range(W):
    grad.putpixel((0, y), lerp(top, bottom, y / (W - 1)))
grad = grad.resize((W, W))
tile_mask = rounded_mask(tile_box, radius)

canvas = Image.new("RGBA", (W, W), (0, 0, 0, 0))
# soft drop shadow under the tile
shadow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
shadow.putalpha(rounded_mask((pad, pad + 14 * SS, W - pad, W - pad + 14 * SS), radius).point(lambda v: v * 0.35))
shadow = shadow.filter(ImageFilter.GaussianBlur(18 * SS))
canvas.alpha_composite(shadow)
tile = grad.convert("RGBA")
tile.putalpha(tile_mask)
canvas.alpha_composite(tile)

# subtle top highlight
hl = Image.new("RGBA", (W, W), (255, 255, 255, 0))
hd = ImageDraw.Draw(hl)
for i in range(64):  # fade from the top edge down to nothing
    yy = pad + i * 6 * SS
    hd.rectangle((pad, yy, W - pad, yy + 6 * SS), fill=(255, 255, 255, round(30 * (1 - i / 64) ** 1.6)))
hl.putalpha(ImageChops.multiply(hl.getchannel("A"), tile_mask))
canvas.alpha_composite(hl)

# --- ticket: white rounded rectangle with semicircle notches at the perforation ---
tx0, ty0, tx1, ty1 = 214 * SS, 318 * SS, 810 * SS, 706 * SS
perf_x = 404 * SS
notch_r = 40 * SS
ticket = Image.new("L", (W, W), 0)
td = ImageDraw.Draw(ticket)
td.rounded_rectangle((tx0, ty0, tx1, ty1), radius=46 * SS, fill=255)
for cy in (ty0, ty1):  # notches top and bottom on the perforation line
    td.ellipse((perf_x - notch_r, cy - notch_r, perf_x + notch_r, cy + notch_r), fill=0)

# ticket shadow
tsh = Image.new("RGBA", (W, W), (90, 30, 5, 0))
tsh.putalpha(ticket.point(lambda v: v * 0.30))
tsh = tsh.transform((W, W), Image.AFFINE, (1, 0, 0, 0, 1, -12 * SS)).filter(ImageFilter.GaussianBlur(14 * SS))
canvas.alpha_composite(tsh)
white = Image.new("RGBA", (W, W), (255, 253, 250, 255))
white.putalpha(ticket)
canvas.alpha_composite(white)

d = ImageDraw.Draw(canvas)
orange = (210, 98, 48, 255)
light = (236, 196, 172, 255)

# perforation: dashes between the notches
y = ty0 + notch_r + 22 * SS
while y < ty1 - notch_r - 22 * SS:
    d.rounded_rectangle((perf_x - 5 * SS, y, perf_x + 5 * SS, y + 24 * SS), radius=5 * SS, fill=light)
    y += 44 * SS

# check mark on the stub
cx, cy = (tx0 + perf_x) // 2, (ty0 + ty1) // 2
d.line([(cx - 52 * SS, cy + 4 * SS), (cx - 14 * SS, cy + 44 * SS), (cx + 58 * SS, cy - 46 * SS)],
       fill=orange, width=30 * SS, joint="curve")
for px, py in ((cx - 52 * SS, cy + 4 * SS), (cx + 58 * SS, cy - 46 * SS)):
    d.ellipse((px - 15 * SS, py - 15 * SS, px + 15 * SS, py + 15 * SS), fill=orange)

# progress lines on the body: one done (orange), two pending (light)
lx0 = perf_x + 64 * SS
rows = [(ty0 + 108 * SS, 300, orange), (ty0 + 194 * SS, 240, light), (ty0 + 280 * SS, 170, light)]
for ly, length, colour in rows:
    d.rounded_rectangle((lx0, ly - 15 * SS, lx0 + length * SS, ly + 15 * SS), radius=15 * SS, fill=colour)

out = canvas.resize((S, S), Image.LANCZOS)
out.save(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
print("saved")
