"""Einstiegspunkt.

  python main.py --example        # Beispielvideo aus echten Spieldaten (Freiburg - Bayern, 25.10.2026)
  python main.py --run            # Einmaliger Durchlauf: neue Spiele holen, rendern (optional hochladen)
  python main.py --run --upload   # ... und zu TikTok hochladen
  python main.py --loop           # Dauerbetrieb (alle 5 Minuten prüfen)
"""
import argparse
import json
import os
import time

import config
import data_source as ds
from i18n import ui


def _pending(state: dict) -> list[dict]:
    ms = [x for x in ds.fetch_finished_matches() if x["id"] not in state["done"]
          and state["tries"].get(x["id"], 0) < config.MAX_TRIES]
    return sorted(ms, key=lambda x: x.get("utcDate", ""))


def _write_captions(entries: list[str]) -> None:
    """Neueste Captions oben in captions.md (zum Einfügen in der TikTok-App, da der Entwurfsweg keinen Titel setzt)."""
    head = "# Captions for new drafts (newest first)\n\n"
    old = ""
    if os.path.exists(config.CAPTIONS_FILE):
        with open(config.CAPTIONS_FILE, encoding="utf-8") as f:
            old = f.read().replace(head, "", 1)
    blocks = ("\n".join(entries) + "\n" + old).split("\n**")
    keep = blocks[:1] + ["**" + b for b in blocks[1:40]]  # ca. die letzten 40 Einträge
    with open(config.CAPTIONS_FILE, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(keep))


def process_new_matches(upload: bool) -> None:
    state = ds.load_state()
    matches = _pending(state)[: config.MAX_PER_RUN]
    print(f"{len(matches)} neue beendete Spiele")
    tables: dict[str, list] = {}
    captions: list[str] = []
    for m in matches:
        code = m["competition"]["code"]
        mid = m["id"]
        try:
            detail = ds.fetch_match_detail(mid)
            if code not in tables:
                tables[code] = ds.fetch_table(code) if config.LEAGUES.get(code, {}).get("table", True) else []
            model = ds.to_video_model(m, detail, tables[code])
            sh, sa = model["score"]
            if sh is None or sa is None:
                raise RuntimeError("kein Endstand in den Daten")
            if (sh + sa) > 0 and not model["goals"]:
                try:
                    import enrich

                    res = enrich.enrich(model, config.VIDEO_LANG)
                except Exception as e:  # Websuche/API-Fehler dürfen das Video nicht verhindern
                    print(f"Spiel {mid}: Ergänzung fehlgeschlagen ({e})")
                    res = None
                if res:
                    model["goals"], extra = res
                    model["extras"] = model["extras"] + extra
                    print(f"Spiel {mid}: {len(model['goals'])} Tore und {len(extra)} Extras ergänzt")
                else:
                    print(f"Spiel {mid}: keine Tordaten, es wird ein Video ohne Tore erzeugt")
            if sh + sa == 0 and os.environ.get("SKIP_GOALLESS", "1") == "1":
                print(f"Spiel {mid}: 0:0, übersprungen")
                state["done"].add(mid)
                continue
            out = os.path.join(config.OUTPUT_DIR, f"{code}_{mid}.mp4")
            import render  # erst hier: braucht Pillow/numpy, die der leichte Check-Schritt nicht installiert hat

            info = render.produce(model, out)
            print("gerendert:", out, f"({info['duration']:.1f} s)")
            title = ui(
                "post_title",
                home=model["home"]["name"], away=model["away"]["name"],
                sh=sh, sa=sa, league=model["league"], tag=model["league"].replace(" ", "").lower(),
            )
            if upload:
                import uploader

                pid = uploader.upload_video(out, title)
                status = uploader.wait_for_status(pid)
                print("hochgeladen:", pid, status)
                if status == "FAILED":
                    raise RuntimeError("TikTok meldet FAILED")
            captions.append(f"**{model['home']['name']} {sh}-{sa} {model['away']['name']}** ({model['league']})\n\n```\n{title}\n```\n")
            state["done"].add(mid)
        except Exception as e:  # ein defektes Spiel soll den Lauf nicht stoppen
            state["tries"][mid] = state["tries"].get(mid, 0) + 1
            print(f"Fehler bei Spiel {mid}: {e}")
        finally:
            ds.save_state(state)
    if captions:
        _write_captions(captions)
        summ = os.environ.get("GITHUB_STEP_SUMMARY")
        if summ:
            with open(summ, "a", encoding="utf-8") as f:
                f.write("## New drafts sent to your TikTok inbox\n\n" + "\n".join(captions))


def check_new() -> int:
    """Gibt die Zahl offener Spiele aus (für den Zeitplan: ohne neue Spiele wird nichts installiert/gerendert)."""
    n = len(_pending(ds.load_state()))
    print(f"{n} offene Spiele")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"new={n}\n")
    return n


def mark_done() -> None:
    """Alle aktuell beendeten Spiele als erledigt markieren (Startpunkt, ohne Videos zu erzeugen)."""
    state = ds.load_state()
    for x in ds.fetch_finished_matches():
        state["done"].add(x["id"])
    ds.save_state(state)
    print(f"{len(state['done'])} Spiele als erledigt markiert")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--example", "--demo", dest="example", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--check", action="store_true", help="nur zählen, wie viele neue Spiele offen sind")
    ap.add_argument("--mark-done", action="store_true", help="aktuelle Spiele als erledigt markieren (Startpunkt)")
    ap.add_argument("--lang", choices=["en", "de"], default=None, help="Sprache des Videos (Standard: en)")
    a = ap.parse_args()
    if a.lang:
        config.VIDEO_LANG = a.lang

    if a.example:
        with open(config.EXAMPLE_MATCH, encoding="utf-8") as f:
            m = json.load(f)
        out = os.path.join(config.OUTPUT_DIR, f"freiburg_bayern_{config.VIDEO_LANG}.mp4")
        import render

        info = render.produce(m, out, preview_dir=os.path.join(config.OUTPUT_DIR, "preview"))
        print("Video:", out)
        print(f"Dauer {info['duration']:.1f} s, Lautheit {info['lufs']:.1f} LUFS")
        for s in info["scenes"]:
            print("  ", s)
    elif a.check:
        check_new()
    elif a.mark_done:
        mark_done()
    elif a.run:
        process_new_matches(a.upload)
    elif a.loop:
        while True:
            process_new_matches(a.upload)
            time.sleep(300)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
