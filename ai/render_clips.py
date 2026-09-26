"""Render spoken-line clips to mp3 with xAI TTS, in the station's voice.

    python -m ai.render_clips                         # every clip in CLIPS, all languages
    python -m ai.render_clips --voice es=luna --only blocked_category

Texts come from ai/prompts/lines.<lang>.json, the single source the station and /screen read.
Output goes to ai/warnings/, served by the relay at /audio/<file>:
    refusal.<key>.<lang>.mp3   blocked_category, scam_pattern, code_reading
    line.<key>.<lang>.mp3      asking_priya, receipt_done (from receipt_done_clip: no amount)
VOICES matches the station agent's voice per language so the clip sounds like
the agent that was just talking. The Hindi lines use feminine verb forms for the agent;
switching Hindi to a male voice (naksh) needs those forms changed too.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import httpx

from common.config import env

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "ai" / "prompts"
WARNINGS = ROOT / "ai" / "warnings"
TTS_LANG = {"en": "en", "es": "es-MX", "hi": "hi"}
VOICES = {"en": "ara", "es": "carina", "hi": "ara"}
# output file stem -> key in lines.<lang>.json
CLIPS = {
    "refusal.blocked_category": "blocked_category",
    "refusal.scam_pattern": "scam_pattern",
    "refusal.code_reading": "code_reading",
    "line.asking_priya": "asking_priya",
    "line.receipt_done": "receipt_done_clip",
}


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
    ap.add_argument("--only", help="render one line key, e.g. blocked_category or asking_priya")
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
    for lang in TTS_LANG:
        lines = json.loads((PROMPTS / f"lines.{lang}.json").read_text(encoding="utf-8"))
        for stem, key in CLIPS.items():
            if args.only and args.only not in (key, stem.split(".", 1)[1]):
                continue
            start = time.perf_counter()
            audio = tts(lines[key], lang, voices[lang], args.speed)
            out = WARNINGS / f"{stem}.{lang}.mp3"
            out.write_bytes(audio)
            print(f"{out.relative_to(ROOT)}  voice={voices[lang]}  {len(audio) // 1024} KB  "
                  f"{(time.perf_counter() - start) * 1000:.0f} ms")


if __name__ == "__main__":
    main()
