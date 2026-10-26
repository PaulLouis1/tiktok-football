"""Sprachumschaltung (de/en): feste UI-Texte und Helfer für übersetzbare Felder im Spiel-JSON.

Felder im JSON dürfen entweder ein normaler Wert oder ein Dict {"de": ..., "en": ...} sein.
"""
import config

UI = {
    "de": {
        "full_time": "ABPFIFF",
        "matchday": "{league} · {n}. SPIELTAG",
        "goal": "TOR!",
        "own_goal": "EIGENTOR!",
        "goal_for": "Tor für {team}",
        "stats_title": "STATISTIK",
        "stat_possession": "Ballbesitz %",
        "stat_shots": "Torschüsse",
        "stat_on_target": "Aufs Tor",
        "stat_corners": "Ecken",
        "table_title": "TABELLE",
        "pts": "{n} Pkt",
        "next_match": "NÄCHSTES TOPSPIEL",
        "who_wins": "Wer gewinnt?",
        "comment_cta": "Schreib's in die Kommentare!",
        "post_title": "{home} {sh}:{sa} {away} | {league} Highlights #fussball #{tag}",
    },
    "en": {
        "full_time": "FULL TIME",
        "matchday": "{league} · MATCHDAY {n}",
        "goal": "GOAL!",
        "own_goal": "OWN GOAL!",
        "goal_for": "Goal for {team}",
        "stats_title": "STATS",
        "stat_possession": "Possession %",
        "stat_shots": "Shots",
        "stat_on_target": "On target",
        "stat_corners": "Corners",
        "table_title": "TABLE",
        "pts": "{n} pts",
        "next_match": "NEXT BIG MATCH",
        "who_wins": "Who wins?",
        "comment_cta": "Drop your pick in the comments!",
        "post_title": "{home} {sh}-{sa} {away} | {league} highlights #football #{tag}",
    },
}


def lang() -> str:
    return config.VIDEO_LANG if config.VIDEO_LANG in UI else "en"


def ui(key: str, **kw) -> str:
    s = UI[lang()][key]
    return s.format(**kw) if kw else s


def tr(v):
    """Wert für die aktuelle Sprache; Dicts werden aufgelöst, alles andere bleibt unverändert."""
    if isinstance(v, dict):
        return v.get(lang()) or v.get("en") or next(iter(v.values()))
    return v


def team_name(m: dict, side: str) -> str:
    t = m[side]
    return t.get("name_" + lang()) or t["name"]


def stat_label(key: str) -> str:
    k = "stat_" + key
    return UI[lang()].get(k, key)
