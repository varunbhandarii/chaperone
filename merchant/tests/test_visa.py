import asyncio

import pytest

from merchant.visa import LineItem, VisaMcpError, VisaMcpPaymentLinks

CART = [LineItem("Lisinopril 10 mg, 30 tablets (pharmacy pickup)", 1, "8.00", "RX-001"),
        LineItem("Nature's Own Honey Wheat Bread", 1, "3.49", "BAK-001")]


def fake_visa(total_back):
    links = VisaMcpPaymentLinks("m", "k", "s")
    sent = {}

    async def call(tool, args):
        sent.update(args)
        return {"id": "L1", "status": "ACTIVE",
                "purchaseInformation": {"paymentLink": "https://ebc2test.cybersource.com/x", "purchaseNumber": "P1"},
                "orderInformation": {"amountDetails": {"totalAmount": total_back, "currency": "USD"}}}

    links._call = call
    return links, sent


def test_multi_item_cart_goes_up_as_one_line_with_safe_description():
    links, sent = fake_visa("11.49")
    link = asyncio.run(links.create("P1", "11.49", "USD", CART))
    assert link.amount == "11.49"
    [line] = sent["lineItems"]
    assert line["unitPrice"] == "11.49" and line["quantity"] == "1"
    assert ";" not in line["productDescription"] and "Bread" in line["productDescription"]


def test_total_mismatch_is_an_error():
    links, _ = fake_visa("8")  # what Pay by Link did with a raw 2-item cart
    with pytest.raises(VisaMcpError):
        asyncio.run(links.create("P1", "11.49", "USD", CART))
