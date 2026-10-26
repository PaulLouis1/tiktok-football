"""Tore und Zusatzszenen per Sprachmodell mit Websuche ergänzen (optional).

Die Gratis-API von football-data.org liefert keine Torfolge. Ist ANTHROPIC_API_KEY gesetzt, sucht das Modell Torschützen,
Minuten und ein paar Besonderheiten zum Spiel im Netz und schreibt Kurztexte in eigenen Worten.
Alles wird gegen den Endstand der API geprüft; passt etwas nicht, wird die Ergänzung verworfen (-> Video ohne Tore).
"""
import json
import re

import requests

import config

API_URL = "https://api.anthropic.com/v1/messages"


def _prompt(m: dict, lang: str) -> str:
    h, a = m["home"]["name"], m["away"]["name"]
    sh, sa = m["score"]
    language = "German" if lang == "de" else "English"
    return f"""Find the facts of this finished football match with web search.

Match: {h} (home) vs {a} (away), {m['league']}, matchday {m.get('matchday')}, final score {sh}:{sa} (home:away).

Return ONLY one JSON object (no prose, no code fence) with this shape:
{{
  "goals": [{{"minute": 16, "injury": null, "side": "home" or "away", "type": "REGULAR" or "OWN" or "PENALTY",
             "scorer": "Full Name", "assist": "Full Name or null"}}],
  "extras": [{{"kind": "chance" or "save" or "player" or "context",
              "minute": 34 or null, "side": "home" or "away" or null,
              "label": "short heading, max 14 characters, uppercase",
              "title": "max 22 characters, uppercase",
              "lines": ["max 38 characters", "max 38 characters"],
              "speech": "1-2 short sentences for a commentator",
              "caption": "the same sentence(s) as subtitle"}}]
}}

Rules:
- "side" of a goal is the team the goal COUNTS FOR (for an own goal: the team that benefits). The number of goals per side must match the final score exactly. "minute" is the regular minute (for 90+5 use minute 90 and injury 5).
- At most 3 extras, only facts you found in the sources (near misses, saves, a standout player, important context). If you found nothing reliable, return an empty list.
- Write label/title/lines/speech/caption in {language}, in your own words, never copying sentences from the sources. Speech: write all numbers as words.
- If you cannot find reliable goal data for this exact match, return {{"goals": [], "extras": []}}. Never guess."""


def _call(prompt: str) -> str:
    body = {
        "model": config.ENRICH_MODEL,
        "max_tokens": 3000,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post(
        API_URL,
        headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=body,
        timeout=240,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Anthropic API {r.status_code}: {r.text[:200]}")
    texts = [b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"]
    return "\n".join(texts)


def _parse(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _digits_to_words(s: str, lang: str) -> str:
    from script import de_num, en_num

    conv = de_num if lang == "de" else en_num
    return re.sub(r"\b(\d{1,3})\b", lambda mo: conv(int(mo.group(1))), s)


def validate_and_convert(m: dict, data: dict | None, lang: str) -> tuple[list[dict], list[dict]] | None:
    """Prüft die Antwort gegen den Endstand; liefert (goals, extras) im Videomodell oder None."""
    if not data or not data.get("goals"):
        return None
    sh, sa = m["score"]
    goals = []
    for g in data["goals"]:
        try:
            minute = int(g["minute"])
            side = g["side"]
            if side not in ("home", "away") or not 1 <= minute <= 130:
                return None
            scorer = str(g["scorer"]).strip()
            if not scorer:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        names = [x.strip().split()[-1] for x in (g.get("assist"), scorer) if x and str(x).strip() and str(x).lower() != "null"]
        goals.append({
            "minute": minute,
            "injury": int(g["injury"]) if g.get("injury") else None,
            "side": side,
            "type": g.get("type") if g.get("type") in ("REGULAR", "OWN", "PENALTY") else "REGULAR",
            "scorer": scorer,
            "short": scorer.split()[-1].upper(),
            "chain": names if len(names) > 1 else [],
            "tag": "",
        })
    if sum(g["side"] == "home" for g in goals) != sh or sum(g["side"] == "away" for g in goals) != sa:
        return None
    goals.sort(key=lambda g: (g["minute"], g["injury"] or 0))

    extras = []
    for i, e in enumerate(data.get("extras") or []):
        try:
            speech = _digits_to_words(str(e["speech"]), lang)
            caption = str(e.get("caption") or e["speech"])
            ex = {
                "prio": 3 + i,
                "side": e.get("side") if e.get("side") in ("home", "away") else None,
                "label": str(e["label"])[:18].upper(),
                "title": str(e["title"])[:26].upper(),
                "lines": [str(x)[:44] for x in (e.get("lines") or [])[:2]],
                "speech": speech,
                "caption": caption,
            }
            if e.get("minute"):
                ex["minute"] = int(e["minute"])
            else:
                ex["order"] = 0.5  # ohne Minute: als Hintergrund direkt nach dem Einstieg
            extras.append(ex)
        except (KeyError, TypeError, ValueError):
            continue
    return goals, extras


def enrich(m: dict, lang: str) -> tuple[list[dict], list[dict]] | None:
    """Gibt (goals, extras) zurück oder None, wenn nichts Verlässliches gefunden wurde."""
    if not config.ANTHROPIC_API_KEY:
        return None
    text = _call(_prompt(m, lang))
    return validate_and_convert(m, _parse(text), lang)
