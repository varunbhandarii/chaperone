"""Point Lithic at this machine's tunnel. python -m policy.card_setup

Creates Ruth's sandbox card if sessions/card.json has none, enrolls
https://$TUNNEL_HOST/card/asa, and checks the account secret against LITHIC_WEBHOOK_SECRET.
The card number is written to sessions/card.json and is never printed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def main() -> int:
    key = os.environ.get("LITHIC_API_KEY", "")
    host = os.environ.get("TUNNEL_HOST", "")
    if not key or not host:
        print("LITHIC_API_KEY and TUNNEL_HOST are required", file=sys.stderr)
        return 1
    url = f"https://{host}/card/asa"
    headers = {"Authorization": key, "Content-Type": "application/json"}
    base = "https://sandbox.lithic.com/v1"
    card_path = Path(os.environ.get("CARD_PATH", ROOT / "sessions" / "card.json"))
    with httpx.Client(timeout=30) as client:
        card = json.loads(card_path.read_text()) if card_path.exists() else {}
        if not card.get("pan"):
            created = client.post(f"{base}/cards", headers=headers, json={"type": "VIRTUAL", "memo": "Ruth", "state": "OPEN"})
            created.raise_for_status()
            body = created.json()
            card = {"token": body.get("token"), "pan": body.get("pan"), "last_four": body.get("last_four")}
            card_path.parent.mkdir(parents=True, exist_ok=True)
            card_path.write_text(json.dumps(card))
        print(f"card last four {card.get('last_four')}")
        client.delete(f"{base}/responder_endpoints", headers=headers, params={"type": "AUTH_STREAM_ACCESS"})
        enrolled = client.post(f"{base}/responder_endpoints", headers=headers, json={"type": "AUTH_STREAM_ACCESS", "url": url})
        enrolled.raise_for_status()
        print(f"enrolled {url}")
        secret = client.get(f"{base}/auth_stream/secret", headers=headers)
        secret.raise_for_status()
        remote = (secret.json() or {}).get("secret") or ""
        local = os.environ.get("LITHIC_WEBHOOK_SECRET", "")
        if remote and remote == local:
            print("webhook secret matches .env")
        else:
            print("webhook secret does not match LITHIC_WEBHOOK_SECRET", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
