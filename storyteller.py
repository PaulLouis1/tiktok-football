"""Textautor nach prompts/master_prompt.md (Redaktion, Dramaturgie, Qualitätskontrolle).

Ablauf pro Spiel:
 1. FAKTEN aus den API-Daten bauen (nur, was wirklich geliefert wurde; Tabelle vorher/nachher selbst berechnet).
 2. Sprachmodell schreibt Hauptgeschichte, Hook, Szenentexte, Abschlussfrage, Titel/Beschreibung/Hashtags (JSON).
 3. Qualitätskontrolle in Code: Struktur, Zahlen nur aus den Fakten, Torschützen genannt, keine Like-/Follow-Floskel,
    Abschlussfrage vorhanden, Faktencheck per zweitem Modellaufruf, Sprechdauer mit der echten Stimme gemessen.
 4. Fällt etwas durch, bekommt das Modell Rückmeldung (max. 2 Korrekturrunden). Danach: Vorlagen-Video (nur Fakten).
Bei widersprüchlichen Daten (Tore passen nicht zum Endstand) wird nichts veröffentlicht (CriticalError).
"""
import json
import os
import re
import tempfile

import requests

import config

API_URL = "https://api.anthropic.com/v1/messages"
PROMPT_FILE = os.path.join(config.BASE_DIR, "prompts", "master_prompt.md")


class CriticalError(RuntimeError):
    """Daten widersprüchlich: Video darf nicht veröffentlicht werden."""


# --- 1. Fakten --------------------------------------------------------------------------------
def standings(m: dict) -> dict | None:
    """Tabelle nach dem Spiel (API) und daraus zurückgerechnet vor dem Spiel (vorläufig, ohne parallele Spiele)."""
    rows = (m.get("facts_raw") or {}).get("table") or []
    hid, aid = m["facts_raw"]["home_id"], m["facts_raw"]["away_id"]
    by_id = {r["team"]["id"]: r for r in rows}
    if hid not in by_id or aid not in by_id:
        return None
    sh, sa = m["score"]

    def pts(own, opp):
        return 3 if own > opp else 1 if own == opp else 0

    before = {}
    for r in rows:
        tid, p, gd, gf = r["team"]["id"], r["points"], r.get("goalDifference", 0), r.get("goalsFor", 0)
        if tid == hid:
            p, gd, gf = p - pts(sh, sa), gd - (sh - sa), gf - sh
        elif tid == aid:
            p, gd, gf = p - pts(sa, sh), gd - (sa - sh), gf - sa
        before[tid] = (p, gd, gf)
    order = sorted(before, key=lambda t: (-before[t][0], -before[t][1], -before[t][2]))
    pos_before = {t: i + 1 for i, t in enumerate(order)}
    out = {"provisional": True,
           "note": "Stand laut API nach Abpfiff; Vorher-Wert zurückgerechnet, parallele Spiele und Tie-Breaker nicht berücksichtigt"}
    for side, tid in (("home", hid), ("away", aid)):
        r = by_id[tid]
        out[side] = {
            "name": m[side]["short"],
            "position_before": pos_before[tid], "position_after": r["position"],
            "points_before": before[tid][0], "points_after": r["points"],
            "goal_difference_after": r.get("goalDifference"),
            "played": r.get("playedGames"),
        }
    out["top3"] = [{"position": r["position"], "name": r["team"].get("shortName") or r["team"]["name"], "points": r["points"]}
                   for r in rows[:3]]
    return out


