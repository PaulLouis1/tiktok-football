"""TikTok Content Posting API – Gerüst. Zwei Modi (config.TIKTOK_MODE):

  draft  (Standard) Video landet als Entwurf im TikTok-Postfach, Veröffentlichen per Tipp in der App.
                    Scope `video.upload`, kein Audit nötig. Titel/Hashtags können hier NICHT per API
                    gesetzt werden -> sie werden als .txt neben das Video gelegt (zum Einfügen).
  direct            Direkt veröffentlichen (Scope `video.publish`, Audit nötig, sonst nur SELF_ONLY).

Ablauf Direct Post laut TikTok-Doku:

Ablauf laut TikTok-Doku:
1. creator_info/query  -> erlaubte Privacy-Level, max. Videolänge des Creators
2. video/init          -> publish_id + upload_url
3. PUT auf upload_url  -> Videodatei (in Chunks)
4. status/fetch        -> Status prüfen

Wichtig:
- Scope `video.publish`, App braucht "Content Posting API" + "Direct Post".
- Ohne bestandenes Audit sind alle Posts SELF_ONLY (nur du siehst sie).
- Der Schritt creator_info darf nicht übersprungen werden (Audit-Kriterium).
"""
import os
import time

import requests

import config

BASE = "https://open.tiktokapis.com/v2/post/publish"
INBOX = f"{BASE}/inbox/video/init/"
CHUNK = 10 * 1024 * 1024  # 10 MB


_token_cache: str = ""


def get_access_token() -> str:
    """Fester Token aus der Umgebung (Tests) oder frischer Token vom Cloudflare-Worker (Zeitplanbetrieb)."""
    global _token_cache
    if config.TIKTOK_ACCESS_TOKEN:
        return config.TIKTOK_ACCESS_TOKEN
    if _token_cache:
        return _token_cache
    if not (config.WORKER_URL and config.BOT_KEY):
        raise RuntimeError("Weder TIKTOK_ACCESS_TOKEN noch WORKER_URL + BOT_KEY gesetzt")
    r = requests.post(f"{config.WORKER_URL}/api/bot/token", headers={"X-Bot-Key": config.BOT_KEY}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Token vom Worker nicht erhalten ({r.status_code}): {r.text[:200]}")
    _token_cache = r.json()["access_token"]
    return _token_cache


def _headers():
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def query_creator_info() -> dict:
    r = requests.post(f"{BASE}/creator_info/query/", headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()["data"]


def _source_info(path: str) -> dict:
    size = os.path.getsize(path)
    chunk = min(size, CHUNK)
    return {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": chunk,
        "total_chunk_count": max(1, size // chunk),  # Rest wird an den letzten Chunk gehängt
    }


def upload_draft(path: str, title: str) -> str:
    """Lädt das Video in den TikTok-Posteingang (Entwurf). Titel wird als .txt neben die Datei gelegt."""
    with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
        f.write(title)
    si = _source_info(path)
    r = requests.post(INBOX, headers=_headers(), json={"source_info": si}, timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    _put_chunks(path, data["upload_url"], si)
    return data["publish_id"]


def upload_video(path: str, title: str) -> str:
    if config.TIKTOK_MODE == "draft":
        return upload_draft(path, title)
    info = query_creator_info()
    privacy = config.TIKTOK_PRIVACY_LEVEL
    if privacy not in info.get("privacy_level_options", []):
        privacy = info["privacy_level_options"][0]

    si = _source_info(path)
    size, chunk, total_chunks = si["video_size"], si["chunk_size"], si["total_chunk_count"]
    body = {
        "post_info": {
            "title": title[:2200],
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": True,
            "disable_stitch": True,
            # KI-Kennzeichnung (synthetische Stimme). Feldname laut Drittanbieter-Doku,
            # bitte gegen die offizielle TikTok-Doku prüfen; bei Ablehnung TIKTOK_AIGC=0 setzen.
            **({"is_aigc": True} if os.environ.get("TIKTOK_AIGC", "1") == "1" else {}),
        },
        "source_info": si,
    }
    r = requests.post(f"{BASE}/video/init/", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    _put_chunks(path, data["upload_url"], si)
    return data["publish_id"]


def _put_chunks(path: str, upload_url: str, si: dict) -> None:
    size, chunk, total_chunks = si["video_size"], si["chunk_size"], si["total_chunk_count"]
    with open(path, "rb") as f:
        for i in range(total_chunks):
            start = i * chunk
            end = size - 1 if i == total_chunks - 1 else start + chunk - 1
            f.seek(start)
            part = f.read(end - start + 1)
            requests.put(
                upload_url,
                data=part,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(len(part)),
                },
                timeout=120,
            ).raise_for_status()


def wait_for_status(publish_id: str, timeout_s: int = 180) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = requests.post(
            f"{BASE}/status/fetch/", headers=_headers(), json={"publish_id": publish_id}, timeout=20
        )
        r.raise_for_status()
        status = r.json()["data"]["status"]
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"):
            return status
        time.sleep(5)
    return "TIMEOUT"
