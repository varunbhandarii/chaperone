"""Render refusal scripts to mp3 with xAI TTS.

    python tts.py --text "Hola" --lang es --out /tmp/x.mp3

The demo clips are rendered by `python -m ai.render_clips` from ai/prompts/lines.<lang>.json.
"""

from __future__ import annotations

import argparse
import base64
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
LANGS = {"en": "en", "es": "es-MX", "hi": "hi"}

load_dotenv(ROOT / ".env")


def tts(text: str, lang: str, voice: str = "eve") -> bytes:
    resp = httpx.post(
        "https://api.x.ai/v1/tts",
        headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}"},
        json={
            "text": text,
            "voice_id": voice,
            "language": LANGS.get(lang, lang),
            "speed": 0.95,
            "output_format": {"codec": "mp3", "sample_rate": 44100, "bit_rate": 128000},
        },
        timeout=60,
    )
    resp.raise_for_status()
    if resp.headers.get("content-type", "").startswith("application/json"):
        body = resp.json()
        return base64.b64decode(body.get("audio") or body["data"])
    return resp.content


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--out")
    args = ap.parse_args()
    if not args.text:
        raise SystemExit("pass --text; demo clips come from `python -m ai.render_clips`")
    jobs = [(args.text, args.lang, Path(args.out or f"tts.{args.lang}.mp3"))]
    for text, lang, out in jobs:
        start = time.perf_counter()
        audio = tts(text, lang)
        out.write_bytes(audio)
        print(f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}: "
              f"{len(audio) / 1024:.0f} KB in {(time.perf_counter() - start) * 1000:.0f} ms")
