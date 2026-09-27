"""Tests never reach live services: no ledger forwarding to a running relay, no Visa VTC calls, no Kroger, and
the mock payment links, whatever the team .env says (MOCK_VISA=0 with real Cybersource keys would make real links
when a test module imports the merchant before setting MOCK_VISA itself, e.g. policy/tests/test_postpurchase.py).

common.config loads .env with setdefault and the services' load_dotenv doesn't override, so values set here
win. A test that needs one of them sets it itself (monkeypatch).
"""

import os

os.environ["RELAY_URL"] = ""
os.environ["VTC_MIRROR"] = "0"
os.environ["MOCK_VISA"] = "1"
os.environ["KROGER_LIVE"] = "0"  # no live Kroger search; catalog/tests/test_live.py fakes Kroger itself
os.environ["CATALOG_LIVE_PATH"] = ""  # and no shared file of live items
