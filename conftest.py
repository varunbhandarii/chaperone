"""Tests never reach live services: no ledger forwarding to a running relay, no Visa VTC calls.

The team .env sets RELAY_URL and holds the VTC keys; common.config loads it with setdefault, so values set here
win. A test that needs either sets it itself (monkeypatch).
"""

import os

os.environ["RELAY_URL"] = ""
os.environ["VTC_MIRROR"] = "0"
