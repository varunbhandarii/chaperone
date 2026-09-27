import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from relay import ledger

WALL = (Path(__file__).resolve().parents[1] / "wall.html").read_text(encoding="utf-8")
SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json").read_text(encoding="utf-8"))


def test_header_is_the_trust_ledger_with_no_store_name_or_fake_total():
    assert "Trust Ledger · Visa sandbox" in WALL
    assert "Corner Market" not in WALL and "142.10" not in WALL


def test_every_event_type_has_a_plain_english_row_or_is_deliberately_hidden():
    handled = set(re.findall(r'case "([a-z_]+)"', WALL))
    missing = set(SCHEMA["properties"]["type"]["enum"]) - handled
    assert not missing, f"no summary on the wall for {sorted(missing)}"


def test_four_guard_lanes_and_the_protected_counter():
    for lane in ("lane-ask", "lane-card", "lane-agent", "lane-family"):
        assert f'id="{lane}"' in WALL
    assert "/wall/data/protected" in WALL and "/wall/data/visa_mandate" in WALL


def test_store_names_come_from_the_registry():
    body = TestClient(ledger.app).get("/wall/data/stores").json()
    names = {m["id"]: m["name"] for m in body["merchants"]}
    assert names["parkside_pharmacy"] == "Parkside Pharmacy" and names["quickgift_cards"] == "QuickGift Cards"
    assert {s["acceptor_id"] for s in body["card_terminal_stores"]} >= {"FIVEPTSDRUG01", "GIFTCARDMALL1"}
