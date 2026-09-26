"""Peachtree Power, the biller.

    GET /billers/peachtree_power/accounts/PP-2231-0098   the real balance, for bill_status and the scam story

Paying: the cart holds the pseudo-sku BILL-peachtree_power (qty 1). Policy prices it from balance_due; the
merchant prices it the same way here, never from the catalog, and makes the Pay by Link on Peachtree Power's
storefront. Once paid, the account shows nothing due and the payment as last_payment. /reset restores the seed.
"""

from __future__ import annotations

import copy
import datetime

BILL_SKU_PREFIX = "BILL-"
CATEGORY = "utility_bill"

# Ruth's account. The scam story: "Your real bill is $86.40, due October 15, not past due."
SEED = {
    "peachtree_power": {
        "name": "Peachtree Power",
        "default_account": "PP-2231-0098",
        "accounts": {
            "PP-2231-0098": {"balance_due": "86.40", "due_date": "2026-10-15", "past_due": False, "autopay": False,
                             "last_payment": {"amount": "91.12", "at": "2026-09-12"}, "disconnect_notice": False},
        },
    },
}
BILLERS: dict = copy.deepcopy(SEED)

SAY = {
    "en": "Your {biller} bill is ${balance} and due {due}. It is not past due, and there is no disconnect notice.",
    "es": "Su factura de {biller} es de ${balance} y vence el {due}. No está atrasada y no hay aviso de corte.",
    "hi": "आपका {biller} बिल ${balance} है और {due} तक देना है। यह बकाया नहीं है, और बिजली काटने का कोई नोटिस नहीं है।",
}
SAY_PAID = {
    "en": "Your {biller} bill is paid. Nothing is due, and there is no disconnect notice.",
    "es": "Su factura de {biller} está pagada. No debe nada y no hay aviso de corte.",
    "hi": "आपका {biller} बिल भर दिया गया है। कुछ बकाया नहीं है, और कोई नोटिस नहीं है।",
}
SAY_PAST_DUE = {
    "en": "Your {biller} bill of ${balance} was due {due}. Please pay it through me or the number on your paper bill, never by gift card.",
    "es": "Su factura de {biller} de ${balance} venció el {due}. Páguela conmigo o al número de su factura en papel, nunca con tarjetas de regalo.",
    "hi": "आपका {biller} बिल ${balance} {due} को देना था। इसे मेरे ज़रिए या कागज़ी बिल के नंबर से भरें, गिफ्ट कार्ड से कभी नहीं।",
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"],
}


class BillError(Exception):
    def __init__(self, http_status: int, error: str):
        super().__init__(error)
        self.http_status, self.error = http_status, error


def reset() -> None:
    BILLERS.clear()
    BILLERS.update(copy.deepcopy(SEED))


def is_bill(sku: str) -> bool:
    return sku.startswith(BILL_SKU_PREFIX)


def masked(ref: str) -> str:
    """PP-2231-0098 -> PP-...0098 (ASCII: Pay by Link rejects the ellipsis character)."""
    return f"{ref.split('-')[0]}-...{ref[-4:]}" if "-" in ref else ref


def _spoken_date(iso: str, lang: str) -> str:
    day = datetime.date.fromisoformat(iso)
    if lang == "es":
        return f"{day.day} de {MONTHS['es'][day.month - 1]}"
    if lang == "hi":
        return day.strftime("%d-%m-%Y")
    return f"{MONTHS['en'][day.month - 1]} {day.day}"


def account(biller_id: str, ref: str | None = None, lang: str = "en") -> dict:
    biller = BILLERS.get(biller_id)
    if biller is None:
        raise BillError(404, f"unknown biller {biller_id}")
    ref = ref or biller["default_account"]
    acct = biller["accounts"].get(ref)
    if acct is None:
        raise BillError(404, f"unknown account {ref}")
    lang = lang if lang in SAY else "en"
    balance = acct["balance_due"]
    if float(balance) == 0:
        template = SAY_PAID
    elif acct["past_due"]:
        template = SAY_PAST_DUE
    else:
        template = SAY
    say = template[lang].format(biller=biller["name"], balance=balance, due=_spoken_date(acct["due_date"], lang))
    return {"biller": biller["name"], "biller_id": biller_id, "account_ref": ref, **copy.deepcopy(acct),
            "say": say, "lang": lang}


def price_line(sku: str, merchant: str, qty: int) -> dict:
    """The order line for BILL-<biller>: the current balance, on that biller's own storefront only."""
    biller_id = sku[len(BILL_SKU_PREFIX):]
    if biller_id != merchant:
        raise BillError(422, f"{sku} can only be paid at {biller_id}")
    if qty != 1:
        raise BillError(422, "a bill is paid once per order (qty 1)")
    facts = account(biller_id)
    if float(facts["balance_due"]) <= 0:
        raise BillError(409, f"nothing is due on {facts['account_ref']}")
    return {"sku": sku, "name": f"{facts['biller']} bill {masked(facts['account_ref'])}", "qty": 1,
            "unit_price": facts["balance_due"], "regular_price": None, "merchant": biller_id,
            "category": CATEGORY, "mandate_category": CATEGORY, "account_ref": facts["account_ref"]}


def record_payment(lines: list[dict], paid_at: float) -> None:
    """After the bill's order is paid: nothing due, and this payment becomes last_payment."""
    day = datetime.datetime.fromtimestamp(paid_at, datetime.timezone.utc).date().isoformat()
    for line in lines:
        if not is_bill(line["sku"]):
            continue
        biller = BILLERS.get(line["merchant"])
        acct = (biller or {}).get("accounts", {}).get(line.get("account_ref"))
        if acct is not None:
            acct.update(balance_due="0.00", past_due=False, disconnect_notice=False,
                        last_payment={"amount": line["unit_price"], "at": day})
