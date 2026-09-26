import asyncio
import os
import secrets
import time

os.environ["MOCK_VISA"] = "1"
os.environ["MERCHANT_VERIFY"] = "off"
os.environ["MERCHANT_PUBLIC_URL"] = "http://testserver"
os.environ.pop("RELAY_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from common.host_header import HEADERS as HOST  # noqa: E402
from merchant import orders, verify  # noqa: E402
from merchant.orders import app  # noqa: E402

MANDATE = "m_ruth_2026_09"
MEDICINE_AND_BREAD = [{"sku": "RX-001", "qty": 1}, {"sku": "BAK-001", "qty": 2}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ORDER_PREPARING_S", "0.05")
    monkeypatch.setenv("ORDER_READY_S", "0.15")
    monkeypatch.setenv("REFUND_TRANSMIT_S", "0.05")
    with TestClient(app, headers=HOST) as c:
        c.post("/reset")
        yield c


def place(client, items=MEDICINE_AND_BREAD, session_id="s1"):
    body = {"mandate_id": MANDATE, "decision_id": "d_" + secrets.token_hex(6), "session_id": session_id,
            "cart": {"items": items}}
    return client.post("/orders", json=body).json()


def pay(client, order):
    return client.post(f"/orders/{order['order_id']}/paid", params={"via": "host_confirmed"}).json()


def action(order, **extra):
    return {"mandate_id": MANDATE, "decision_id": order["decision_id"], "order_id": order["order_id"],
            "session_id": order["session_id"], **extra}


def wait_for(client, order_id, predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = client.get(f"/orders/{order_id}").json()
        if predicate(current):
            return current
        time.sleep(0.02)
    raise AssertionError(f"timed out; last {current}")


def event_types(client):
    return [e["type"] for e in client.get("/panel").json()["events"]]


# ---- lifecycle

def test_paid_order_moves_to_preparing_then_ready_with_a_pickup_code(client):
    order = place(client)
    assert order["status"] == "awaiting_payment" and len(order["pickup_code"]) == 3
    pay(client, order)
    ready = wait_for(client, order["order_id"], lambda o: o["status"] == "ready_for_pickup")
    assert [t["status"] for t in ready["timeline"]] == ["awaiting_payment", "paid", "preparing", "ready_for_pickup"]
    statuses = [e for e in client.get("/panel").json()["events"] if e["type"] == "order_status"]
    assert [e["status"] for e in statuses] == ["preparing", "ready_for_pickup"]
    assert all(e["pickup_code"] == order["pickup_code"] for e in statuses)


def test_repeat_payment_does_not_rewind_the_lifecycle(client):
    order = place(client)
    pay(client, order)
    wait_for(client, order["order_id"], lambda o: o["status"] == "preparing")
    again = pay(client, order)
    assert again["status"] in ("preparing", "ready_for_pickup") and event_types(client).count("paid") == 1


def test_host_marks_picked_up(client):
    order = place(client)
    pay(client, order)
    done = client.post(f"/orders/{order['order_id']}/picked-up").json()
    assert done["status"] == "picked_up"
    time.sleep(0.25)  # the cancelled timers do not move it back to ready
    assert client.get(f"/orders/{order['order_id']}").json()["status"] == "picked_up"
    assert TestClient(app).post(f"/orders/{order['order_id']}/picked-up").status_code == 403


def test_fully_refunded_order_stops_advancing(client):
    order = place(client, [{"sku": "BAK-001", "qty": 1}])
    pay(client, order)
    client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1))
    time.sleep(0.25)
    assert client.get(f"/orders/{order['order_id']}").json()["status"] == "refunded"


def test_partially_refunded_order_still_gets_ready_for_pickup(client):
    order = place(client)
    pay(client, order)
    client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1))
    ready = wait_for(client, order["order_id"], lambda o: o["fulfilment"] == "ready_for_pickup")
    assert ready["status"] == "partially_refunded"


