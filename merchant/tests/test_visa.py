import asyncio

import pytest

from merchant.visa import LineItem, VisaMcpError, VisaMcpPaymentLinks

CART = [LineItem("Lisinopril 10 mg, 30 tablets (pharmacy pickup)", 1, "8.00", "RX-001"),
        LineItem("Nature's Own Honey Wheat Bread", 1, "3.49", "BAK-001")]
ENSURE = [LineItem("Ensure Max Protein Shake; Milk Chocolate", 5, "9.99", "NUT-001")]


def fake_visa(total_back, stored_total=None, read_fails=False, read_delay=0.0):
    """Cybersource through the MCP toolkit: create answers total_back, a later read answers stored_total."""
    links = VisaMcpPaymentLinks("m", "k", "s")
    links._session = object()  # a live MCP session, so the read-back runs
    calls = {}

    def link(total):
        return {"id": "L1", "status": "ACTIVE",
                "purchaseInformation": {"paymentLink": "https://ebc2test.cybersource.com/x", "purchaseNumber": "P1"},
                "orderInformation": {"amountDetails": {"totalAmount": total, "currency": "USD"}}}

    async def call(tool, args):
        calls[tool] = args
        if tool == "get_payment_link":
            await asyncio.sleep(read_delay)
            if read_fails:
                raise VisaMcpError("get_payment_link: Failed to get payment link")
            return link(stored_total if stored_total is not None else total_back)
        return link(total_back)

    links._call = call
    return links, calls


def test_multi_item_cart_goes_up_as_one_line_with_safe_description():
    links, calls = fake_visa("11.49")
    link = asyncio.run(links.create("P1", "11.49", "USD", CART))
    assert link.amount == "11.49"
    [line] = calls["create_payment_link"]["lineItems"]
    assert line["unitPrice"] == "11.49" and line["quantity"] == "1"
    assert line["productName"] == "Corner Market order (2 items)"
    assert ";" not in line["productDescription"] and "Bread" in line["productDescription"]
    assert calls["get_payment_link"] == {"id": "L1"}


def test_single_line_with_quantity_goes_up_as_one_unit_at_the_total():
    """Five Ensure at $9.99 once made a $9.99 link: Pay by Link priced one unit."""
    links, calls = fake_visa("49.95")
    assert asyncio.run(links.create("P1", "49.95", "USD", ENSURE)).amount == "49.95"
    [line] = calls["create_payment_link"]["lineItems"]
    assert line["quantity"] == "1" and line["unitPrice"] == "49.95"
    assert line["productName"] == "5 x Ensure Max Protein Shake  Milk Chocolate"
    assert line["productDescription"].startswith("5 x Ensure") and len(line["productDescription"]) < 256
    assert line["productSKU"] == "NUT-001"


def test_single_item_of_one_keeps_its_plain_name():
    links, calls = fake_visa("8.00")
    asyncio.run(links.create("P1", "8.00", "USD", CART[:1]))
    assert calls["create_payment_link"]["lineItems"][0]["productName"].startswith("Lisinopril")


def test_total_mismatch_is_an_error():
    links, _ = fake_visa("8")  # what Pay by Link did with a raw 2-item cart
    with pytest.raises(VisaMcpError):
        asyncio.run(links.create("P1", "11.49", "USD", CART))


def test_stored_total_is_checked_after_creation():
    links, _ = fake_visa("49.95", stored_total="9.99")
    with pytest.raises(VisaMcpError, match="stored the link total as 9.99"):
        asyncio.run(links.create("P1", "49.95", "USD", ENSURE))


def test_a_failed_read_back_keeps_the_real_link():
    links, _ = fake_visa("49.95", read_fails=True)
    assert asyncio.run(links.create("P1", "49.95", "USD", ENSURE)).id == "L1"


def test_description_stays_under_the_field_limit():
    many = [LineItem(f"Very long grocery product name number {i} with extra words", 2, "1.00", f"S{i}") for i in range(20)]
    links, calls = fake_visa("40.00")
    asyncio.run(links.create("P1", "40.00", "USD", many))
    line = calls["create_payment_link"]["lineItems"][0]
    assert len(line["productDescription"]) <= 250 and len(line["productName"]) <= 60


def test_slow_read_back_keeps_the_link_within_policys_budget():
    import time

    links, _ = fake_visa("49.95", read_delay=5)
    start = time.perf_counter()
    link = asyncio.run(links.create("P1", "49.95", "USD", ENSURE))
    assert link.amount == "49.95"
    assert time.perf_counter() - start < 2.5  # the read-back gives up after 1.5 s; policy allows 5 s in all
