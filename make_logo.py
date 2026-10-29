"""Prime's logo: a Hassium (Hs, element 108) atom, Bohr style, with every one of its 108 electrons in shells
2-8-18-32-32-14-2, drawn in brushed steel with cool blue accents.

    python make_logo.py

writes, into assets/:
    prime_logo.svg        the atom on a transparent background (vector master)
    prime_icon.svg        the app icon: the atom on a brushed-steel squircle
    prime_icon.png        1024 px app icon (rendered with Pillow, same geometry as the SVG)
    prime_logo.png        1024 px atom, transparent
    Prime.icns            macOS app icon (all sizes; Pillow writes it on any OS)
    Prime.iconset/        the same sizes as PNGs, for `iconutil -c icns` on the Mac if preferred
    status/<state>.png    44 px menu-bar icons, one per state (idle, listening, thinking, speaking, asking, muted)
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

SHELLS = (2, 8, 18, 32, 32, 14, 2)        # K L M N O P Q: 108 electrons
assert sum(SHELLS) == 108

SIZE = 1024
C = SIZE / 2
NUCLEUS_R = 118
RADII = [172 + i * 50 for i in range(len(SHELLS))]   # 172 ... 472
ELECTRON_R = (11, 11, 10.5, 9, 9, 10, 11)
OFFSET = (90, 22.5, 10, 0, 5.625, 12.86, 0)         # degrees: staggers the shells so electrons don't line up

STEEL_LIGHT, STEEL, STEEL_DARK = "#d9dee4", "#8e98a3", "#3b424b"
BLUE, BLUE_GLOW = "#4fb3ff", "#9fd8ff"
STATE_COLOURS = {"idle": "#9aa4af", "listening": "#4fb3ff", "thinking": "#38e1ff", "speaking": "#7cc8ff",
                 "asking": "#ffb020", "muted": "#ff4d5e"}


def electrons():
    """(shell index, x, y) for all 108 electrons."""
    out = []
    for s, (n, r) in enumerate(zip(SHELLS, RADII)):
        for k in range(n):
            a = math.radians(OFFSET[s] + 360 * k / n - 90)
            out.append((s, C + r * math.cos(a), C + r * math.sin(a)))
    assert len(out) == 108
    return out


# ---- SVG --------------------------------------------------------------------------------------------

def svg(background=False):
    defs = f"""
  <defs>
    <radialGradient id="nucleus" cx="40%" cy="35%" r="70%">
      <stop offset="0" stop-color="{STEEL_LIGHT}"/><stop offset=".55" stop-color="{STEEL}"/>
      <stop offset="1" stop-color="{STEEL_DARK}"/>
    </radialGradient>
    <radialGradient id="electron" cx="35%" cy="30%" r="75%">
      <stop offset="0" stop-color="#ffffff"/><stop offset=".45" stop-color="{BLUE_GLOW}"/>
      <stop offset="1" stop-color="{BLUE}"/>
    </radialGradient>
    <linearGradient id="plate" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#5d6670"/><stop offset=".45" stop-color="#2f353c"/>
      <stop offset="1" stop-color="#1a1e23"/>
    </linearGradient>
    <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{STEEL_LIGHT}"/><stop offset=".5" stop-color="{STEEL}"/>
      <stop offset="1" stop-color="{STEEL_LIGHT}"/>
    </linearGradient>
    <pattern id="brush" width="1024" height="6" patternUnits="userSpaceOnUse">
      <rect width="1024" height="3" fill="#ffffff" opacity=".035"/>
    </pattern>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>"""
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}">',
             f"  <title>Prime: Hassium (Hs, 108) with electrons 2-8-18-32-32-14-2</title>", defs]
    if background:
        parts += ['  <rect x="24" y="24" width="976" height="976" rx="220" fill="url(#plate)"/>',
                  '  <rect x="24" y="24" width="976" height="976" rx="220" fill="url(#brush)"/>',
                  f'  <rect x="30" y="30" width="964" height="964" rx="214" fill="none" stroke="{STEEL}" '
                  'stroke-opacity=".7" stroke-width="6"/>']
    for r in RADII:
        parts.append(f'  <circle cx="{C:g}" cy="{C:g}" r="{r}" fill="none" stroke="url(#ring)" '
                     'stroke-width="4" stroke-opacity=".75"/>')
    parts.append('  <g filter="url(#glow)">')
    for s, x, y in electrons():
        parts.append(f'    <circle cx="{x:.2f}" cy="{y:.2f}" r="{ELECTRON_R[s]}" fill="url(#electron)"/>')
    parts.append("  </g>")
    parts += [f'  <circle cx="{C:g}" cy="{C:g}" r="{NUCLEUS_R}" fill="url(#nucleus)" stroke="{BLUE}" '
              'stroke-width="6"/>',
              f'  <text x="{C:g}" y="{C + 44:g}" text-anchor="middle" font-family="Helvetica Neue, Arial, sans-serif" '
              f'font-weight="700" font-size="128" fill="#0f1317" letter-spacing="-2">Hs</text>',
              f'  <text x="{C:g}" y="{C - 62:g}" text-anchor="middle" font-family="Helvetica Neue, Arial, sans-serif" '
              f'font-weight="600" font-size="30" fill="#1b2128">108</text>',
              "</svg>"]
    return "\n".join(parts) + "\n"


# ---- raster (Pillow), same geometry -----------------------------------------------------------------

def _font(size):
    for name in ("HelveticaNeue.ttc", "Helvetica.ttc", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _radial(size, inner, outer, centre=(0.4, 0.35)):
    """A radial gradient disc image (RGBA), `size` px square."""
    img = Image.new("RGBA", (size, size))
    px = img.load()
    cx, cy = centre[0] * size, centre[1] * size
    maxd = size * 0.85
    ir, ig, ib = (int(inner[i:i + 2], 16) for i in (1, 3, 5))
    orr, og, ob = (int(outer[i:i + 2], 16) for i in (1, 3, 5))
    for y in range(size):
        for x in range(size):
            t = min(1.0, math.hypot(x - cx, y - cy) / maxd)
            px[x, y] = (int(ir + (orr - ir) * t), int(ig + (og - ig) * t), int(ib + (ob - ib) * t), 255)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


def render(background=False, scale=2):
    S = SIZE * scale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if background:
        plate = Image.new("RGBA", (S, S))
        d = ImageDraw.Draw(plate)
        for y in range(S):  # diagonal-ish steel gradient, then brushed lines
            t = y / S
            v = int(0x5d + (0x1a - 0x5d) * t)
            d.line([(0, y), (S, y)], fill=(v, v + 7, v + 13, 255))
        for y in range(0, S, 6 * scale):
            d.line([(0, y), (S, y)], fill=(255, 255, 255, 10), width=scale)
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle((24 * scale, 24 * scale, 1000 * scale, 1000 * scale),
                                               radius=220 * scale, fill=255)
        img.paste(plate, (0, 0), mask)
        ImageDraw.Draw(img).rounded_rectangle((30 * scale, 30 * scale, 994 * scale, 994 * scale),
                                              radius=214 * scale, outline=(142, 152, 163, 180), width=6 * scale)
    d = ImageDraw.Draw(img)
    for r in RADII:
        R = r * scale
        d.ellipse((C * scale - R, C * scale - R, C * scale + R, C * scale + R),
                  outline=(190, 198, 207, 190), width=4 * scale)
    # electrons with a blue glow
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for s, x, y in electrons():
        R = (ELECTRON_R[s] + 7) * scale
        gd.ellipse((x * scale - R, y * scale - R, x * scale + R, y * scale + R), fill=(79, 179, 255, 150))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(8 * scale)))
    dot = _radial(int(24 * scale), "#ffffff", BLUE, centre=(0.35, 0.3))
    for s, x, y in electrons():
        size = int(2 * ELECTRON_R[s] * scale)
        e = dot.resize((size, size), Image.LANCZOS)
        img.alpha_composite(e, (int(x * scale - size / 2), int(y * scale - size / 2)))
    # nucleus
    n = _radial(2 * NUCLEUS_R * scale, STEEL_LIGHT, STEEL_DARK)
    img.alpha_composite(n, (int((C - NUCLEUS_R) * scale), int((C - NUCLEUS_R) * scale)))
    d = ImageDraw.Draw(img)
    R = NUCLEUS_R * scale
    d.ellipse((C * scale - R, C * scale - R, C * scale + R, C * scale + R), outline=BLUE, width=6 * scale)
    d.text((C * scale, (C + 8) * scale), "Hs", font=_font(128 * scale), fill="#0f1317", anchor="mm")
    d.text((C * scale, (C - 72) * scale), "108", font=_font(30 * scale), fill="#1b2128", anchor="mm")
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def status_icon(colour, px=44):
    """A small menu-bar atom: nucleus in the state's colour, three rings, a few electrons."""
    S = px * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    for i, r in enumerate((0.26, 0.36, 0.46)):
        R = r * S
        d.ellipse((c - R, c - R, c + R, c + R), outline=(170, 180, 190, 255), width=int(S * 0.035))
        for k in range((2, 3, 4)[i]):
            a = math.radians(360 * k / (2, 3, 4)[i] + 30 * i - 90)
            x, y, er = c + R * math.cos(a), c + R * math.sin(a), S * 0.05
            d.ellipse((x - er, y - er, x + er, y + er), fill=colour)
    R = 0.16 * S
    d.ellipse((c - R, c - R, c + R, c + R), fill=colour)
    return img.resize((px, px), Image.LANCZOS)


def main():
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "prime_logo.svg").write_text(svg(background=False), encoding="utf-8")
    (ASSETS / "prime_icon.svg").write_text(svg(background=True), encoding="utf-8")
    logo, icon = render(background=False), render(background=True)
    logo.save(ASSETS / "prime_logo.png")
    icon.save(ASSETS / "prime_icon.png")
    iconset = ASSETS / "Prime.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        icon.resize((base, base), Image.LANCZOS).save(iconset / f"icon_{base}x{base}.png")
        icon.resize((base * 2, base * 2), Image.LANCZOS).save(iconset / f"icon_{base}x{base}@2x.png")
    icon.save(ASSETS / "Prime.icns")
    status = ASSETS / "status"
    status.mkdir(exist_ok=True)
    for state, colour in STATE_COLOURS.items():
        status_icon(colour).save(status / f"{state}.png")
    print(f"108 electrons in shells {'-'.join(map(str, SHELLS))}; wrote logo, icon, Prime.icns and "
          f"{len(STATE_COLOURS)} status icons to {ASSETS}")


if __name__ == "__main__":
    main()