def test_reset_stops_the_timers(client):
    order = place(client)
    pay(client, order)
    client.post("/reset")
    time.sleep(0.2)
    assert orders.ORDERS == {} and not any(orders.TIMERS.values())


def test_list_filters_by_session(client):
    place(client, session_id="s1")
    other = place(client, session_id="s2")
    assert [o["order_id"] for o in client.get("/orders", params={"session_id": "s2"}).json()] == [other["order_id"]]


# ---- cancel

def test_cancel_an_unpaid_order_deactivates_its_link(client):
    order = place(client, [{"sku": "NUT-001", "qty": 1}])
    r = client.post(f"/orders/{order['order_id']}/cancel", json=action(order))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled" and r.json()["link_status"] == "INACTIVE"
    link_id = order["payment_link"]["id"]
    assert orders.payment_links.links[link_id].status == "INACTIVE"
    stored = client.get(f"/orders/{order['order_id']}").json()
    assert stored["status"] == "cancelled" and stored["timeline"][-1]["status"] == "cancelled"
    assert "order_cancelled" in event_types(client)
    # the payment page and a late payment both leave it cancelled
    assert "cancelled" in client.get(f"/pay/{link_id}").text
    client.post(f"/pay/{link_id}")
    assert pay(client, order)["status"] == "cancelled"
    again = client.post(f"/orders/{order['order_id']}/cancel", json=action(order)).json()
    assert again["duplicate"] is True


def test_a_paid_order_cannot_be_cancelled(client):
    order = place(client)
    pay(client, order)
    r = client.post(f"/orders/{order['order_id']}/cancel", json=action(order))
    assert r.status_code == 409 and "returned" in r.json()["detail"]["error"]


def test_cancel_checks_the_mandate_and_the_path(client):
    order = place(client)
    assert client.post(f"/orders/{order['order_id']}/cancel", json=action(order, mandate_id="m_other")).status_code == 403
    assert client.post(f"/orders/{order['order_id']}/cancel", json=action(order, order_id="ord_x")).status_code == 422
    assert client.post("/orders/ord_nope/cancel", json=action(order, order_id="ord_nope")).status_code == 404


def test_a_failed_deactivation_leaves_the_order_payable(client, monkeypatch):
    order = place(client)

    async def down(*args):
        raise RuntimeError("cybersource down")
    monkeypatch.setattr(orders.payment_links, "deactivate", down)
    assert client.post(f"/orders/{order['order_id']}/cancel", json=action(order)).status_code == 502
    assert client.get(f"/orders/{order['order_id']}").json()["status"] == "awaiting_payment"


# ---- refunds

def test_partial_then_full_refund_in_cybersource_shape(client):
    order = place(client, [{"sku": "BAK-001", "qty": 2}, {"sku": "NUT-001", "qty": 1}])
    pay(client, order)
    r = client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1, reason="stale"))
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["status"] == "PENDING" and answer["source"] == "sandbox-processor-stub"
    assert answer["refundAmountDetails"] == {"refundAmount": "3.49", "currency": "USD"}
    assert answer["processorInformation"]["responseCode"] == "100" and len(answer["id"]) == 22
    assert answer["reconciliationId"]
    stored = client.get(f"/orders/{order['order_id']}").json()
    assert stored["status"] == "partially_refunded" and stored["refunds"][0]["card_last4"] == "1111"
    wait_for(client, order["order_id"], lambda o: o["refunds"][0]["status"] == "TRANSMITTED")
    results = [e["status"] for e in client.get("/panel").json()["events"] if e["type"] == "refund_result"]
    assert results == ["PENDING", "TRANSMITTED"]
    # the rest: the other bread and the shake
    client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1))
    client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="NUT-001", qty=1))
    done = client.get(f"/orders/{order['order_id']}").json()
    assert done["status"] == "refunded"
    receipt = client.get(f"/orders/{order['order_id']}/receipt").json()
    assert len(receipt["refunds"]) == 3 and receipt["refunds"][0]["label"].endswith("sandbox processor stub")