def build_facts(m: dict, forms: dict | None = None) -> dict:
    fr = m.get("facts_raw") or {}
    goals = []
    for i, g in enumerate(m["goals"], 1):
        goals.append({
            "nr": i, "minute": g["minute"], "injury_time": g.get("injury"), "team": m[g["side"]]["short"], "side": g["side"],
            "scorer": g["scorer"], "assist": (g["chain"][0] if g.get("chain") else None), "type": g.get("type", "REGULAR"),
        })
    cards = []
    for b in fr.get("bookings") or []:
        if b.get("card") in ("RED", "YELLOW_RED"):
            side = "home" if (b.get("team") or {}).get("id") == fr.get("home_id") else "away"
            cards.append({"minute": b.get("minute"), "team": m[side]["short"], "side": side,
                          "player": (b.get("player") or {}).get("name"), "card": b["card"]})
    facts = {
        "competition": m["league"], "season": fr.get("season"), "matchday": m.get("matchday"), "date": fr.get("date"),
        "status": "FINISHED",
        "home": m["home"]["short"], "away": m["away"]["short"], "final_score": m["score"],
        "halftime_score": fr.get("halftime"),
        "goals": goals, "red_cards": cards,
        "standings": standings(m),
        "form_last_five": forms or None,
        "not_available": ["xG", "Torschüsse", "Großchancen", "Ballbesitz", "Aufstellungen", "VAR-Entscheidungen", "Zitate"],
    }
    if forms:
        for side in ("home", "away"):
            f = forms.get(side) or []
            facts.setdefault("form_summary", {})[side] = {
                "wins": sum(x["result"] == "W" for x in f), "draws": sum(x["result"] == "D" for x in f),
                "losses": sum(x["result"] == "L" for x in f),
            }
    # Plausibilitätsprüfung: Tore müssen zum Endstand passen
    sh, sa = m["score"]
    if sum(g["side"] == "home" for g in m["goals"]) != sh or sum(g["side"] == "away" for g in m["goals"]) != sa:
        raise CriticalError("Torfolge passt nicht zum Endstand")
    return facts


def _ints(obj) -> set[int]:
    return {int(x) for x in re.findall(r"\d+", json.dumps(obj, ensure_ascii=False))}


# --- 2. Prompt und Modellaufruf --------------------------------------------------------------------
def system_prompt(lang: str) -> str:
    with open(PROMPT_FILE, encoding="utf-8") as f:
        master = f.read()
    language = "natürliches Deutsch" if lang == "de" else "natural English (translate all rules above to English)"
    return master + f"""

## 10. Technische Umsetzung in dieser Pipeline (hat bei Ausgabe und Format Vorrang vor Abschnitt 9)

Du erhältst die Spieldaten als JSON unter FAKTEN. Verwende ausschließlich diese Daten. Antworte mit GENAU EINEM JSON-Objekt
(ohne Fließtext, ohne Codeblock). Statt der Ausgabeteile A–J gilt dieses Schema:

{{
  "story_type": "eine Kategorie aus Abschnitt 2",
  "story": "Hauptgeschichte in einem Satz",
  "hooks": ["Hook 1 (der beste, wird verwendet)", "Hook 2", "Hook 3"],
  "hook": {{"speech": "...", "caption": "..."}},
  "context": {{"label": "", "title": "", "lines": ["", ""], "speech": "...", "caption": "..."}} oder null,
  "goals": [{{"speech": "...", "caption": "..."}}],
  "extras": [{{"minute": 55 oder null, "side": "home" | "away" | null, "label": "", "title": "", "lines": [""],
              "speech": "...", "caption": "..."}}],
  "standings": {{"speech": "...", "caption": "..."}} oder null,
  "outro": {{"speech": "...", "caption": "..."}},
  "pronunciations": {{"Spielername": "Schreibweise, so wie er gesprochen wird"}},
  "title": "TikTok-Titel, höchstens 80 Zeichen",
  "description": "Videobeschreibung in 1-2 Sätzen",
  "hashtags": ["#fussball", "..."],
  "warnings": ["fehlende Daten oder Unsicherheiten, die du bemerkt hast"]
}}

Regeln für diese Pipeline:
- Sprache der Texte: {language}.
- "hook" ist die erste Szene (Spielstand wird als Grafik gezeigt). "context" erklärt knapp, warum das Spiel wichtig war
  (nur belegt: Tabelle, Form der letzten fünf Spiele); null, wenn nichts Belegtes vorliegt.
- "goals": GENAU ein Eintrag pro Tor, in der Reihenfolge der Tore aus den FAKTEN. Nenne Torschützen (Nachname reicht) und die
  Minute, bei vorhandenem Assist auch diesen. Der Spielstand wird als Grafik gezeigt, du musst ihn nicht jedes Mal sagen.
  Keine Entstehung erfinden: Nur sagen, was in den Fakten steht (Elfmeter, Eigentor, Assist, Minute).
- "extras": nur für belegte Platzverweise (red_cards) oder andere Wendepunkte aus den Fakten, höchstens 2.
- "standings": 1-2 Sätze zur Tabellenfolge (Platz und Punkte vorher/nachher laut Feld standings). Wenn standings fehlt: null.
  Wenn "provisional" true ist, kennzeichne die Einordnung als vorläufig. Vorher-Nachher-Zahlen werden als Grafik ergänzt.
- "outro": kurze sachliche Einordnung und eine konkrete Diskussionsfrage zum Spiel. Keine Bitte um Likes, Follows oder Kommentare
  allgemein.
- "speech" ist der gesprochene Text: alle Zahlen als Wörter geschrieben (Minuten, Ergebnisse, Plätze). Kurze Sätze.
- "caption" ist derselbe Text als Untertitel mit Ziffern (Ergebnisse als 2:1, Minuten als 34.). Gleiche Wörter und gleiche Reihenfolge.
- Gesamtumfang aller "speech"-Felder zusammen: ca. 170 bis 190 Wörter (ergibt 65-80 Sekunden Video). Das wird mit der echten Stimme gemessen.
- Grafikfelder: "label" höchstens 14 Zeichen in Großbuchstaben, "title" höchstens 22 Zeichen in Großbuchstaben, jede Zeile in "lines" höchstens 38 Zeichen.
- "pronunciations": nur für Namen, die eine deutsche Sprachausgabe falsch sprechen würde (fremde Namen); sonst leeres Objekt.
- Nenne in "warnings" ehrlich, was fehlt (zum Beispiel Statistiken oder Aufstellungen). Erfinde nichts.
"""


