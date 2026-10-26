# TikTok football recap pipeline

Turns finished matches of the top-5 leagues (Bundesliga, Premier League, La Liga, Serie A, Ligue 1) into ~40-70 s vertical
recap videos (generated graphics, subtitles, synthetic commentary voice, no licensed footage) and sends them to the
owner's TikTok inbox as drafts. The owner reviews and publishes them in the TikTok app.

## Pieces
- `main.py`: `--example` (demo video), `--check`, `--mark-done`, `--run [--upload]`, `--loop`, `--lang en|de`
- `data_source.py`: football-data.org v4 (free tier) -> video model, plus automatic extra scenes (half-time, standings)
- `script.py` / `render.py` / `voice.py` / `background.py` / `i18n.py`: commentary, frames, offline TTS, audio mix, stadium background
- `uploader.py`: TikTok Content Posting API, inbox/draft upload (`TIKTOK_MODE=draft`, default) or direct post
- `.github/workflows/recap.yml`: schedule (every 30 min): check -> render -> upload -> save `state.json` and `captions.md`
- Cloudflare worker (in the website repo): holds the TikTok client secret, hands out fresh access tokens to this job

## GitHub secrets (Settings > Secrets and variables > Actions)
| Secret | Value |
|---|---|
| `FOOTBALL_DATA_TOKEN` | API key from football-data.org |
| `WORKER_URL` | e.g. `https://tiktok-api.<name>.workers.dev` |
| `BOT_KEY` | the same random password as the worker's `BOT_KEY` secret |

## First start
Run the workflow manually (Actions > recap > Run workflow) with **init** ticked. This only marks the matches that are
already finished as done. After that the schedule produces videos for new matches only.

## Captions
The inbox upload cannot set a caption. The caption of each new draft is written to `captions.md` (newest first) and
shown in the run summary. Paste it when you publish.

## Local
`pip install -r requirements.txt`, FFmpeg, `bash setup_tts.sh`, then `python main.py --example`.
Fonts: Poppins (SIL Open Font License) in `fonts/`.