def test_over_refund_is_409(client):
    order = place(client, [{"sku": "BAK-001", "qty": 1}])
    pay(client, order)
    assert client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=2)).status_code == 409
    client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1))
    again = client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1))
    assert again.status_code == 409


def test_refund_amount_comes_from_the_order_not_the_caller(client):
    order = place(client, [{"sku": "BAK-001", "qty": 1}])
    pay(client, order)
    r = client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1, amount="100.00"))
    assert r.status_code == 422 and r.json()["detail"]["amount"] == "3.49"
    ok = client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1, amount="3.49"))
    assert ok.status_code == 200


def test_refund_has_no_destination_field(client):
    order = place(client, [{"sku": "BAK-001", "qty": 1}])
    pay(client, order)
    r = client.post(f"/orders/{order['order_id']}/refunds",
                    json=action(order, sku="BAK-001", qty=1, card_number="4000000000000002", to="gift card"))
    assert r.status_code == 200  # extra fields are ignored: money only goes back to the paying card
    assert client.get(f"/orders/{order['order_id']}").json()["refunds"][0]["card_last4"] == "1111"


def test_prescription_and_unpaid_orders_are_not_refundable(client):
    order = place(client)
    assert client.post(f"/orders/{order['order_id']}/refunds",
                       json=action(order, sku="BAK-001", qty=1)).status_code == 409  # not paid yet
    pay(client, order)
    rx = client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="RX-001", qty=1))
    assert rx.status_code == 409 and rx.json()["detail"]["say_key"] == "refund_not_allowed_rx"
    assert client.post(f"/orders/{order['order_id']}/refunds",
                       json=action(order, sku="EGG-001", qty=1)).status_code == 422  # not in this order


def test_events_follow_the_contract(client):
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json").read_text())
    order = place(client, [{"sku": "BAK-001", "qty": 2}])
    pay(client, order)
    client.post(f"/orders/{order['order_id']}/refunds", json=action(order, sku="BAK-001", qty=1))
    wait_for(client, order["order_id"], lambda o: o["fulfilment"] == "ready_for_pickup" and o["refunds"][0]["status"] == "TRANSMITTED")
    unpaid = place(client, [{"sku": "NUT-001", "qty": 1}])
    client.post(f"/orders/{unpaid['order_id']}/cancel", json=action(unpaid))
    for event in client.get("/panel").json()["events"]:
        jsonschema.validate(event, schema)


# ---- the decision check for post-purchase requests (enforce mode)

def test_post_purchase_decision_must_belong_to_the_order():
    order = {"order_id": "ord_1", "decision_id": "d_order", "mandate_id": MANDATE}
    body = b'{"decision_id": "d_order", "mandate_id": "m_ruth_2026_09", "order_id": "ord_1"}'
    allow = {"decision": "allow"}
    assert "for order ord_1" in asyncio.run(verify.check_decision(body, lambda _: allow, order))
    refund_decision = b'{"decision_id": "d_refund", "mandate_id": "m_ruth_2026_09", "order_id": "ord_1"}'
    assert asyncio.run(verify.check_decision(refund_decision, lambda _: {**allow, "order_id": "ord_1"}, order))
    with pytest.raises(verify.DecisionError, match="not for order"):
        asyncio.run(verify.check_decision(refund_decision, lambda _: {**allow, "order_id": "ord_2"}, order))
    with pytest.raises(verify.DecisionError, match="another mandate"):
        asyncio.run(verify.check_decision(body.replace(b"m_ruth_2026_09", b"m_x"), lambda _: allow, order))
    with pytest.raises(verify.DecisionError, match="is deny"):
        asyncio.run(verify.check_decision(body, lambda _: {"decision": "deny"}, order))
