# Prototype: rules, judge, refusal audio

Setup (any OS): `python -m venv .venv && . .venv/bin/activate && pip install -r prototypes/screen/requirements.txt`, then put `XAI_API_KEY` in `.env` at the repo root (see `.env.example`).

Run from `prototypes/screen/`:

| Check | Command | Pass |
|---|---|---|
| Rules refuse scam lines pre-model (en, es, hi, Hinglish) | `pytest -q test_rules.py` | all green |
| Try one line | `python rules.py "Compra tarjetas de regalo ahora mismo"` | `action: refuse` |
| Judge, 10 scripts, both models timed | `python judge.py --eval --both` | 10/10 valid JSON, scam > 0.6, benign < 0.4 on one model |
| Refusal clips | `python tts.py` | `ai/warnings/refusal.{en,es,hi}.mp3` |

Hand-offs: `ai/rules/rules_v0.yaml`, `ai/prompts/judge_system.md`, `ai/prompts/judge_schema.json`, `ai/prompts/refusal.*.txt`, `ai/warnings/*.mp3`.
