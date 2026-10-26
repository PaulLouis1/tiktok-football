#!/usr/bin/env bash
# Lädt die Offline-Sprachausgabe (sherpa-onnx + deutsche Piper-Stimme "Thorsten") von GitHub.
# Läuft auf Linux x86_64. Für andere Systeme das passende Release unter
# https://github.com/k2-fsa/sherpa-onnx/releases wählen.
set -euo pipefail
cd "$(dirname "$0")"
LANGS="${LANGS:-en de}"   # z. B. LANGS=en für nur Englisch (kleiner, schneller)
mkdir -p tts/runtime tts/model
cd tts
V=v1.12.20
curl -L --fail -o rt.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/$V/sherpa-onnx-$V-linux-x64-shared.tar.bz2"
tar xjf rt.tar.bz2 -C runtime --strip-components=1
rm rt.tar.bz2
if [[ " $LANGS " == *" de "* ]]; then
  # Deutsche Stimme (Piper "Thorsten", CC0)
  curl -L --fail -o model.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-de_DE-thorsten-medium.tar.bz2"
  tar xjf model.tar.bz2 -C model --strip-components=1
  rm model.tar.bz2
fi
if [[ " $LANGS " == *" en "* ]]; then
  # Englische Stimme (Kokoro, Apache-2.0)
  mkdir -p en
  curl -L --fail -o kokoro.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-en-v0_19.tar.bz2"
  tar xjf kokoro.tar.bz2 -C en --strip-components=1
  rm kokoro.tar.bz2
fi
echo "TTS bereit ($LANGS)."
