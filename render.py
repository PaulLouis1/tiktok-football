"""Szenenaufbau, Grafik-Animation und Video-Rendering (Pillow + FFmpeg, parallel)."""
import bisect
import math
import multiprocessing as mp
import os
import subprocess
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

import background
import config
import script as scriptmod
import voice
from i18n import stat_label, team_name, tr, ui

W, H = config.WIDTH, config.HEIGHT
WHITE = (255, 255, 255)
MUTED = (190, 198, 215)
GOLD = (255, 206, 0)
PANEL = (6, 10, 22)
BLACK = (0, 0, 0)

CAPTION_Y = 1345  # unterhalb davon überlagert TikTok seine eigene Oberfläche

_STATE: dict = {}


# --- Helfer ------------------------------------------------------------------
@lru_cache(maxsize=256)
def font(size, kind="bold"):
    return ImageFont.truetype(config.FONTS[kind], int(size))


def fit_size(text, max_w, start, kind="bold", min_size=20):
    s = int(start)
    while s > min_size and font(s, kind).getlength(text) > max_w:
        s -= 2
    return s


def clamp01(x):
    return max(0.0, min(1.0, x))


def ease_out_cubic(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_out_back(x, s=1.9):
    x = clamp01(x)
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


@lru_cache(maxsize=1024)
def text_layer(text, size, kind="bold", fill=WHITE, stroke=0, stroke_fill=BLACK):
    f = font(size, kind)
    l, t, r, b = f.getbbox(text, stroke_width=stroke)
    pad = stroke + 4
    bg = (stroke_fill if stroke else fill)[:3] + (0,)
    im = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), bg)
    ImageDraw.Draw(im).text((pad - l, pad - t), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return im


@lru_cache(maxsize=256)
def pill_layer(text, size, fg, bg, px=38, py=16, kind="bold", outline=None, ow=0):
    f = font(size, kind)
    l, t, r, b = f.getbbox(text)
    w, h = (r - l) + 2 * px, (b - t) + 2 * py
    im = Image.new("RGBA", (w + 4, h + 4), tuple(bg[:3]) + (0,))
    dd = ImageDraw.Draw(im)
    dd.rounded_rectangle([2, 2, w + 2, h + 2], radius=h // 2, fill=bg, outline=outline, width=ow)
    dd.text((2 + px - l, 2 + py - t), text, font=f, fill=fg)
    return im


def paste_center(img, layer, cx, cy, scale=1.0, alpha=1.0):
    if scale <= 0.01 or alpha <= 0.01:
        return
    if abs(scale - 1) > 0.005:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BICUBIC)
    if alpha < 0.99:
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        layer = layer.copy()
        layer.putalpha(a)
    img.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)


def tcol(m, side):
    c = m[side].get("color")
    if c:
        return tuple(c)
    return config.DEFAULT_HOME_COLOR if side == "home" else config.DEFAULT_AWAY_COLOR


def running_score(m, upto):
    """Spielstand nach dem Tor mit Index upto (−1 = vor dem ersten Tor)."""
    h = a = 0
    for g in m["goals"][: upto + 1]:
        if g["side"] == "home":
            h += 1
        else:
            a += 1
    return h, a


def draw_scorebug(img, d, m, sh, sa, pop=1.0):
    d.rounded_rectangle([140, 178, 940, 300], radius=61, fill=PANEL + (225,), outline=(255, 255, 255, 55), width=2)
    paste_center(img, text_layer(m["home"]["tla"], 54, "bold", tcol(m, "home")), 300, 240)
    paste_center(img, text_layer(m["away"]["tla"], 54, "bold", tcol(m, "away")), 780, 240)
    paste_center(img, text_layer(f"{sh} : {sa}", 78, "bold", WHITE), 540, 240, scale=pop)


