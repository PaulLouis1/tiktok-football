"""Spieldaten: football-data.org-Client und Umwandlung ins Videomodell."""
import json
import os
import time
from datetime import date, timedelta

import requests

import config

_last_call = 0.0


def _get(path: str, params: dict | None = None) -> dict:
    """GET mit simplem Rate-Limiting für das Free-Tier (10 Calls/Minute)."""
    global _last_call
    wait = config.MIN_SECONDS_BETWEEN_CALLS - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(
        f"{config.API_BASE}{path}",
        headers={"X-Auth-Token": config.API_TOKEN},
        params=params,
        timeout=20,
    )
    _last_call = time.time()
    if r.status_code >= 400:
        raise RuntimeError(f"football-data.org antwortete {r.status_code}: {r.text[:200]}")
    return r.json()


def fetch_finished_matches(days_back: int = 1) -> list[dict]:
    """Alle beendeten Spiele der fünf Ligen der letzten Tage (ein einziger Call)."""
    today = date.today()
    data = _get(
        "/matches",
        {
            "competitions": ",".join(config.LEAGUES.keys()),
            "dateFrom": (today - timedelta(days=days_back)).isoformat(),
            "dateTo": (today + timedelta(days=1)).isoformat(),
            "status": "FINISHED",
        },
    )
    return data.get("matches", [])


def fetch_match_detail(match_id: int) -> dict:
    """Detail inkl. Torfolge (falls im Tarif enthalten)."""
    return _get(f"/matches/{match_id}")


def fetch_table(competition_code: str) -> list[dict]:
    data = _get(f"/competitions/{competition_code}/standings")
    for st in data.get("standings", []):
        if st.get("type") == "TOTAL":
            return st["table"]
    return []


def _last_name(person: dict | None) -> str:
    name = (person or {}).get("name") or ""
    return name.split()[-1] if name else ""


def to_video_model(match: dict, detail: dict | None, table: list[dict] | None) -> dict:
    """Wandelt API-Antworten in das Modell um, das der Renderer nutzt (siehe examples/*.json)."""
    d = detail or match
    home, away = match["homeTeam"], match["awayTeam"]

    def team(t: dict) -> dict:
        short = t.get("shortName") or t["name"]
        return {"name": t["name"], "short": short, "tla": t.get("tla") or short[:3].upper()}

    goals = []
    for g in d.get("goals") or []:
        # Achtung: Bei Eigentoren sollte geprüft werden, welcher Mannschaft die API das Tor zuordnet.
        side = "home" if (g.get("team") or {}).get("id") == home["id"] else "away"
        scorer = (g.get("scorer") or {}).get("name") or "Unbekannt"
        chain = [n for n in (_last_name(g.get("assist")), _last_name(g.get("scorer"))) if n]
        goals.append(
            {
                "minute": g.get("minute"),
                "injury": g.get("injuryTime"),
                "side": side,
                "type": g.get("type") or "REGULAR",
                "scorer": scorer,
                "short": (scorer.split()[-1] if scorer != "Unbekannt" else scorer).upper(),
                "chain": chain if len(chain) > 1 else [],
                "tag": "",
            }
        )

    code0 = match["competition"]["code"]
    if not config.LEAGUES.get(code0, {}).get("table", True):
        table = []  # z. B. Champions League: keine Tabellenszene
    rows = []
    for row in (table or [])[:5]:
        tid = row["team"]["id"]
        rows.append(
            {
                "pos": row["position"],
                "name": row["team"].get("shortName") or row["team"]["name"],
                "pts": row["points"],
                "hl": 1 if tid in (home["id"], away["id"]) else 0,
            }
        )

    ft = match["score"]["fullTime"]
    code = match["competition"]["code"]
    _fix_own_goal_sides(goals, ft["home"], ft["away"])
    m = {
        "league_code": code,
        "league": config.LEAGUES.get(code, {}).get("name", match["competition"]["name"]),
        "matchday": match.get("matchday"),
        "home": team(home),
        "away": team(away),
        "score": [ft["home"], ft["away"]],
        "goals": goals,
        "stats": None,  # Free-Tier liefert keine Spielstatistik -> zweite Quelle ergänzen
        "table": rows,
        "next": None,
    }
    m["extras"] = auto_extras(m, match, table or [])
    return m


