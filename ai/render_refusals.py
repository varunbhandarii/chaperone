"""Render refusal scripts to mp3 with xAI TTS, in the station's voice.

    python -m ai.render_refusals                      # every ai/prompts/refusal.<key>.<lang>.txt
    python -m ai.render_refusals --voice es=luna --only blocked_category

Output: ai/warnings/refusal.<key>.<lang>.mp3, served by the relay at /audio/refusal.<key>.<lang>.mp3.
VOICES matches the station agent's voice per language so the clip sounds like
the agent that was just talking. The Hindi scripts use feminine verb forms for the agent;
switching Hindi to a male voice (naksh) needs those forms changed too.
"""

from __future__ import annotations

import argparse
import base64
import time
from pathlib import Path

import httpx

from common.config import env

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "ai" / "prompts"
WARNINGS = ROOT / "ai" / "warnings"
TTS_LANG = {"en": "en", "es": "es-MX", "hi": "hi"}
VOICES = {"en": "ara", "es": "carina", "hi": "ara"}


def available_voices() -> set[str]:
    resp = httpx.get("https://api.x.ai/v1/tts/voices",
                     headers={"Authorization": f"Bearer {env('XAI_API_KEY')}"}, timeout=15)
    resp.raise_for_status()
    return {v["voice_id"] for v in resp.json()["voices"]}


def tts(text: str, lang: str, voice: str, speed: float = 0.9) -> bytes:
    resp = httpx.post(
        "https://api.x.ai/v1/tts",
        headers={"Authorization": f"Bearer {env('XAI_API_KEY')}"},
        json={
            "text": text,
            "voice_id": voice,
            "language": TTS_LANG[lang],
            "speed": speed,
            "output_format": {"codec": "mp3", "sample_rate": 44100, "bit_rate": 128000},
        },
        timeout=60,
    )
    resp.raise_for_status()
    if resp.headers.get("content-type", "").startswith("application/json"):
        body = resp.json()
        return base64.b64decode(body.get("audio") or body["data"])
    return resp.content


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", action="append", default=[], metavar="LANG=VOICE",
                    help="override one language's voice, e.g. --voice es=luna (repeatable)")
    ap.add_argument("--speed", type=float, default=0.9)
    ap.add_argument("--only", help="render one spoken_key")
    args = ap.parse_args()

    voices = dict(VOICES)
    for override in args.voice:
        lang, _, voice = override.partition("=")
        if lang not in voices or not voice:
            raise SystemExit(f"--voice expects LANG=VOICE with LANG in {sorted(voices)}: {override!r}")
        voices[lang] = voice
    missing = set(voices.values()) - available_voices()
    if missing:
        raise SystemExit(f"unknown voice(s): {sorted(missing)}; see GET /v1/tts/voices")

    WARNINGS.mkdir(parents=True, exist_ok=True)
    for src in sorted(PROMPTS.glob("refusal.*.*.txt")):
        _, key, lang, _ = src.name.split(".")
        if args.only and key != args.only:
            continue
        start = time.perf_counter()
        audio = tts(src.read_text(encoding="utf-8").strip(), lang, voices[lang], args.speed)
        out = WARNINGS / f"refusal.{key}.{lang}.mp3"
        out.write_bytes(audio)
        print(f"{out.relative_to(ROOT)}  voice={voices[lang]}  {len(audio) // 1024} KB  "
              f"{(time.perf_counter() - start) * 1000:.0f} ms")


if __name__ == "__main__":
    main()