# --- Szenen ------------------------------------------------------------------
def scene_hook(img, d, m, sc, t, T):
    label = ui("matchday", league=m["league"].upper(), n=m["matchday"])
    paste_center(img, text_layer(label, 34, "med", MUTED), 540, 215, alpha=ease_out_cubic(t / 0.3))
    paste_center(img, pill_layer(ui("full_time"), 44, WHITE, (226, 36, 60, 255)), 540, 315, scale=ease_out_back(t / 0.4))

    e = ease_out_back((t - 0.05) / 0.5)
    for side, cx in (("home", 290), ("away", 790)):
        name = m[side]["short"].upper()
        size = fit_size(name, 470, 74)
        shift = (1 - ease_out_cubic((t - 0.05) / 0.4)) * (-420 if side == "home" else 420)
        paste_center(img, text_layer(name, size, "bold", tcol(m, side), 5, BLACK), cx + shift, 520, alpha=ease_out_cubic(t / 0.25))
    sh, sa = m["score"]
    paste_center(img, text_layer(f"{sh} : {sa}", fit_size(f"{sh} : {sa}", 900, 330), "bold", WHITE, 8, BLACK), 540, 790, scale=e)

    es = ease_out_back((t - 0.55) / 0.45)
    if es > 0.02:
        lay = pill_layer(tr(m.get("sticker")) or ui("full_time"), 48, (20, 20, 30), GOLD + (255,), px=44, py=22)
        lay = lay.rotate(4, expand=True, resample=Image.BICUBIC)
        paste_center(img, lay, 540, 1060, scale=es)


def scene_goal(img, d, m, sc, t, T):
    i = sc["idx"]
    g = m["goals"][i]
    side = g["side"]
    col = tcol(m, side)
    own = g.get("type") == "OWN"

    if t < 0.28:
        d.rectangle([0, 0, W, H], fill=(255, 255, 255, int(130 * (1 - t / 0.28))))

    after = t >= 0.35
    sh, sa = running_score(m, i if after else i - 1)
    pop = 1 + 0.4 * clamp01(1 - (t - 0.35) / 0.35) if after else 1.0
    draw_scorebug(img, d, m, sh, sa, pop)

    label = ui("own_goal") if own else ui("goal")
    paste_center(img, text_layer(label, 90, "bold", col, 7, BLACK), 540, 420, scale=ease_out_back(t / 0.35))

    minute = f"{g['minute']}+{g['injury']}'" if g.get("injury") else f"{g['minute']}'"
    paste_center(img, text_layer(minute, fit_size(minute, 880, 270), "bold", WHITE, 9, BLACK), 540, 590, scale=ease_out_back((t - 0.06) / 0.4))

    e = ease_out_cubic((t - 0.14) / 0.35)
    name = g["short"]
    paste_center(img, text_layer(name, fit_size(name, 940, 120), "bold", WHITE, 7, BLACK), 540 + (1 - e) * 520, 790, alpha=e)

    e2 = ease_out_cubic((t - 0.24) / 0.3)
    chip = ui("goal_for", team=team_name(m, side))
    paste_center(img, pill_layer(chip, 40, col, PANEL + (220,), outline=col + (255,), ow=4), 540, 905, alpha=e2, scale=0.9 + 0.1 * e2)

    # Kette: wer hat das Tor vorbereitet?
    nodes = tr(g.get("chain")) or []
    if nodes:
        size = 46
        while size > 26:
            widths = [pill_layer(n, size, WHITE, PANEL + (220,)).width for n in nodes]
            if sum(widths) + 54 * (len(nodes) - 1) <= 900:
                break
            size -= 2
        widths = [pill_layer(n, size, WHITE, PANEL + (220,)).width for n in nodes]
        total = sum(widths) + 54 * (len(nodes) - 1)
        x = (W - total) / 2
        cy = 1030
        for k, n in enumerate(nodes):
            ap = ease_out_cubic((t - (0.4 + 0.2 * k)) / 0.28)
            last = k == len(nodes) - 1
            lay = pill_layer(n, size, (10, 14, 28) if last else WHITE, (col + (255,)) if last else PANEL + (220,))
            paste_center(img, lay, x + widths[k] / 2, cy, alpha=ap, scale=0.85 + 0.15 * ap)
            x += widths[k]
            if k < len(nodes) - 1:
                ax = x + 27
                a = int(255 * ap)
                d.polygon([(ax - 9, cy - 15), (ax + 9, cy), (ax - 9, cy + 15)], fill=(255, 255, 255, a))
                x += 54

    tag = tr(g.get("tag"))
    if tag:
        e3 = ease_out_cubic((t - 0.7) / 0.4)
        paste_center(img, text_layer(tag, fit_size(tag, 900, 38, "med"), "med", MUTED, 3, BLACK), 540, 1130, alpha=e3)