def _fix_own_goal_sides(goals: list[dict], sh: int, sa: int) -> None:
    """Eigentore: Zählt die API das Tor dem Team des Schützen statt dem Begünstigten, passen die Tore nicht zum Endstand.
    Dann werden die Eigentore umgedreht (nur wenn danach alles stimmt)."""
    def counts():
        return sum(g["side"] == "home" for g in goals), sum(g["side"] == "away" for g in goals)

    if sh is None or sa is None or counts() == (sh, sa):
        return
    own = [g for g in goals if g["type"] == "OWN"]
    if not own:
        return
    for g in own:
        g["side"] = "away" if g["side"] == "home" else "home"
    if counts() != (sh, sa):  # hat nicht geholfen: zurückdrehen
        for g in own:
            g["side"] = "away" if g["side"] == "home" else "home"


_ORD_EN = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh",
           "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth",
           "twentieth"]
_ORD_EN_S = lambda n: f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def auto_extras(m: dict, match: dict, full_table: list[dict]) -> list[dict]:
    """Zusatzszenen, die sich allein aus den Free-Tier-Daten bauen lassen (Halbzeit, Tabellenplätze)."""
    from script import en_num, de_num

    ex = []
    h, a = m["home"]["short"], m["away"]["short"]
    ht = ((match.get("score") or {}).get("halfTime")) or {}
    if ht.get("home") is not None and ht.get("away") is not None:
        hh, ha = ht["home"], ht["away"]
        lead = "home" if hh > ha else "away" if ha > hh else None
        if hh == ha:
            en = f"At half-time it was level: {h} {en_num(hh)}, {a} {en_num(ha)}."
            de = f"Zur Pause stand es unentschieden: {h} {de_num(hh)}, {a} {de_num(ha)}."
            lines_en = ["All square at the break"]
            lines_de = ["Gleichstand zur Pause"]
        else:
            ln = h if lead == "home" else a
            en = f"At half-time, {ln} led {en_num(max(hh, ha))} to {en_num(min(hh, ha))}."
            de = f"Zur Pause führte {ln} mit {de_num(max(hh, ha))} zu {de_num(min(hh, ha))}."
            lines_en = [f"{ln} ahead at the break"]
            lines_de = [f"{ln} führt zur Pause"]
        ex.append({
            "prio": 1, "order": 45.5, "after_minute": 45, "score": [hh, ha], "side": lead,
            "label": {"en": "HALF-TIME", "de": "HALBZEIT"},
            "title": f"{hh}:{ha}",
            "lines": {"en": lines_en, "de": lines_de},
            "speech": {"en": en, "de": de}, "caption": {"en": en, "de": de},
        })
    pos = {}
    for row in full_table:
        pos[row["team"]["id"]] = (row["position"], row["points"])
    ids = (match["homeTeam"]["id"], match["awayTeam"]["id"])
    if all(i in pos for i in ids) and max(pos[ids[0]][0], pos[ids[1]][0]) <= 20:
        (ph, pth), (pa, pta) = pos[ids[0]], pos[ids[1]]
        en = (f"In the table, {h} are {_ORD_EN[ph]} on {en_num(pth)} points, "
              f"{a} are {_ORD_EN[pa]} on {en_num(pta)}.")
        de = (f"In der Tabelle steht {h} auf Platz {de_num(ph)} mit {de_num(pth)} Punkten, "
              f"{a} auf Platz {de_num(pa)} mit {de_num(pta)} Punkten.")
        ex.append({
            "prio": 2, "order": 99.0,
            "label": {"en": "STANDINGS", "de": "TABELLE"},
            "title": {"en": "WHERE THEY STAND", "de": "SO STEHEN SIE"},
            "lines": {"en": [f"{h}: {_ORD_EN_S(ph)}, {pth} pts", f"{a}: {_ORD_EN_S(pa)}, {pta} pts"],
                      "de": [f"{h}: Platz {ph}, {pth} Pkt.", f"{a}: Platz {pa}, {pta} Pkt."]},
            "speech": {"en": en, "de": de}, "caption": {"en": en, "de": de},
        })
    return ex


def load_example() -> dict:
    with open(config.EXAMPLE_MATCH, encoding="utf-8") as f:
        return json.load(f)


# --- Zustand: welche Spiele wurden schon verarbeitet? -------------------------
def load_state() -> dict:
    """{"done": [Match-IDs], "tries": {Match-ID: Versuche}} (altes Listenformat wird übernommen)."""
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE) as f:
            raw = json.load(f)
        if isinstance(raw, list):
            raw = {"done": raw}
        return {"done": set(raw.get("done", [])), "tries": {int(k): v for k, v in raw.get("tries", {}).items()}}
    return {"done": set(), "tries": {}}


def save_state(state: dict) -> None:
    with open(config.STATE_FILE, "w") as f:
        json.dump({"done": sorted(state["done"]), "tries": {str(k): v for k, v in sorted(state["tries"].items())}}, f)
