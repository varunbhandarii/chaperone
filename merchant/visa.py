"""Payment links: Visa Acceptance Agent Toolkit MCP (Cybersource sandbox) or a mock with the same shape.

get_payment_links() picks the backend:
  MOCK_VISA=1, or any VISA_ACCEPTANCE_* credential missing -> MockPaymentLinks
  otherwise -> VisaMcpPaymentLinks, falling back to the mock on error unless VISA_FALLBACK_TO_MOCK=0

Two gotchas in @visaacceptance/mcp 0.0.96 that this module works around:
  * credentials go in env vars, not --secret-key=..., because the CLI splits each flag on every "=" and
    truncates base64 secrets that end in "=".
  * the API host is sandbox only when VISA_ACCEPTANCE_ENVIRONMENT=SANDBOX is set explicitly; unset it
    silently calls production api.cybersource.com. We always pass SANDBOX.
"""

import asyncio
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field

MCP_PACKAGE = "@visaacceptance/mcp@0.0.96"
CRED_VARS = ("VISA_ACCEPTANCE_MERCHANT_ID", "VISA_ACCEPTANCE_API_KEY_ID", "VISA_ACCEPTANCE_SECRET_KEY")


@dataclass
class LineItem:
    productName: str
    quantity: int
    unitPrice: str  # "3.49", always a string for the API
    productSKU: str = ""


@dataclass
class PaymentLink:
    id: str
    url: str
    status: str  # ACTIVE | INACTIVE (Visa); the mock also uses PAID
    amount: str
    currency: str
    purchase_number: str
    backend: str  # "visa" | "mock"
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def new_purchase_number() -> str:
    """< 20 alphanumeric chars, unique per link."""
    return f"CM{int(time.time()):x}{secrets.token_hex(3)}".upper()[:19]


def safe_text(text: str) -> str:
    """Visa rejects some punctuation in line-item text (";" fails the whole request)."""
    return re.sub(r"[^A-Za-z0-9 ,.'()/-]", " ", text)


def money(value: float) -> str:
    return f"{value:.2f}"


def same_amount(a, b) -> bool:
    try:
        return round(float(a) * 100) == round(float(b) * 100)
    except (TypeError, ValueError):
        return False


def one_line(amount: str, line_items: list[LineItem]) -> dict:
    """The whole cart as one Pay by Link line: quantity "1" and unitPrice = the cart total.

    Pay by Link prices a link from the first line's single unit: a 2-item $11.49 cart came back $8.00 and
    a single line "5 x $9.99" came back $9.99. One line whose unitPrice equals totalAmount is the pattern
    the API documents. The itemised list goes in productDescription (under 256 characters).
    """
    if len(line_items) == 1:
        li = line_items[0]
        name = f"{li.quantity} x {li.productName}" if li.quantity != 1 else li.productName
    else:
        name = f"Corner Market order ({sum(li.quantity for li in line_items)} items)"
    line = {
        "productName": safe_text(name)[:60].strip(),
        "productDescription": safe_text(", ".join(f"{li.quantity} x {li.productName}" for li in line_items))[:250],
        "quantity": "1",
        "unitPrice": amount,
    }
    if len(line_items) == 1 and line_items[0].productSKU:
        line["productSKU"] = line_items[0].productSKU
    return line


class MockPaymentLinks:
    backend = "mock"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.links: dict[str, PaymentLink] = {}

    async def create(self, purchase_number: str, amount: str, currency: str, line_items: list[LineItem]) -> PaymentLink:
        link_id = "mock_" + secrets.token_hex(6)
        link = PaymentLink(
            id=link_id,
            url=f"{self.base_url}/pay/{link_id}",
            status="ACTIVE",
            amount=amount,
            currency=currency,
            purchase_number=purchase_number,
            backend=self.backend,
            raw={"lineItems": [asdict(li) for li in line_items]},
        )
        self.links[link_id] = link
        return link

    async def get(self, link_id: str) -> PaymentLink | None:
        return self.links.get(link_id)

    async def start(self):
        pass

    async def close(self):
        pass


class VisaMcpError(RuntimeError):
    pass


