"""Decision and monthly-spend files. Paths come from the environment so tests stay isolated."""

from __future__ import annotations

import json

from common.config import decisions_path
from policy.mandate import MONTHLY_BASELINE


def monthly_path():
    from common.config import env
    from pathlib import Path

    return Path(env("MONTHLY_PATH", str(decisions_path().parent / "monthly.json")))


def mandate_path():
    from common.config import env
    from pathlib import Path

    return Path(env("MANDATE_PATH", str(decisions_path().parent / "mandate.json")))


def _read(path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8") or "null") or fallback


def _write(path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def load_spent_cents() -> int:
    data = _read(monthly_path(), None)
    if not data:
        return int(round(MONTHLY_BASELINE * 100))
    return int(data["spent_cents"])


def store_spent_cents(cents: int) -> None:
    _write(monthly_path(), {"spent_cents": cents, "total": round(cents / 100, 2)})


def load_decisions() -> dict:
    return _read(decisions_path(), {})


def save_decision(document: dict) -> None:
    current = load_decisions()
    current[document["decision_id"]] = document
    _write(decisions_path(), current)


def get_decision(decision_id: str) -> dict | None:
    return load_decisions().get(decision_id)


def save_mandate(document: dict) -> None:
    _write(mandate_path(), document)


def load_mandate() -> dict | None:
    path = mandate_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def reset() -> None:
    store_spent_cents(int(round(MONTHLY_BASELINE * 100)))
    path = decisions_path()
    if path.exists():
        path.unlink()
