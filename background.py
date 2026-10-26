"""Selbst gezeichneter Stadion-Hintergrund (Flutlicht, Tribüne, Rasen) – komplett eigene Grafik.

make_stadium() liefert zwei Versionen (gedimmt/hell). Zwischen beiden wird im Video
weich geblendet, damit Flutlicht und Zuschauer-Lichter leicht „leben“.
"""
import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HORIZON = 985  # Oberkante des Rasens


def _vgrad(w, h, stops):
    ys = np.linspace(0, 1, h)
    arr = np.zeros((h, w, 3), np.float32)
    for c in range(3):
        col = np.interp(ys, [s[0] for s in stops], [s[1][c] for s in stops])
        arr[:, :, c] = col[:, None]
    return arr


def _radial(w, h, cx, cy, rx, ry, color, strength):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d2 = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    g = np.exp(-d2) * strength
    return g[:, :, None] * np.array(color, np.float32)[None, None, :]


def _to_arr(img):
    return np.asarray(img, np.float32)


def make_stadium(w, h, accent_colors=((255, 255, 255), (255, 64, 80)), seed=7):
    rnd = random.Random(seed)

    # ---------- Basis (ohne Leuchtendes) ----------
    base = _vgrad(w, h, [(0.0, (3, 6, 16)), (0.18, (9, 16, 38)), (0.32, (20, 34, 72)), (0.52, (26, 44, 88)), (1.0, (10, 20, 40))])

    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)

    # Dach / Tribünenrücken
    d.polygon([(0, 470), (w, 470), (w, 640), (0, 640)], fill=(7, 10, 22))
    d.polygon([(0, 640), (w, 640), (w, HORIZON - 40), (0, HORIZON - 40)], fill=(11, 16, 32))
    # Ränge als leicht hellere Bänder
    for i in range(9):
        y0 = 640 + i * 36
        d.rectangle([0, y0, w, y0 + 4], fill=(18, 26, 48))
    # Dachkante mit Streben
    for x in range(-40, w + 80, 90):
        d.line([(x, 470), (x + 30, 640)], fill=(14, 20, 38), width=6)
    # Werbebande (nur Farbflächen, keine Marken)
    board_cols = [(24, 70, 190), (236, 238, 244), (214, 34, 48), (24, 70, 190), (255, 196, 0)]
    for i, x in enumerate(range(0, w + 120, 120)):
        d.rectangle([x, HORIZON - 40, x + 120, HORIZON], fill=tuple(int(c * 0.45) for c in board_cols[i % len(board_cols)]))

    # Rasen mit Perspektiv-Streifen
    pitch_mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(pitch_mask).polygon([(150, HORIZON), (w - 150, HORIZON), (w + 520, h), (-520, h)], fill=255)
    pitch = Image.new("RGB", (w, h), (12, 52, 28))  # Auslauf neben dem Spielfeld
    pd = ImageDraw.Draw(pitch)
    n = 16
    for i in range(n):
        ya = HORIZON + (h - HORIZON) * (i / n) ** 1.6
        yb = HORIZON + (h - HORIZON) * ((i + 1) / n) ** 1.6
        col = (24, 98, 46) if i % 2 == 0 else (30, 112, 54)
        pd.rectangle([0, ya, w, yb + 1], fill=col)
    # Linien
    line = (225, 235, 225)
    lw = lambda y: max(2, int(3 + (y - HORIZON) / (h - HORIZON) * 9))
    # Seitenlinien
    pd.line([(150, HORIZON), (-520, h)], fill=line, width=8)
    pd.line([(w - 150, HORIZON), (w + 520, h)], fill=line, width=8)
    # Mittellinie + Anstoßkreis
    yc = HORIZON + (h - HORIZON) * 0.58
    pd.line([(-200, yc), (w + 200, yc)], fill=line, width=lw(yc))
    pd.ellipse([w / 2 - 360, yc - 105, w / 2 + 360, yc + 105], outline=line, width=lw(yc))
    pd.ellipse([w / 2 - 12, yc - 5, w / 2 + 12, yc + 5], fill=line)
    # Strafraum im Hintergrund (weit weg, schmal)
    yb1 = HORIZON + 38
    pd.line([(330, HORIZON), (260, yb1)], fill=line, width=3)
    pd.line([(w - 330, HORIZON), (w - 260, yb1)], fill=line, width=3)
    pd.line([(260, yb1), (w - 260, yb1)], fill=line, width=3)
    img.paste(pitch, (0, 0), pitch_mask)

    # ---------- Leuchtendes (Glow-Layer) ----------
    glow = np.zeros((h, w, 3), np.float32)

    # Flutlichtmasten: je zwei Lichtfelder links und rechts
    lights = [(110, 395), (330, 340), (w - 330, 340), (w - 110, 395)]
    for lx, ly in lights:
        glow += _radial(w, h, lx, ly, 300, 300, (150, 185, 255), 0.26)
        glow += _radial(w, h, lx, ly, 90, 90, (255, 255, 255), 0.55)
    # Lichtkegel Richtung Rasen
    beams = Image.new("RGB", (w, h), (0, 0, 0))
    bd = ImageDraw.Draw(beams)
    for lx, ly in lights:
        tx = w / 2 + (lx - w / 2) * 0.25
        bd.polygon([(lx - 28, ly), (lx + 28, ly), (tx + 340, h * 0.78), (tx - 340, h * 0.78)], fill=(30, 40, 62))
    beams = beams.filter(ImageFilter.GaussianBlur(45))
    glow += _to_arr(beams) * 0.55
    # Lichtfelder (kleine helle Kacheln)
    gl = Image.new("RGB", (w, h), (0, 0, 0))
    gd = ImageDraw.Draw(gl)
    for lx, ly in lights:
        for r in range(3):
            for c in range(4):
                x0, y0 = lx - 54 + c * 28, ly - 36 + r * 26
                gd.rectangle([x0, y0, x0 + 20, y0 + 16], fill=(255, 255, 255))
    glow += _to_arr(gl.filter(ImageFilter.GaussianBlur(1.2))) * 0.8
    glow += _to_arr(gl.filter(ImageFilter.GaussianBlur(18))) * 0.7

    # Zuschauer: viele kleine Lichter (Handys, Fackeln, Schals) mit Bokeh
    crowd = Image.new("RGB", (w, h), (0, 0, 0))
    cd = ImageDraw.Draw(crowd)
    palette = [(255, 244, 214)] * 8 + [accent_colors[1]] * 3 + [accent_colors[0]] * 2 + [(255, 170, 60)] * 2 + [(120, 190, 255)]
    for r in range(17):
        y = 655 + r * 17.5
        step = 15 + r * 0.55
        rad = 2.0 + r * 0.14
        x = rnd.uniform(0, step)
        while x < w:
            if rnd.random() < 0.62:
                col = rnd.choice(palette)
                k = rnd.uniform(0.35, 1.0)
                col = tuple(int(c * k) for c in col)
                yy = y + rnd.uniform(-3, 3)
                cd.ellipse([x - rad, yy - rad, x + rad, yy + rad], fill=col)
            x += step * rnd.uniform(0.7, 1.3)
    glow += _to_arr(crowd) * 0.75
    glow += _to_arr(crowd.filter(ImageFilter.GaussianBlur(5))) * 1.2
    glow += _to_arr(crowd.filter(ImageFilter.GaussianBlur(14))) * 1.0

    # LED-Bande leuchtet
    led = Image.new("RGB", (w, h), (0, 0, 0))
    ld = ImageDraw.Draw(led)
    for i, x in enumerate(range(0, w + 120, 120)):
        ld.rectangle([x + 4, HORIZON - 36, x + 116, HORIZON - 4], fill=board_cols[i % len(board_cols)])
    glow += _to_arr(led) * 0.55
    glow += _to_arr(led.filter(ImageFilter.GaussianBlur(16))) * 0.9
    # Lichtfleck auf dem Rasen
    glow += _radial(w, h, w / 2, 1230, 620, 420, (110, 190, 120), 0.20)

    # ---------- Zusammensetzen ----------
    base_arr = _to_arr(img)
    # Vignette
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vig = 1.0 - 0.38 * (((xx - w / 2) / (w * 0.75)) ** 2 + ((yy - h * 0.48) / (h * 0.75)) ** 2)
    vig = np.clip(vig, 0.45, 1.0)[:, :, None]
    # Textfreundlich abdunkeln: Rasenbereich unten, Himmel oben leicht
    dark = np.interp(np.arange(h), [0, 300, 1000, 1500, h], [0.80, 1.0, 1.0, 0.70, 0.52]).astype(np.float32)[:, None, None]

    def compose(k):
        out = (base_arr + glow * k) * vig * dark
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

    return compose(0.55), compose(1.0)