class VisaMcpPaymentLinks:
    """One long-lived MCP stdio session (npx startup is seconds; the demo can't pay that per order)."""

    backend = "visa"

    def __init__(self, merchant_id: str, api_key_id: str, secret_key: str):
        self.env = {
            **os.environ,
            "VISA_ACCEPTANCE_MERCHANT_ID": merchant_id,
            "VISA_ACCEPTANCE_API_KEY_ID": api_key_id,
            "VISA_ACCEPTANCE_SECRET_KEY": secret_key,
            "VISA_ACCEPTANCE_ENVIRONMENT": "SANDBOX",
        }
        self._session = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def _run_session(self, ready: asyncio.Future):
        # The stdio client's task group must be entered and exited in the same task, so it lives here.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command="npx",
            args=["-y", MCP_PACKAGE, "--tools=paymentLinks.create,paymentLinks.read"],
            env=self.env,
        )
        try:
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                ready.set_result(session)
                await self._stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            else:
                print(f"[visa] MCP session ended: {type(e).__name__}: {e}")
        finally:
            self._session = None

    async def _ensure_session(self):
        if self._session is None:
            self._stop = asyncio.Event()
            ready = asyncio.get_running_loop().create_future()
            self._task = asyncio.create_task(self._run_session(ready))
            self._session = await asyncio.wait_for(ready, timeout=90)  # first npx run downloads the package
        return self._session

    async def start(self):
        """Warm the npx process at server startup so the first order isn't slow."""
        async with self._lock:
            await self._ensure_session()

    async def _call(self, tool: str, args: dict) -> dict:
        async with self._lock:
            session = await self._ensure_session()
            try:
                result = await asyncio.wait_for(session.call_tool(tool, args), timeout=30)
            except Exception:
                await self._close_unlocked()  # reconnect on the next call
                raise
        text = "".join(getattr(c, "text", "") for c in result.content)
        # The toolkit swallows API errors and returns a bare string like "Failed to create payment link".
        is_error = getattr(result, "is_error", None) or getattr(result, "isError", False)
        if is_error or not text.lstrip().startswith("{"):
            raise VisaMcpError(f"{tool}: {text.strip() or 'empty response'}")
        return json.loads(text)

    async def create(self, purchase_number: str, amount: str, currency: str, line_items: list[LineItem]) -> PaymentLink:
        args = {
            "linkType": "PURCHASE",
            "purchaseNumber": purchase_number,
            "currency": currency,
            "totalAmount": amount,
            "lineItems": [one_line(amount, line_items)],
        }
        data = await self._call("create_payment_link", args)
        link = self._to_link(data, purchase_number, amount, currency)
        if not same_amount(link.amount, amount):
            raise VisaMcpError(f"Visa set the link total to {link.amount}, expected {amount}")
        try:  # read the stored link back: the create answer echoes our request more than it shows the link
            stored = await self.get(link.id)
        except Exception as e:  # noqa: BLE001 - the create answer already matched; keep the real link
            print(f"[visa] could not read link {link.id} back: {type(e).__name__}: {e}")
            return link
        if stored is not None and stored.amount and not same_amount(stored.amount, amount):
            raise VisaMcpError(f"Visa stored the link total as {stored.amount}, expected {amount}")
        return link

    async def get(self, link_id: str) -> PaymentLink | None:
        data = await self._call("get_payment_link", {"id": link_id})
        return self._to_link(data, None, None, None)

    def _to_link(self, data: dict, purchase_number, amount, currency) -> PaymentLink:
        purchase = data.get("purchaseInformation", {})
        details = data.get("orderInformation", {}).get("amountDetails", {})
        url = purchase.get("paymentLink") or data.get("paymentLink") or ""
        if not data.get("id") or not url:
            raise VisaMcpError(f"unexpected payment link response: {json.dumps(data)[:300]}")
        return PaymentLink(
            id=data["id"],
            url=url,
            status=data.get("status", "ACTIVE"),
            amount=details.get("totalAmount") or amount or "",
            currency=details.get("currency") or currency or "USD",
            purchase_number=purchase.get("purchaseNumber") or purchase_number or "",
            backend=self.backend,
            raw=data,
        )

    async def _close_unlocked(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
        self._task = self._session = None

    async def close(self):
        async with self._lock:
            await self._close_unlocked()


class FallbackPaymentLinks:
    """Real Visa first; on any failure the demo keeps going on the mock."""

    def __init__(self, primary: VisaMcpPaymentLinks, fallback: MockPaymentLinks):
        self.primary, self.fallback = primary, fallback
        self.last_error: str | None = None

    @property
    def backend(self) -> str:
        return self.primary.backend

    async def start(self):
        try:
            await self.primary.start()
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            print(f"[visa] MCP warm-up failed, orders will fall back to mock: {self.last_error}")

    async def create(self, *args, **kwargs) -> PaymentLink:
        try:
            link = await self.primary.create(*args, **kwargs)
            self.last_error = None
            return link
        except Exception as e:  # noqa: BLE001 - any Visa/MCP/npx failure falls back
            self.last_error = f"{type(e).__name__}: {e}"
            print(f"[visa] falling back to mock: {self.last_error}")
            return await self.fallback.create(*args, **kwargs)

    async def get(self, link_id: str) -> PaymentLink | None:
        if link_id.startswith("mock_"):
            return await self.fallback.get(link_id)
        return await self.primary.get(link_id)

    async def close(self):
        await self.primary.close()


def get_payment_links(mock_base_url: str):
    mock = MockPaymentLinks(mock_base_url)
    creds = [os.environ.get(v, "") for v in CRED_VARS]
    if os.environ.get("MOCK_VISA") == "1" or not all(creds):
        return mock
    real = VisaMcpPaymentLinks(*creds)
    if os.environ.get("VISA_FALLBACK_TO_MOCK", "1") == "0":
        return real
    return FallbackPaymentLinks(real, mock)