def scene_stats(img, d, m, sc, t, T):
    sh, sa = m["score"]
    draw_scorebug(img, d, m, sh, sa)
    paste_center(img, text_layer(ui("stats_title"), 76, "bold", WHITE, 6, BLACK), 540, 440, scale=ease_out_back(t / 0.35))
    d.rounded_rectangle([60, 495, 960, 1140], radius=44, fill=PANEL + (200,))
    x0, bw = 115, 810
    d.text((x0, 548), m["home"]["tla"], font=font(40), fill=tcol(m, "home") + (255,), anchor="lm")
    d.text((x0 + bw, 548), m["away"]["tla"], font=font(40), fill=tcol(m, "away") + (255,), anchor="rm")
    y = 625
    for k, (label, (a, b)) in enumerate(m["stats"].items()):
        p = ease_out_cubic((t - 0.15 - 0.15 * k) / 0.55)
        if p > 0:
            al = int(255 * p)
            d.text((540, y), stat_label(label), font=font(34, "med"), fill=MUTED + (al,), anchor="mm")
            d.text((x0, y), str(a), font=font(52), fill=WHITE + (al,), anchor="lm")
            d.text((x0 + bw, y), str(b), font=font(52), fill=WHITE + (al,), anchor="rm")
            by0, by1 = y + 42, y + 86
            d.rounded_rectangle([x0, by0, x0 + bw, by1], radius=22, fill=(255, 255, 255, 40))
            tot = (a + b) or 1
            wa, wb = int(bw * a / tot * p), int(bw * b / tot * p)
            if wa > 12:
                d.rounded_rectangle([x0, by0, x0 + wa - 3, by1], radius=22, fill=tcol(m, "home") + (255,))
            if wb > 12:
                d.rounded_rectangle([x0 + bw - wb + 3, by0, x0 + bw, by1], radius=22, fill=tcol(m, "away") + (255,))
        y += 130


def scene_table(img, d, m, sc, t, T):
    sh, sa = m["score"]
    draw_scorebug(img, d, m, sh, sa)
    paste_center(img, text_layer(ui("table_title"), 76, "bold", WHITE, 6, BLACK), 540, 440, scale=ease_out_back(t / 0.35))
    y = 520
    for k, row in enumerate(m["table"]):
        p = ease_out_cubic((t - 0.12 - 0.12 * k) / 0.35)
        off = int((1 - p) * 700)
        hl = row.get("hl", 0)
        if hl == 1:
            bg, fg = (226, 36, 60, 240), WHITE
        elif hl == 2:
            bg, fg = GOLD + (240,), (20, 20, 30)
        else:
            bg, fg = PANEL + (205,), WHITE
        d.rounded_rectangle([90 - off, y, 940 - off, y + 104], radius=30, fill=bg)
        cy = y + 52
        d.text((130 - off, cy), str(row["pos"]), font=font(52), fill=fg + (255,), anchor="lm")
        rname = row.get("name_" + config.VIDEO_LANG) or row["name"]
        d.text((215 - off, cy), rname, font=font(fit_size(rname, 460, 50)), fill=fg + (255,), anchor="lm")
        d.text((900 - off, cy), ui("pts", n=row["pts"]), font=font(48), fill=fg + (255,), anchor="rm")
        y += 122


def scene_outro(img, d, m, sc, t, T):
    nxt = m.get("next")
    y_cta = 760
    if nxt:
        paste_center(img, pill_layer(ui("next_match"), 40, (20, 20, 30), GOLD + (255,), px=40, py=16), 540, 450, scale=ease_out_back(t / 0.35))
        for k, (name, cy) in enumerate(((tr(nxt["home"]), 600), (tr(nxt["away"]), 790))):
            e = ease_out_back((t - 0.12 - 0.12 * k) / 0.45)
            paste_center(img, text_layer(name.upper(), fit_size(name.upper(), 940, 120), "bold", WHITE, 7, BLACK), 540, cy, scale=e)
        paste_center(img, text_layer("VS", 54, "bold", GOLD, 4, BLACK), 540, 695, scale=ease_out_back((t - 0.2) / 0.4))
        paste_center(img, text_layer(tr(nxt["when"]), 50, "med", WHITE, 4, BLACK), 540, 905, alpha=ease_out_cubic((t - 0.4) / 0.4))
        y_cta = 1060
    e = ease_out_back((t - 0.5) / 0.5)
    if e > 0.02:
        d_w, d_h = 900, 230
        box = Image.new("RGBA", (d_w + 4, d_h + 4), (0, 0, 0, 0))
        bd = ImageDraw.Draw(box)
        bd.rounded_rectangle([2, 2, d_w + 2, d_h + 2], radius=44, fill=PANEL + (225,), outline=GOLD + (255,), width=5)
        cta = ui("comment_cta")
        bd.text((d_w / 2 + 2, 78), ui("who_wins"), font=font(84), fill=GOLD + (255,), anchor="mm")
        bd.text((d_w / 2 + 2, 170), cta, font=font(fit_size(cta, 840, 46, "med"), "med"), fill=WHITE + (255,), anchor="mm")
        paste_center(img, box, 540, y_cta + 40, scale=e)


