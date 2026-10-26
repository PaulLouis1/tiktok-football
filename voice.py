"""Sprecher: Offline-TTS (deutsche Stimme), Mastering und Audiomix mit Stadionkulisse."""
import os
import re
import subprocess

import config


def _env():
    e = os.environ.copy()
    e["LD_LIBRARY_PATH"] = config.TTS_LIB + ":" + e.get("LD_LIBRARY_PATH", "")
    return e


def _ff(args, **kw):
    return subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, **kw)


def duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def lufs(path: str) -> float:
    """Integrierte Lautheit (EBU R128)."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    vals = re.findall(r"\bI:\s+(-?\d+(?:\.\d+)?)\s+LUFS", r.stderr)
    return float(vals[-1]) if vals else -70.0


def synth(text: str, out_wav: str) -> None:
    from i18n import lang

    if lang() == "en":
        e = config.TTS_EN_DIR
        cmd = [
            config.TTS_BIN,
            f"--kokoro-model={e}/model.onnx",
            f"--kokoro-voices={e}/voices.bin",
            f"--kokoro-tokens={e}/tokens.txt",
            f"--kokoro-data-dir={e}/espeak-ng-data",
            f"--kokoro-length-scale={config.TTS_EN_LENGTH_SCALE}",
            "--num-threads=2",
            f"--sid={config.TTS_EN_SID}",
            f"--output-filename={out_wav}",
            text,
        ]
        subprocess.run(cmd, env=_env(), check=True, capture_output=True)
        return
    m = config.TTS_MODEL_DIR
    cmd = [
        config.TTS_BIN,
        f"--vits-model={m}/de_DE-thorsten-medium.onnx",
        f"--vits-tokens={m}/tokens.txt",
        f"--vits-data-dir={m}/espeak-ng-data",
        f"--vits-length-scale={config.TTS_LENGTH_SCALE}",
        "--vits-noise-scale=0.7",
        "--vits-noise-scale-w=0.85",
        "--num-threads=2",
        "--sid=0",
        f"--output-filename={out_wav}",
        text,
    ]
    subprocess.run(cmd, env=_env(), check=True, capture_output=True)


def polish(raw_wav: str, out_wav: str) -> None:
    """Leichte Klangformung: Rumpeln raus, Präsenz rein, Dynamik verdichten."""
    af = (
        "highpass=f=90,"
        "equalizer=f=200:t=q:w=1:g=1.5,"
        "equalizer=f=3200:t=q:w=1.1:g=3,"
        "equalizer=f=7500:t=q:w=1:g=-2,"
        "acompressor=threshold=-21dB:ratio=3.2:attack=6:release=80:makeup=4,"
        "aresample=44100,aformat=channel_layouts=mono"
    )
    _ff(["-i", raw_wav, "-af", af, out_wav])


def synth_scene(text: str, out_wav: str) -> float:
    raw = out_wav + ".raw.wav"
    synth(text, raw)
    polish(raw, out_wav)
    os.remove(raw)
    return duration(out_wav)


def mix_audio(scenes: list[dict], total: float, work: str) -> str:
    """Setzt die Szenen-Sprache zeitlich zusammen, legt Kulisse + Impacts darunter, normalisiert."""
    # 1) Sprachspur: jede Szene verzögert und auf Szenenlänge aufgefüllt, dann verkettet
    args, filt, labels = [], [], []
    for i, sc in enumerate(scenes):
        args += ["-i", sc["wav"]]
        lead_ms = int(config.SCENE_LEAD * 1000)
        filt.append(f"[{i}:a]adelay={lead_ms}:all=1,apad=whole_dur={sc['dur']:.3f},atrim=0:{sc['dur']:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[a{i}]")
    filt.append("".join(labels) + f"concat=n={len(scenes)}:v=0:a=1[v]")
    voice_wav = os.path.join(work, "voice_track.wav")
    _ff([*args, "-filter_complex", ";".join(filt), "-map", "[v]", "-ar", "44100", "-ac", "1", voice_wav])
    final = os.path.join(work, "mix.wav")

    if not config.AMBIENCE:
        _ff(["-i", voice_wav, "-af", f"loudnorm=I={config.TARGET_LUFS}:TP=-1.5:LRA=9,aformat=channel_layouts=stereo", "-ar", "44100", final])
        return final

    # 2) Stadionkulisse: rosa Rauschen, bandbegrenzt, schwillt bei Toren an
    goal_windows = [(sc["start"], sc["start"] + sc["dur"]) for sc in scenes if sc["kind"] == "goal"]
    swell = "+".join(f"clip((t-{s:.2f})/0.25,0,1)*clip(({e:.2f}-t)/0.6,0,1)" for s, e in goal_windows) or "0"
    amb_raw = os.path.join(work, "amb_raw.wav")
    amb_filter = (
        f"anoisesrc=color=pink:amplitude=0.6:sample_rate=44100:duration={total:.3f},"
        "highpass=f=200,lowpass=f=2600,tremolo=f=0.32:d=0.45,"
        f"volume='1+2.4*({swell})':eval=frame,aformat=channel_layouts=mono"
    )
    _ff(["-f", "lavfi", "-i", amb_filter, amb_raw])

    # 3) Impact-Sounds an jedem Szenenwechsel (Bass-Hit + kurzer Klick), stärker bei Toren
    terms = []
    for sc in scenes:
        s = sc["start"]
        a = 0.55 if sc["kind"] == "goal" else 0.32
        terms.append(
            f"if(gte(t,{s:.3f}),{a}*(sin(2*PI*62*(t-{s:.3f}))+0.5*sin(2*PI*124*(t-{s:.3f})))*exp(-9*(t-{s:.3f}))"
            f"+0.12*(random(0)*2-1)*exp(-45*(t-{s:.3f})),0)"
        )
    hits_wav = os.path.join(work, "hits.wav")
    _ff(["-f", "lavfi", "-i", f"aevalsrc='{'+'.join(terms)}':s=44100:d={total:.3f}", hits_wav])

    # 4) Pegel: Kulisse ~20 LU unter der Stimme (bei Toren schwillt sie an), Impacts als Akzent
    v_l, a_l = lufs(voice_wav), lufs(amb_raw)
    amb_gain = v_l - 20.0 - a_l
    _ff([
        "-i", voice_wav, "-i", amb_raw, "-i", hits_wav,
        "-filter_complex",
        f"[0:a]volume=1.0[v];[1:a]volume={amb_gain:.2f}dB[a];[2:a]volume=-9dB[h];"
        f"[v][a][h]amix=inputs=3:normalize=0:duration=first,"
        f"loudnorm=I={config.TARGET_LUFS}:TP=-1.5:LRA=9,aformat=channel_layouts=stereo",
        "-ar", "44100", final,
    ])
    return final
