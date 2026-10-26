"""Kommentartexte (Sprecher + Untertitel), zweisprachig (de/en).

Liegt im Spiel-JSON ein "script"-Block vor (z. B. von einem Sprachmodell geschrieben),
wird er benutzt: entweder direkt oder pro Sprache {"de": {...}, "en": {...}}.
Fehlt er, erzeugen einfache Vorlagen den Text aus den Spieldaten.
Wichtig: Texte immer in eigenen Worten schreiben, keine Ticker-Texte kopieren.
"""
import re

from i18n import lang

# --- Zahlwörter (für die Sprachausgabe) ------------------------------------------
_U = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "zwölf",
      "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
_T = {20: "zwanzig", 30: "dreißig", 40: "vierzig", 50: "fünfzig", 60: "sechzig", 70: "siebzig", 80: "achtzig", 90: "neunzig"}

_EU = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
       "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_ET = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}


def de_num(n: int) -> str:
    """Zahl als deutsches Wort, 0–199."""
    if n < 20:
        return _U[n]
    if n < 100:
        t, u = n // 10 * 10, n % 10
        return _T[t] if u == 0 else ("ein" if u == 1 else _U[u]) + "und" + _T[t]
    r = n - 100
    return "hundert" + ("" if r == 0 else de_num(r))


def en_num(n: int) -> str:
    """Zahl als englisches Wort, 0–199."""
    if n < 20:
        return _EU[n]
    if n < 100:
        t, u = n // 10 * 10, n % 10
        return _ET[t] if u == 0 else f"{_ET[t]}-{_EU[u]}"
    r = n - 100
    return "one hundred" + ("" if r == 0 else " " + en_num(r))


def _minute_words(g: dict) -> str:
    if lang() == "en":
        return "Stoppage time" if g.get("injury") else f"Minute {en_num(int(g['minute']))}"
    return "Nachspielzeit" if g.get("injury") else f"Minute {de_num(int(g['minute']))}"


def _minute_label(g: dict) -> str:
    n = int(g["minute"])
    if lang() == "en":
        if g.get("injury"):
            return f"{n}+{g['injury']}'"
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    return f"{n}.+{g['injury']}" if g.get("injury") else f"{n}."


def default_script(m: dict) -> dict:
    sh, sa = m["score"]
    home, away = m["home"]["short"], m["away"]["short"]
    en = lang() == "en"
    if en:
        s = {
            "hook": {
                "speech": (f"{m['league']}, matchday {en_num(int(m['matchday']))}: " if m.get("matchday") else f"{m['league']}: ")
                + f"{home} versus {away} ends {en_num(sh)} to {en_num(sa)}. Here are the goals!",
                "caption": f"{home} {sh}-{sa} {away}. Here are the goals!" if sh + sa else f"{home} {sh}-{sa} {away}. No goals tonight.",
            },
            "goals": [],
            "stats": {"speech": "And here are the numbers from the match.", "caption": "The numbers from the match"},
            "table": {"speech": "Here is how the table looks now.", "caption": "Here is how the table looks now"},
            "outro": {
                "speech": "Who wins the next one? Drop your pick in the comments!",
                "caption": "Who wins the next one? Drop your pick in the comments!",
            },
        }
    else:
        s = {
            "hook": {
                "speech": (f"{m['league']}, {de_num(int(m['matchday']))}. Spieltag: " if m.get("matchday") else f"{m['league']}: ")
                + f"{home} gegen {away}: {de_num(sh)} zu {de_num(sa)}. Das waren die Tore!",
                "caption": f"{home} {sh}:{sa} {away}. Das waren die Tore!",
            },
            "goals": [],
            "stats": {"speech": "Und so sahen die Zahlen zum Spiel aus.", "caption": "Die Zahlen zum Spiel"},
            "table": {"speech": "So sieht die Tabelle jetzt aus.", "caption": "So sieht die Tabelle jetzt aus"},
            "outro": {
                "speech": "Wer gewinnt das nächste Spiel? Schreib es in die Kommentare!",
                "caption": "Wer gewinnt das nächste Spiel? Schreib es in die Kommentare!",
            },
        }
    h = a = 0
    for g in m["goals"]:
        side = g["side"]
        team = m[side]["short"]
        name = g["short"].title()
        mw, ml = _minute_words(g), _minute_label(g)
        own, opp = (h, a) if side == "home" else (a, h)
        own, owngoal = own + 1, g.get("type") == "OWN"
        if side == "home":
            h += 1
        else:
            a += 1
        own_after, opp_after = (h, a) if side == "home" else (a, h)
        late = (g.get("minute") or 0) >= 85
        before_diff = (own_after - 1) - opp_after
        if before_diff < 0 and own_after > opp_after:
            kind = "turn"
        elif before_diff < 0 and own_after == opp_after:
            kind = "level"
        elif own_after - 1 == 0 and opp_after == 0:
            kind = "open"
        elif before_diff < 0:
            kind = "back"
        elif before_diff == 0:
            kind = "lead"
        elif own_after - opp_after >= 3:
            kind = "rout"
        else:
            kind = "extend"
        if late and kind in ("lead", "turn", "level", "extend", "rout"):
            late_kind = True
        else:
            late_kind = False
        hs, as_ = m["home"]["short"], m["away"]["short"]
        if en:
            who = f"own goal by {name}" if owngoal else name
            phrase = {
                "open": f"{team} break the deadlock{'' if owngoal else ' through ' + name}",
                "lead": f"{team} take the lead{'' if owngoal else ' through ' + name}",
                "turn": f"{team} turn it around{'' if owngoal else ' thanks to ' + name}",
                "level": f"{team} are level again{'' if owngoal else ', ' + name + ' equalises'}",
                "back": f"{team} pull one back{'' if owngoal else ' through ' + name}",
                "extend": f"{team} extend the lead{'' if owngoal else ' through ' + name}",
                "rout": f"{team} are cruising{'' if owngoal else ', ' + name + ' scores again'}",
            }[kind]
            if owngoal:
                phrase += f" after an own goal by {name}"
            if late_kind:
                phrase += ", late on"
            sp = f"{mw}: {phrase}. {hs} {en_num(h)}, {as_} {en_num(a)}."
            cap = f"{ml} minute: {phrase}. {hs} {h}-{a} {as_}."
        else:
            phrase = {
                "open": f"{team} bricht den Bann{'' if owngoal else ' durch ' + name}",
                "lead": f"{team} geht in Führung{'' if owngoal else ' durch ' + name}",
                "turn": f"{team} dreht das Spiel{'' if owngoal else ' dank ' + name}",
                "level": f"{team} gleicht aus{'' if owngoal else ' durch ' + name}",
                "back": f"{team} verkürzt{'' if owngoal else ' durch ' + name}",
                "extend": f"{team} baut die Führung aus{'' if owngoal else ' durch ' + name}",
                "rout": f"{team} legt nach{'' if owngoal else ' durch ' + name}",
            }[kind]
            if owngoal:
                phrase += f" nach einem Eigentor von {name}"
            if late_kind:
                phrase += ", spät im Spiel"
            sp = f"{mw}: {phrase}. {hs} {de_num(h)}, {as_} {de_num(a)}."
            cap = f"{ml} Minute: {phrase}. {hs} {h}:{a} {as_}."
        s["goals"].append({"speech": sp, "caption": cap})
    return s