def _score_for(m, info):
    """Spielstand passend zur Zusatzszene: nach Minute, sonst Endstand."""
    mm = info.get("minute") if info.get("minute") is not None else info.get("after_minute")
    if mm is None:
        return tuple(m["score"])
    h = a = 0
    for g in m["goals"]:
        if g["minute"] <= mm:
            if g["side"] == "home":
                h += 1
            else:
                a += 1
    return h, a


def scene_info(img, d, m, sc, t, T):
    """Zusatzszene: Beinahe-Tor, Parade, Hintergrund, auffälliger Spieler."""
    info = sc["info"]
    col = tcol(m, info["side"]) if info.get("side") else GOLD
    sh, sa = _score_for(m, info)
    draw_scorebug(img, d, m, sh, sa)

    big = tr(info.get("minute_text")) or (f"{info['minute']}'" if info.get("minute") is not None else None)
    if big:
        label_y, title_y, lines_y0 = 420, 790, 900
    else:
        label_y, title_y, lines_y0 = 470, 650, 790

    label = tr(info["label"])
    paste_center(img, text_layer(label, fit_size(label, 900, 84), "bold", col, 7, BLACK), 540, label_y, scale=ease_out_back(t / 0.35))
    if big:
        paste_center(img, text_layer(big, fit_size(big, 880, 270), "bold", WHITE, 9, BLACK), 540, 590, scale=ease_out_back((t - 0.06) / 0.4))
    e = ease_out_cubic((t - 0.14) / 0.35)
    title = tr(info["title"])
    paste_center(img, text_layer(title, fit_size(title, 940, 120 if big else 132), "bold", WHITE, 7, BLACK), 540 + (1 - e) * 520, title_y, alpha=e)

    lines = tr(info.get("lines")) or []
    if lines:
        e2 = ease_out_cubic((t - 0.3) / 0.35)
        h = 36 + 76 * len(lines)
        d.rounded_rectangle([140, lines_y0, 940, lines_y0 + h], radius=36, fill=PANEL + (int(205 * e2),))
        for k, ln in enumerate(lines):
            ek = ease_out_cubic((t - 0.4 - 0.2 * k) / 0.3)
            f = font(fit_size(ln, 730, 44, "med"), "med")
            d.text((540, lines_y0 + 18 + 38 + 76 * k), ln, font=f, fill=WHITE + (int(255 * ek),), anchor="mm")


SCENES = {"hook": scene_hook, "goal": scene_goal, "stats": scene_stats, "table": scene_table, "outro": scene_outro, "info": scene_info}


def draw_caption(img, sc, t):
    for a, b, text in sc["chunks"]:
        if a <= t < b:
            s = 0.86 + 0.14 * ease_out_cubic((t - a) / 0.12)
            col = GOLD if any(ch.isdigit() for ch in text) else WHITE
            paste_center(img, text_layer(text, fit_size(text, 940, 80), "bold", col, 10, BLACK), 540, CAPTION_Y, scale=s)
            return


# --- Frame-Rendering -----------------------------------------------------------
def render_frame(i: int) -> bytes:
    S = _STATE
    T = i / config.FPS
    k = max(0, min(len(S["scenes"]) - 1, bisect.bisect_right(S["starts"], T) - 1))
    sc = S["scenes"][k]
    t = T - sc["start"]
    a = 0.5 + 0.5 * math.sin(2 * math.pi * T / 2.8)  # Flutlicht/Zuschauer pulsieren sanft
    img = Image.blend(S["bg"][0], S["bg"][1], a)
    d = ImageDraw.Draw(img, "RGBA")
    SCENES[sc["kind"]](img, d, S["m"], sc, t, T)
    draw_caption(img, sc, t)
    return img.tobytes()


