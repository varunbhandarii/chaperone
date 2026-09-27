"""Tests never reach live services: no ledger forwarding to a running relay, no Visa VTC calls, and the mock
payment links, whatever the team .env says (MOCK_VISA=0 with real Cybersource keys would make real links when a
test module imports the merchant before setting MOCK_VISA itself, e.g. policy/tests/test_postpurchase.py).

common.config loads .env with setdefault and the services' load_dotenv doesn't override, so values set here
win. A test that needs one of them sets it itself (monkeypatch).
"""

import os

os.environ["RELAY_URL"] = ""
os.environ["VTC_MIRROR"] = "0"
os.environ["MOCK_VISA"] = "1"