def _custom(m: dict) -> dict:
    s = m.get("script") or {}
    if "hook" in s or "goals" in s:  # einsprachiger Block
        return s
    return s.get(lang()) or s.get("en") or {}


def build_script(m: dict) -> dict:
    base = default_script(m)
    custom = _custom(m)
    for k in ("hook", "stats", "table", "outro"):
        if k in custom:
            base[k] = {**base[k], **custom[k]}
    for i, g in enumerate(custom.get("goals", [])):
        if i < len(base["goals"]):
            base["goals"][i] = {**base["goals"][i], **g}
    return base


_STOP_DE = {"und", "in", "am", "an", "zu", "der", "die", "das", "den", "dem", "ein", "eine", "mit", "von", "auf",
            "im", "ins", "vor", "vorm", "aus", "bei", "nach", "für", "noch", "mal", "es", "ist", "wird", "so", "als",
            "nicht", "durch", "zum", "zur", "selbst", "wieder", "schon", "sich", "ihn", "ihm", "dann", "aber", "oder"}
_STOP_EN = {"the", "a", "an", "of", "to", "and", "in", "at", "on", "for", "with", "by", "his", "her", "their", "after",
            "from", "into", "he", "she", "it", "is", "are", "as", "but", "or", "then", "off", "out", "up", "just",
            "only", "that", "this", "its", "than", "over", "near", "through", "while"}


def make_chunks(caption: str, t0: float, t1: float, t_end: float, max_chars: int | None = None, max_words: int | None = None):
    """Teilt den Untertitel in kurze Häppchen (ohne Satzbrüche an Füllwörtern) über die Sprechzeit."""
    en = lang() == "en"
    max_chars = max_chars or (22 if en else 20)
    max_words = max_words or (4 if en else 3)
    stop = _STOP_EN if en else _STOP_DE
    words = caption.split()
    groups, i = [], 0
    while i < len(words):
        cur = [words[i]]
        i += 1
        while i < len(words):
            text = " ".join(cur)
            if cur[-1][-1] in ".!?:;," and not re.fullmatch(r"\d+\.", cur[-1]):
                break
            last_is_stop = cur[-1].lower().strip(",.!?:;") in stop
            if (len(cur) >= max_words or len(text) >= max_chars) and not last_is_stop:
                break
            if len(text) > 28:
                break
            cur.append(words[i])
            i += 1
        groups.append(" ".join(cur))
    weights = [len(g) + 3 for g in groups]
    total = sum(weights) or 1
    out, t = [], t0
    for g, wt in zip(groups, weights):
        dt = (t1 - t0) * wt / total
        out.append([t, t + dt, g])
        t += dt
    if out:
        out[-1][1] = t_end  # letzter Untertitel bleibt bis Szenenende stehen
    return [tuple(c) for c in out]