def _call(system: str, messages: list[dict], max_tokens: int = 3500) -> str:
    r = requests.post(
        API_URL,
        headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": config.STORY_MODEL, "max_tokens": max_tokens, "system": system, "messages": messages},
        timeout=180,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Anthropic API {r.status_code}: {r.text[:200]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def _parse(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(text[a : b + 1])
    except json.JSONDecodeError:
        return None


# --- 3. Qualitätskontrolle ------------------------------------------------------------------------
_BAD_CTA = re.compile(r"\b(folg\w*|like\w*|abonnier\w*|subscribe|follow\w*|teil\w* das video)\b", re.I)


def _texts(d: dict):
    for k in ("hook", "context", "standings", "outro"):
        if d.get(k):
            yield k, d[k]
    for i, g in enumerate(d.get("goals") or []):
        yield f"goal{i + 1}", g
    for i, e in enumerate(d.get("extras") or []):
        yield f"extra{i + 1}", e


def check_structure(d: dict | None, facts: dict) -> list[str]:
    errs = []
    if not d:
        return ["Antwort war kein gültiges JSON"]
    for k in ("hook", "goals", "outro", "title"):
        if not d.get(k):
            errs.append(f"Feld '{k}' fehlt")
    if errs:
        return errs
    if len(d["goals"]) != len(facts["goals"]):
        errs.append(f"'goals' muss genau {len(facts['goals'])} Einträge haben, hat aber {len(d['goals'])}")
        return errs
    for name, t in _texts(d):
        if not str(t.get("speech", "")).strip() or not str(t.get("caption", "")).strip():
            errs.append(f"{name}: speech oder caption leer")
    allowed = _ints(facts)
    for name, t in _texts(d):
        bad = sorted({int(x) for x in re.findall(r"\d+", str(t.get("caption", "")))} - allowed)
        if bad:
            errs.append(f"{name}: Zahl(en) {bad} stehen nicht in den Fakten (nur belegte Zahlen verwenden)")
    for g, fg in zip(d["goals"], facts["goals"]):
        last = fg["scorer"].split()[-1].lower()
        if last not in str(g.get("speech", "")).lower():
            errs.append(f"Tor {fg['nr']}: Torschütze {fg['scorer']} wird im Sprechtext nicht genannt")
    outro = d["outro"]
    if _BAD_CTA.search(str(outro.get("speech", ""))):
        errs.append("outro: keine generische Bitte um Likes/Follows, stattdessen eine konkrete Spielfrage")
    if "?" not in str(outro.get("caption", "")):
        errs.append("outro: es fehlt eine konkrete Diskussionsfrage (mit Fragezeichen)")
    for e in d.get("extras") or []:
        if e.get("label") and len(str(e["label"])) > 18 or e.get("title") and len(str(e["title"])) > 26:
            errs.append("extras: label/title zu lang")
    return errs


def fact_check(d: dict, facts: dict) -> list[str]:
    """Zweiter Modellaufruf: Aussagen, die NICHT durch die Fakten belegt sind."""
    script = {k: v for k, v in d.items() if k in ("hook", "context", "goals", "extras", "standings", "outro")}
    prompt = (
        "Prüfe ein Fußball-Skript streng gegen die FAKTEN. Liste jede Aussage im Skript, die durch die FAKTEN nicht eindeutig "
        "belegt ist (erfundene Entstehung von Toren, Taktik, Motive, Zitate, Verletzungen, Kausalzusammenhänge ohne Beleg, falsche "
        "Zahlen, falsche Zuordnung zu Mannschaften, nicht belegte Superlative). Meinungsstarke, aber belegte Einordnungen sind "
        "erlaubt. Antworte NUR mit JSON: {\"unsupported\": [\"Aussage ... (Grund)\"]}. Leere Liste, wenn alles belegt ist.\n\n"
        f"FAKTEN:\n{json.dumps(facts, ensure_ascii=False)}\n\nSKRIPT:\n{json.dumps(script, ensure_ascii=False)}"
    )
    res = _parse(_call("Du bist ein strenger Faktenprüfer für Sportjournalismus.", [{"role": "user", "content": prompt}], 1200))
    if res is None:
        return ["Faktencheck lieferte keine auswertbare Antwort"]
    return [str(x) for x in res.get("unsupported", [])]


# --- Umsetzung ins Videomodell + Dauermessung ---------------------------------------------------------
def _pron(speech: str, prons: dict) -> str:
    for name, spoken in (prons or {}).items():
        if name and spoken:
            speech = re.sub(rf"\b{re.escape(name)}\b", str(spoken), speech)
    return speech


def _standing_lines(st: dict) -> list[str]:
    lines = []
    for side in ("home", "away"):
        s = st[side]
        same = s["position_before"] == s["position_after"]
        if config.VIDEO_LANG == "de":
            lines.append(f"{s['name']}: bleibt auf Platz {s['position_after']} ({s['points_after']} Pkt.)" if same
                         else f"{s['name']}: Platz {s['position_before']} auf {s['position_after']} ({s['points_after']} Pkt.)")
        else:
            lines.append(f"{s['name']}: stays {s['position_after']} ({s['points_after']} pts)" if same
                         else f"{s['name']}: {s['position_before']} to {s['position_after']} ({s['points_after']} pts)")
    return lines


def apply_to_model(m: dict, d: dict, facts: dict) -> None:
    """Schreibt den Autorentext in m['script'] und m['extras']."""
    prons = d.get("pronunciations") or {}

    def sc(t):
        return {"speech": _pron(str(t["speech"]), prons), "caption": str(t["caption"])}

    script = {"hook": sc(d["hook"]), "goals": [sc(g) for g in d["goals"]], "outro": sc(d["outro"])}
    extras = []
    if d.get("context"):
        c = d["context"]
        extras.append({"must": True, "prio": 0, "order": 0.5, "side": None, "score": [0, 0],
                       "label": str(c.get("label") or "KONTEXT")[:18].upper(), "title": str(c.get("title") or "DAS SPIEL")[:26].upper(),
                       "lines": [str(x)[:44] for x in (c.get("lines") or [])[:2]], **sc(c)})
    for e in (d.get("extras") or [])[:2]:
        ex = {"must": True, "prio": 0, "side": e.get("side") if e.get("side") in ("home", "away") else None,
              "label": str(e.get("label") or "WENDEPUNKT")[:18].upper(), "title": str(e.get("title") or "")[:26].upper(),
              "lines": [str(x)[:44] for x in (e.get("lines") or [])[:2]], **sc(e)}
        if e.get("minute"):
            ex["minute"] = int(e["minute"])
        else:
            ex["order"] = 0.6
            ex["score"] = [0, 0]
        extras.append(ex)
    if d.get("standings") and facts.get("standings"):
        st = facts["standings"]
        extras.append({"must": True, "prio": 0, "order": 99.0, "side": None,
                       "label": {"de": "TABELLENFOLGE", "en": "TABLE MOVE"}.get(config.VIDEO_LANG, "TABLE"),
                       "title": {"de": "VORLÄUFIG", "en": "PROVISIONAL"}.get(config.VIDEO_LANG, ""),
                       "lines": _standing_lines(st), **sc(d["standings"])})
    m["script"] = script
    m["extras"] = extras


def estimate_duration(m: dict) -> tuple[float, dict]:
    """Echte Sprechdauer mit der Stimme messen und die Gesamtlänge wie in render.produce berechnen."""
    import render
    import script as scriptmod
    import voice

    base = render.build_scene_list(m, scriptmod.build_script(m)) + render.build_extra_scenes(m)
    total, per = 0.0, {}
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(base):
            al = voice.synth_scene(s["speech"], os.path.join(tmp, f"e{i}.wav"))
            dur = max(3.2 if s["kind"] in ("hook", "outro") else 3.0, config.SCENE_LEAD + al + config.SCENE_TAIL)
            total += dur
            per[f"{s['kind']}{i}"] = round(dur, 1)
    return total, per


# --- 4. Gesamtablauf ---------------------------------------------------------------------------
def write(m: dict, forms: dict | None = None, max_rounds: int = 3) -> dict:
    """Erzeugt den Text, prüft ihn und schreibt ihn ins Modell. Liefert Meta (Titel, Beschreibung, Hashtags, Warnungen)."""
    facts = build_facts(m, forms)
    system = system_prompt(config.VIDEO_LANG)
    messages = [{"role": "user", "content": "FAKTEN:\n" + json.dumps(facts, ensure_ascii=False)}]
    last_issue = ""
    for rnd in range(1, max_rounds + 1):
        raw = _call(system, messages)
        d = _parse(raw)
        issues = check_structure(d, facts)
        if not issues:
            issues = [f"Faktencheck: {x}" for x in fact_check(d, facts)]
        if not issues:
            apply_to_model(m, d, facts)
            total, per = estimate_duration(m)
            if config.MIN_TOTAL <= total <= config.MAX_TOTAL:
                return {"title": str(d["title"])[:100], "description": str(d.get("description", "")), "story": d.get("story", ""),
                        "hashtags": d.get("hashtags") or [], "warnings": d.get("warnings") or [], "hooks": d.get("hooks") or [],
                        "duration": round(total, 1), "rounds": rnd}
            direction = "zu kurz" if total < config.MIN_TOTAL else "zu lang"
            issues = [f"Die gemessene Videolänge beträgt {total:.0f} s und ist {direction} (Ziel {config.MIN_TOTAL:.0f}-{config.MAX_TOTAL:.0f} s). "
                      f"Kürze bzw. ergänze Sprechtext entsprechend (grob 2,5 Wörter pro Sekunde), ohne Fakten zu erfinden."]
        last_issue = "; ".join(issues)
        print(f"Autor, Runde {rnd}: {last_issue[:300]}")
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Bitte korrigiere und antworte erneut mit dem vollständigen JSON:\n- " + "\n- ".join(issues)}]
    raise RuntimeError(f"Text nach {max_rounds} Runden nicht freigabefähig: {last_issue[:200]}")
