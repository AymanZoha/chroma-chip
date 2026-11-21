"""Concept renders of the CHROMA player (an original design), with the screen showing
real frames from the CHROMA v2 simulation.

  python product/render_product.py [screen.png ...]
Writes v2/media/product_front.png, product_angle.png, product_exploded.png.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

V2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(V2, "media")
FD = "C:/Windows/Fonts/"
PX = 8                          # pixels per millimetre
BODY_W, BODY_H = 62, 128        # mm
SCR_X, SCR_Y = 9, 10            # screen offset inside body (mm)
SCR_W, SCR_H = 360, 480         # screen size in px (240x320 LCD at 1.5x)
ACCENT = (0, 220, 255)


def font(name, size):
    return ImageFont.truetype(FD + name, size)


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def vgradient(w, h, top, bottom):
    t = np.linspace(0, 1, h)[:, None, None]
    a = np.array(top)[None, None, :] * (1 - t) + np.array(bottom)[None, None, :] * t
    return Image.fromarray(np.repeat(a, w, axis=1).astype(np.uint8))


def brushed(w, h, base_top, base_bottom, seed=3):
    img = np.asarray(vgradient(w, h, base_top, base_bottom)).astype(np.float32)
    rng = np.random.default_rng(seed)
    streaks = rng.normal(0, 1, (1, w)).repeat(h, 0)
    streaks = np.asarray(Image.fromarray(((streaks * 20) + 128).clip(0, 255).astype(np.uint8))
                         .filter(ImageFilter.GaussianBlur((0.6, 0))), np.float32) - 128
    img += streaks[..., None] * 0.18
    return Image.fromarray(img.clip(0, 255).astype(np.uint8))


def icon(d, kind, cx, cy, s, fill):
    if kind == "play":
        d.polygon([(cx - s * 0.35, cy - s * 0.5), (cx - s * 0.35, cy + s * 0.5), (cx + s * 0.5, cy)], fill=fill)
    elif kind == "next":
        for o in (-0.45, 0.05):
            d.polygon([(cx + s * o, cy - s * 0.4), (cx + s * o, cy + s * 0.4), (cx + s * (o + 0.42), cy)], fill=fill)
        d.rectangle([cx + s * 0.47, cy - s * 0.4, cx + s * 0.57, cy + s * 0.4], fill=fill)
    elif kind == "prev":
        for o in (0.45, -0.05):
            d.polygon([(cx + s * o, cy - s * 0.4), (cx + s * o, cy + s * 0.4), (cx + s * (o - 0.42), cy)], fill=fill)
        d.rectangle([cx - s * 0.57, cy - s * 0.4, cx - s * 0.47, cy + s * 0.4], fill=fill)


def device_front(screen):
    """Front face of the player as an RGBA image (no background)."""
    w, h = BODY_W * PX, BODY_H * PX
    body = brushed(w, h, (58, 62, 72), (30, 32, 38)).convert("RGBA")
    d = ImageDraw.Draw(body)
    # bevel highlights
    d.rounded_rectangle([1, 1, w - 2, h - 2], radius=11 * PX, outline=(130, 136, 150), width=3)
    d.rounded_rectangle([6, 6, w - 7, h - 7], radius=10 * PX, outline=(20, 22, 26), width=2)
    # glass panel with screen
    gx, gy, gw, gh = 4 * PX, 4 * PX, w - 8 * PX, 66 * PX
    d.rounded_rectangle([gx, gy, gx + gw, gy + gh], radius=7 * PX, fill=(6, 7, 10))
    scr = screen.convert("RGB").resize((SCR_W, SCR_H), Image.LANCZOS)
    sx, sy = (w - SCR_W) // 2, gy + (gh - SCR_H) // 2
    body.paste(scr, (sx, sy))
    # glass reflection
    refl = Image.new("L", (w, h), 0)
    rd = ImageDraw.Draw(refl)
    rd.polygon([(gx, gy), (gx + gw * 0.62, gy), (gx + gw * 0.18, gy + gh), (gx, gy + gh)], fill=26)
    refl = refl.filter(ImageFilter.GaussianBlur(18))
    glass_mask = rounded_mask(gw, gh, 7 * PX)
    clip = Image.new("L", (w, h), 0)
    clip.paste(glass_mask, (gx, gy))
    refl = Image.fromarray(np.minimum(np.asarray(refl), np.asarray(clip)))
    body = Image.composite(Image.new("RGBA", (w, h), (255, 255, 255, 255)), body, refl)
    d = ImageDraw.Draw(body)
    # controls
    cy = gy + gh + 16 * PX
    for kind, cx, r in (("prev", w * 0.2, 6.0), ("play", w * 0.5, 9.0), ("next", w * 0.8, 6.0)):
        R = r * PX
        if kind == "play":
            glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(glow).ellipse([cx - R - 10, cy - R - 10, cx + R + 10, cy + R + 10], fill=ACCENT + (90,))
            body = Image.alpha_composite(body, glow.filter(ImageFilter.GaussianBlur(10)))
            d = ImageDraw.Draw(body)
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(24, 26, 31), outline=(95, 100, 112), width=3)
        d.ellipse([cx - R + 6, cy - R + 6, cx + R - 6, cy + R - 6], fill=(34, 37, 44))
        if kind == "play":
            d.ellipse([cx - R + 2, cy - R + 2, cx + R - 2, cy + R - 2], outline=ACCENT, width=3)
        icon(d, kind, cx, cy, R * 0.9, (225, 230, 238))
    # wordmark + speaker grille
    f = font("bahnschrift.ttf", 30)
    t = "CHROMA"
    tw = d.textlength(t, font=f)
    d.text(((w - tw) / 2, cy + 13 * PX), t, font=f, fill=(170, 176, 188))
    gy2 = cy + 22 * PX
    for row in range(4):
        for col in range(13):
            x = w / 2 + (col - 6) * 2.6 * PX + (1.3 * PX if row % 2 else 0)
            y = gy2 + row * 2.2 * PX
            if abs(x - w / 2) < 17 * PX:
                d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(12, 13, 16))
    mask = rounded_mask(w, h, 11 * PX)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(body, (0, 0), mask)
    return out


def backdrop(w, h):
    bg = vgradient(w, h, (232, 236, 242), (196, 202, 212)).convert("RGBA")
    return bg


def shadow(size, box, blur=30, alpha=120, offset=(0, 30)):
    sh = Image.new("RGBA", size, (0, 0, 0, 0))
    x0, y0, x1, y1 = box
    ImageDraw.Draw(sh).rounded_rectangle([x0 + offset[0], y0 + offset[1], x1 + offset[0], y1 + offset[1]],
                                         radius=80, fill=(0, 0, 0, alpha))
    return sh.filter(ImageFilter.GaussianBlur(blur))


def render_front(screen, path):
    dev = device_front(screen)
    W, H = 1400, 1400
    img = backdrop(W, H)
    x, y = (W - dev.width) // 2, (H - dev.height) // 2
    img = Image.alpha_composite(img, shadow((W, H), (x, y, x + dev.width, y + dev.height), 40, 110, (0, 40)))
    img.alpha_composite(dev, (x, y))
    d = ImageDraw.Draw(img)
    d.text((60, 60), "CHROMA player — concept", font=font("seguisb.ttf", 34), fill=(40, 44, 54))
    d.text((60, 104), "Screen: actual CHROMA v2 chip output (cycle-accurate simulation)",
           font=font("segoeui.ttf", 24), fill=(90, 96, 110))
    img.convert("RGB").save(path)


def perspective_coeffs(src, dst):
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); B.append(v)
    return np.linalg.solve(np.array(A, float), np.array(B, float)).tolist()


def render_angle(screen, path):
    dev = device_front(screen)
    W, H = 1400, 1400
    img = backdrop(W, H)
    w, h = dev.size
    # rotate the device ~30 degrees about its vertical axis
    x0, y0 = 420, 170
    tl, tr, br, bl = (x0, y0), (x0 + w * 0.82, y0 + 60), (x0 + w * 0.82, y0 + h - 30), (x0, y0 + h + 30)
    depth = 40
    img = Image.alpha_composite(img, shadow((W, H), (x0 - depth, y0 + 40, x0 + w * 0.82, y0 + h + 30), 45, 110, (30, 40)))
    coeffs = perspective_coeffs([(0, 0), (w, 0), (w, h), (0, h)], [tl, tr, br, bl])
    warped = dev.transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    alpha = warped.split()[3]
    for k in range(depth, 0, -1):  # extrude the rounded silhouette to the left = device thickness
        shade = 44 + int(26 * (1 - k / depth))
        layer = Image.new("RGBA", (W, H), (shade, shade + 3, shade + 10, 255))
        layer.putalpha(alpha)
        img.alpha_composite(layer, (-k, 0))
    d = ImageDraw.Draw(img)
    rx = x0 - depth // 2
    d.rounded_rectangle([rx - 7, y0 + 260, rx + 7, y0 + 380], radius=7, fill=(26, 28, 33), outline=(110, 116, 130), width=2)
    for k in range(9):
        yy = y0 + 272 + k * 12
        d.line([(rx - 5, yy), (rx + 5, yy)], fill=(80, 84, 96), width=2)
    img = Image.alpha_composite(img, warped)
    d = ImageDraw.Draw(img)
    d.text((60, 60), "CHROMA player — three-quarter view", font=font("seguisb.ttf", 34), fill=(40, 44, 54))
    d.text((60, 104), "Volume roller on the side, USB-C charging on top", font=font("segoeui.ttf", 24), fill=(90, 96, 110))
    img.convert("RGB").save(path)


def render_exploded(screen, path):
    W, H = 1700, 1900
    img = backdrop(W, H)
    d = ImageDraw.Draw(img)
    d.text((60, 50), "What's inside", font=font("seguisb.ttf", 38), fill=(40, 44, 54))
    d.text((60, 98), "Exploded concept view (not to scale)", font=font("segoeui.ttf", 24), fill=(90, 96, 110))
    layers = [  # top to bottom
        ("Cover glass", None, (210, 225, 235)),
        ("2.8\" 240x320 IPS LCD  ·  ST7789, 8-bit bus", "screen", None),
        ("Main board  ·  CHROMA v2 + 8 MB PSRAM + I2S amp", "board", (24, 110, 60)),
        ("Raspberry Pi Zero 2 W  ·  Wi-Fi + Spotify Connect", "pi", (20, 120, 70)),
        ("1200 mAh LiPo  ·  USB-C charging", "batt", (60, 64, 74)),
        ("28 mm speaker", "spk", (40, 42, 48)),
        ("Aluminium back shell", "shell", (70, 74, 84)),
    ]
    pw, ph = 360, 640
    U = np.array([0.80, -0.30])   # screen direction of the plate's width axis (per px)
    V = np.array([0.50, 0.36])    # screen direction of the plate's height axis (per px)
    placed = []
    for i, (label, kind, colr) in enumerate(layers):
        tl = np.array([250.0, 230.0 + i * 205])
        quad = [tuple(tl), tuple(tl + U * pw), tuple(tl + U * pw + V * ph), tuple(tl + V * ph)]
        placed.append((label, kind, colr, quad))
    for label, kind, colr, quad in reversed(placed):  # draw the bottom layer first
        plate = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        pd = ImageDraw.Draw(plate)
        if kind == "screen":
            pd.rectangle([0, 0, pw - 1, ph - 1], fill=(8, 8, 10, 255))
            plate.paste(screen.convert("RGB").resize((pw - 30, ph - 60)), (15, 30))
        elif kind is None:
            pd.rounded_rectangle([0, 0, pw - 1, ph - 1], radius=50, fill=colr + (110,), outline=(255, 255, 255, 230), width=5)
        else:
            pd.rounded_rectangle([0, 0, pw - 1, ph - 1], radius=50 if kind == "shell" else 16, fill=colr + (255,))
            if kind == "board":
                pd.rectangle([110, 260, 250, 400], fill=(20, 20, 24))
                pd.text((128, 315), "CHROMA", font=font("bahnschrift.ttf", 26), fill=(205, 205, 210))
                pd.rectangle([120, 450, 200, 510], fill=(30, 30, 34))
                for k in range(10):
                    pd.rectangle([30 + k * 30, 24, 44 + k * 30, 44], fill=(210, 180, 80))
            if kind == "pi":
                pd.rectangle([130, 260, 230, 360], fill=(30, 30, 34))
                pd.rectangle([40, 560, 130, 600], fill=(170, 170, 175))
            if kind == "batt":
                pd.rounded_rectangle([30, 40, pw - 30, ph - 40], radius=20, fill=(92, 98, 112))
            if kind == "spk":
                pd.ellipse([pw / 2 - 80, ph - 230, pw / 2 + 80, ph - 70], fill=(15, 15, 18), outline=(120, 120, 130), width=6)
        coeffs = perspective_coeffs([(0, 0), (pw, 0), (pw, ph), (0, ph)], quad)
        img = Image.alpha_composite(img, plate.transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC))
    d = ImageDraw.Draw(img)
    for label, kind, colr, quad in placed:
        anchor = ((quad[1][0] + quad[2][0]) / 2, (quad[1][1] + quad[2][1]) / 2)
        lx = 1020
        d.line([anchor, (lx - 10, anchor[1])], fill=(120, 126, 140), width=2)
        d.ellipse([anchor[0] - 5, anchor[1] - 5, anchor[0] + 5, anchor[1] + 5], fill=(120, 126, 140))
        d.text((lx, anchor[1] - 18), label, font=font("segoeui.ttf", 26), fill=(40, 44, 54))
    img.convert("RGB").save(path)


if __name__ == "__main__":
    os.makedirs(MEDIA, exist_ok=True)
    scr = sys.argv[1] if len(sys.argv) > 1 else os.path.join(MEDIA, "v2_screen_a.png")
    s = Image.open(scr)
    render_front(s, os.path.join(MEDIA, "product_front.png"))
    render_angle(s, os.path.join(MEDIA, "product_angle.png"))
    render_exploded(s, os.path.join(MEDIA, "product_exploded.png"))
    print("renders written to", MEDIA)
