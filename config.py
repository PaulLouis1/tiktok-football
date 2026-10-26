"""Zentrale Konfiguration."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- football-data.org -------------------------------------------------------
API_BASE = "https://api.football-data.org/v4"
API_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN", "")  # Header: X-Auth-Token

# Free-Tier: 10 Requests pro Minute -> Abstand zwischen Calls
MIN_SECONDS_BETWEEN_CALLS = 6.5

# Die fünf Ligen (Codes bei football-data.org)
LEAGUES = {
    "BL1": {"name": "Bundesliga"},
    "PL": {"name": "Premier League"},
    "PD": {"name": "La Liga"},
    "SA": {"name": "Serie A"},
    "FL1": {"name": "Ligue 1"},
    "CL": {"name": "Champions League", "table": False},  # Ligaphase-Tabelle zu groß, K.-o.-Runde ohne Tabelle
}
# Weitere Wettbewerbe per Umgebungsvariable, z. B. EXTRA_COMPETITIONS="EL" (nur wenn der Tarif sie enthält)
for _code in filter(None, os.environ.get("EXTRA_COMPETITIONS", "").replace(" ", "").split(",")):
    LEAGUES.setdefault(_code, {"name": {"EL": "Europa League", "ECL": "Conference League"}.get(_code, _code), "table": False})

# --- Sprache -------------------------------------------------------------------
VIDEO_LANG = os.environ.get("VIDEO_LANG", "en")  # "en" oder "de"

# --- Video -------------------------------------------------------------------
WIDTH, HEIGHT = 1080, 1920
FPS = 30
_FONT_DIR = os.path.join(BASE_DIR, "fonts")  # Poppins (SIL Open Font License), liegt im Repo
FONTS = {
    "bold": os.path.join(_FONT_DIR, "Poppins-Bold.ttf"),
    "med": os.path.join(_FONT_DIR, "Poppins-Medium.ttf"),
    "italic": os.path.join(_FONT_DIR, "Poppins-BoldItalic.ttf"),
}
RENDER_PROCESSES = int(os.environ.get("RENDER_PROCESSES", "2"))

# Standardfarben, falls ein Verein keine eigene Farbe mitbringt
DEFAULT_HOME_COLOR = (255, 255, 255)
DEFAULT_AWAY_COLOR = (255, 196, 0)

# --- Sprecher (Offline-TTS, siehe setup_tts.sh) ---------------------------------
TTS_DIR = os.environ.get("TTS_DIR", os.path.join(BASE_DIR, "tts"))
TTS_BIN = os.path.join(TTS_DIR, "runtime", "bin", "sherpa-onnx-offline-tts")
TTS_LIB = os.path.join(TTS_DIR, "runtime", "lib")
TTS_MODEL_DIR = os.path.join(TTS_DIR, "model")
TTS_LENGTH_SCALE = float(os.environ.get("TTS_LENGTH_SCALE", "0.88"))  # <1 = schneller (Deutsch)
# Englisch: Kokoro (Apache-2.0). Sprecher-IDs v0_19: 0 af, 1 af_bella, 2 af_nicole, 3 af_sarah, 4 af_sky,
# 5 am_adam, 6 am_michael, 7 bf_emma, 8 bf_isabella, 9 bm_george, 10 bm_lewis
TTS_EN_DIR = os.path.join(TTS_DIR, "en")
TTS_EN_SID = int(os.environ.get("EN_VOICE_SID", "6"))
TTS_EN_LENGTH_SCALE = float(os.environ.get("EN_LENGTH_SCALE", "0.9"))
SCENE_TAIL = 0.35          # Pause nach dem letzten Wort einer Szene (s)
SCENE_LEAD = 0.12          # Pause vor dem ersten Wort (s)
AMBIENCE = os.environ.get("AMBIENCE", "1") == "1"  # leise Stadionkulisse + Impact-Sounds
TARGET_LUFS = -14.0        # TikTok-typische Lautheit
# Zielspanne der Videolänge: knapp über eine Minute (Zusatzszenen werden nach Priorität ergänzt)
MIN_TOTAL = float(os.environ.get("MIN_TOTAL", "61"))
MAX_TOTAL = float(os.environ.get("MAX_TOTAL", "70"))

# --- Pfade -------------------------------------------------------------------
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
STATE_FILE = os.path.join(BASE_DIR, "state.json")  # bereits verarbeitete Match-IDs
EXAMPLE_MATCH = os.path.join(BASE_DIR, "examples", "freiburg_bayern_2026-10-25.json")

# --- Tore/Zusatzszenen per Sprachmodell (optional; ohne Key: Videos ohne Tore) ---------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ENRICH_MODEL = os.environ.get("ENRICH_MODEL", "claude-sonnet-5-5")

# --- Zeitplan-Betrieb ------------------------------------------------------------
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "12"))   # höchstens so viele Videos pro Lauf
MAX_TRIES = int(os.environ.get("MAX_TRIES", "6"))        # Spiele ohne Tordaten so oft erneut versuchen
CAPTIONS_FILE = os.path.join(BASE_DIR, "captions.md")
WORKER_URL = os.environ.get("WORKER_URL", "").rstrip("/")  # Cloudflare-Worker (liefert frische Tokens)
BOT_KEY = os.environ.get("BOT_KEY", "")

# --- TikTok ------------------------------------------------------------------
TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
# Bis zum bestandenen Audit erzwingt TikTok SELF_ONLY (nur für dich sichtbar)
# "draft" = Entwurf im TikTok-Postfach (Standard, kein Audit nötig), "direct" = direkt veröffentlichen
TIKTOK_MODE = os.environ.get("TIKTOK_MODE", "draft")
TIKTOK_PRIVACY_LEVEL = os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")