def build_scene_list(m, scr):
    """Pflichtszenen mit Sortierschlüssel 'order' (Tore nach Spielminute)."""
    scenes = [{"kind": "hook", "order": 0.0, **scr["hook"]}]
    for i, g in enumerate(m["goals"]):
        scenes.append({"kind": "goal", "idx": i, "order": g["minute"] + (g.get("injury") or 0) / 10, **scr["goals"][i]})
    if m.get("stats"):
        scenes.append({"kind": "stats", "order": 100.0, **scr["stats"]})
    if m.get("table"):
        scenes.append({"kind": "table", "order": 102.0, **scr["table"]})
    scenes.append({"kind": "outro", "order": 103.0, **scr["outro"]})
    return scenes


def build_extra_scenes(m):
    """Optionale Zusatzszenen (m['extras']); werden nach Priorität ergänzt, bis die Mindestlänge erreicht ist."""
    out = []
    for ex in m.get("extras", []):
        order = ex.get("order", ex["minute"] + 0.5 if ex.get("minute") is not None else 50.0)
        out.append({"kind": "info", "info": ex, "order": order, "prio": ex.get("prio", 9),
                    "speech": tr(ex["speech"]), "caption": tr(ex["caption"])})
    return out


def produce(m: dict, out_path: str, work_dir: str | None = None, preview_dir: str | None = None) -> dict:
    """Komplettes Video: Sprecher -> Timing -> Audiomix -> Frames -> MP4."""
    work = work_dir or os.path.join(config.OUTPUT_DIR, "work")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    base = build_scene_list(m, scriptmod.build_script(m))
    extras = build_extra_scenes(m)
    for i, sc in enumerate(base + extras):
        sc["wav"] = os.path.join(work, f"v{i:02d}.wav")
        al = voice.synth_scene(sc["speech"], sc["wav"])
        sc["audio_len"] = al
        sc["dur"] = max(3.2 if sc["kind"] in ("hook", "outro") else 3.0, config.SCENE_LEAD + al + config.SCENE_TAIL)
        sc["chunks"] = scriptmod.make_chunks(sc["caption"], config.SCENE_LEAD, config.SCENE_LEAD + al, sc["dur"])

    # Zielspanne: Zusatzszenen nach Priorität ergänzen, bis die Mindestlänge erreicht ist
    total = sum(s["dur"] for s in base)
    chosen = []
    for ex in sorted(extras, key=lambda s: s["prio"]):
        if total >= config.MIN_TOTAL:
            break
        if total + ex["dur"] <= config.MAX_TOTAL:
            chosen.append(ex)
            total += ex["dur"]
    scenes = sorted(base + chosen, key=lambda s: s["order"])
    t = 0.0
    for sc in scenes:
        sc["start"] = t
        t += sc["dur"]
    total = t

    audio = voice.mix_audio(scenes, total, work)

    _STATE.clear()
    _STATE.update(
        m=m,
        scenes=scenes,
        starts=[s["start"] for s in scenes],
        bg=background.make_stadium(W, H, (tcol(m, "home"), tcol(m, "away"))),
    )
    n = int(math.ceil(total * config.FPS))
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(config.FPS), "-i", "-",
        "-i", audio,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    ctx = mp.get_context("fork")  # Kindprozesse erben _STATE
    with ctx.Pool(config.RENDER_PROCESSES) as pool:
        for s in range(0, n, 48):
            for b in pool.map(render_frame, range(s, min(n, s + 48)), chunksize=4):
                proc.stdin.write(b)
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg fehlgeschlagen")

    info = {
        "duration": total,
        "lufs": voice.lufs(out_path),
        "scenes": [(s["kind"], round(s["start"], 2), round(s["dur"], 2)) for s in scenes],
    }
    if preview_dir:
        os.makedirs(preview_dir, exist_ok=True)
        for i, sc in enumerate(scenes):
            ts = sc["start"] + min(sc["dur"] - 0.2, 2.4)
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", out_path,
                 "-frames:v", "1", os.path.join(preview_dir, f"scene_{i:02d}_{sc['kind']}.png")],
                check=True,
            )
    return info
