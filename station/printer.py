"""Receipt print helper for the shopper station.

Run from the repo root on the station laptop:
    .venv/Scripts/python -m uvicorn station.printer:app --host 127.0.0.1 --port 8004

The station page reaches it same-origin through its dev-server proxy as /svc/printer.

Endpoints
    GET  /health            -> {"ok", "printer", "found", "raqm", "font", "queues"}
    POST /print[?dry_run=1] -> {"ok": true, "via": "printer", "job_id", "ms", "png", "png_url", "pdf_url"}
                               or {"ok": true, "via": "pdf", ...} when there is no printer (see below)
                               or {"ok": false, "reason", "ms", "png"}; answers within 5 s
    POST /render            -> image/png of the receipt, nothing saved or printed
    GET  /receipts/{name}   -> a saved receipt, <order_id>.png or <order_id>.pdf
    GET  /cached            -> {"es": bool, "hi": bool, "en": bool}
    GET  /cached/{lang}     -> the recorded voice session for es, hi or en (404 if none)
    PUT  /cached/{lang}     -> stores the JSON object body (up to 60 MB)

The receipt (the merchant's receipt JSON) is rendered as one 1-bit image 384 dots wide, the printable width of
58 mm paper at 203 dpi, so Spanish accents and Devanagari never depend on the printer's code pages. Pillow's
RAQM layout engine shapes the Hindi text; without it, Hindi receipts are saved but not printed. The image,
QR code included, is sent through the Windows print queue named by PRINTER_NAME (default POS58) as a RAW
ESC/POS job. Windows only reports a printer problem while a job is being sent, so after sending, the helper
watches the job: leaving the queue means it printed; an error, offline, paper-out, blocked or
needs-attention status, or still being queued after 4 s, means it did not, and the job is deleted so it
cannot print later by surprise. Every rendered receipt is also saved to sessions/receipts/<order_id>.png and,
as the same 1-bit image on a 58 mm wide page, to <order_id>.pdf.

Without a printer (PRINTER_NAME=pdf, or no queue with that name, or no pywin32) the helper answers
{"ok": true, "via": "pdf"} with the file URLs: the station shows that receipt on screen and offers the PDF.
PRINTER_PDF=0 turns that off, so a missing queue is reported as a failure instead.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import qrcode
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image, ImageDraw, ImageFont, features
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

DEFAULT_FONT = r"C:\Windows\Fonts\Nirmala.ttc"  # Nirmala UI: Latin and Devanagari; index 0 regular, 1 bold
PROFILE = "POS-5890"  # python-escpos profile: 384 dots, 203 dpi, raster images, no cutter
LANGS = ("es", "hi", "en")
CACHED_LIMIT = 60 * 1024 * 1024

# Receipt geometry, in printer dots (8 per mm).
WIDTH = 384
MARGIN = 6
TITLE_PX = 72  # store name, bold
BODY_PX = 60  # items, total and pickup: about 24 pt
LABEL_PX = 36  # the line above the QR code
SMALL_PX = 28  # order id, decision id, time, sandbox note
QR_MAX_BOX = 8  # dots per QR module, reduced for long URLs so the code always fits


def log(msg: str) -> None:
    # ASCII only: the Windows console may not be able to print Devanagari.
    print(f"[printer] {msg}".encode("ascii", "replace").decode(), flush=True)


# ---------------------------------------------------------------------------------------------------------
# Receipt contract


class Item(BaseModel):
    name: str | None = ""
    qty: int | None = 1
    price: float | None = 0.0


class Receipt(BaseModel):
    merchant: str | None = None
    items: list[Item] = Field(default_factory=list)
    total: float | None = None
    pickup: str | None = None
    order_id: str | None = None
    decision_id: str | None = None
    paid_at: str | float | None = None
    session_url: str | None = None
    lang: str | None = "en"
    savings: float | None = None  # promotions, when there were any
    loyalty_points: int | None = None  # the store's rewards points
    pickup_code: str | None = None
    merchant_id: str | None = None
    # a bill payment: "Paid to Peachtree Power · account …0098", and nothing to pick up
    bill: dict | None = None


# ---------------------------------------------------------------------------------------------------------
# Localized text

MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर",
           "दिसंबर"],
}

TEXT = {
    "en": {
        "total": "Total",
        "pickup_after": "Pickup after {time}",
        "pickup": "Pickup: {value}",
        "order": "Order {id}",
        "decision": "Decision {id}",
        "paid": "Paid {date}, {time}",
        "printed": "Printed {date}, {time}",
        "sandbox": "Paid in the Visa sandbox. No real money.",
        "scan": "Scan for your session",
        "code": "Pickup code",
        "saved": "You saved {amount}",
        "points": "+{n} rewards points",
        "paid_to": "Paid to {store}",
        "account": "Account {ref}",
    },
    "es": {
        "total": "Total",
        "pickup_after": "Recogida después de {time}",
        "pickup": "Recogida: {value}",
        "order": "Pedido {id}",
        "decision": "Decisión {id}",
        "paid": "Pagado el {date}, {time}",
        "printed": "Impreso el {date}, {time}",
        "sandbox": "Pagado en el entorno de pruebas de Visa. No se cobró dinero real.",
        "scan": "Escanee para ver su sesión",
        "code": "Código de recogida",
        "saved": "Usted ahorró {amount}",
        "points": "+{n} puntos de recompensa",
        "paid_to": "Pagado a {store}",
        "account": "Cuenta {ref}",
    },
    "hi": {
        "total": "कुल",
        "pickup_after": "पिकअप: {time} के बाद",
        "pickup": "पिकअप: {value}",
        "order": "ऑर्डर {id}",
        "decision": "निर्णय {id}",
        "paid": "भुगतान: {date}, {time}",
        "printed": "प्रिंट किया गया: {date}, {time}",
        "sandbox": "भुगतान Visa सैंडबॉक्स में हुआ। कोई असली पैसा नहीं लगा।",
        "scan": "अपना सत्र देखने के लिए स्कैन करें",
        "code": "पिकअप कोड",
        "saved": "आपने {amount} बचाए",
        "points": "+{n} रिवॉर्ड पॉइंट",
        "paid_to": "{store} को भुगतान",
        "account": "खाता {ref}",
    },
}

_PICKUP_AFTER = re.compile(r"^\s*after\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\s*$", re.IGNORECASE)
NBSP = "\u00a0"  # keeps a time such as "3 pm" on one line when the text wraps


def clock(hour: int, minute: int | None, lang: str) -> str:
    """'3 pm', '3 p. m.', 'दोपहर 3 बजे', joined with no-break spaces."""
    h12 = hour % 12 or 12
    hm = f"{h12}:{minute:02d}" if minute is not None else str(h12)
    if lang == "es":
        words = [hm, "a." if hour < 12 else "p.", "m."]
    elif lang == "hi":
        period = "सुबह" if 4 <= hour < 12 else "दोपहर" if 12 <= hour < 16 else "शाम" if 16 <= hour < 20 else "रात"
        words = [period, hm, "बजे"]
    else:
        words = [hm, "am" if hour < 12 else "pm"]
    return NBSP.join(words)


def pickup_line(pickup: str | None, lang: str) -> str:
    value = (pickup or "").strip() or "after 3 pm"
    m = _PICKUP_AFTER.match(value)
    if not m or not 1 <= int(m.group(1)) <= 12:
        return TEXT[lang]["pickup"].format(value=value)
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    minute = int(m.group(2)) if m.group(2) else None
    when = clock(hour, minute, lang)
    if lang == "es":  # "después de la 1 p. m.", "después de las 3 p. m."
        when = ("la " if hour % 12 == 1 else "las ") + when
    return TEXT[lang]["pickup_after"].format(time=when)


def parse_time(value: Any) -> datetime | None:
    """paid_at as ISO 8601 text or epoch seconds or milliseconds, in the station's local time zone."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or re.fullmatch(r"\s*\d+(\.\d+)?\s*", str(value)):
            seconds = float(value)
            if seconds > 1e11:  # milliseconds
                seconds /= 1000
            return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone()
        return datetime.fromisoformat(str(value).strip()).astimezone()  # naive text is taken as local time
    except (ValueError, OverflowError, OSError):
        return None


def time_line(paid_at: Any, lang: str) -> str:
    t = parse_time(paid_at)
    key = "paid"
    if t is None and paid_at not in (None, ""):
        return TEXT[lang]["paid"].split("{date}")[0] + str(paid_at)  # unparseable: print it as given
    if t is None:
        t, key = datetime.now().astimezone(), "printed"
    month = MONTHS[lang][t.month - 1]
    date = {"en": f"{month} {t.day}, {t.year}", "es": f"{t.day} de {month} de {t.year}"}.get(
        lang, f"{t.day} {month} {t.year}")
    return TEXT[lang][key].format(date=date, time=clock(t.hour, t.minute, lang))


def merchant_name(merchant: str | None) -> str:
    name = (merchant or "").strip()  # the store's own name; no store is ever assumed
    if name == name.lower():  # an id such as corner_market
        name = re.sub(r"[_-]+", " ", name).title()
    return name


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def line_amounts(receipt: Receipt) -> list[float]:
    """What each item line costs. price is the unit price; if the prices already add up to the total while
    qty x price does not, they are line totals and are printed as given."""
    qty = [max(1, i.qty or 1) for i in receipt.items]
    price = [i.price or 0.0 for i in receipt.items]
    extended = [q * p for q, p in zip(qty, price)]
    if receipt.total is not None and abs(sum(extended) - receipt.total) >= 0.005 \
            and abs(sum(price) - receipt.total) < 0.005:
        return price
    return extended


# ---------------------------------------------------------------------------------------------------------
# Fonts and rendering


@dataclass
class Fonts:
    path: str
    raqm: bool
    engine: Any
    bold_index: int | None
    _cache: dict = field(default_factory=dict)

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key not in self._cache:
            if self.path == "":
                self._cache[key] = ImageFont.load_default(size)
            else:
                index = self.bold_index if bold and self.bold_index is not None else 0
                self._cache[key] = ImageFont.truetype(self.path, size, index=index, layout_engine=self.engine)
        return self._cache[key]


def load_fonts(path: str | None = None, engine: Any = None) -> Fonts:
    """Load the receipt font once. RAQM is used when Pillow can load it (needed to shape Hindi)."""
    path = path or os.environ.get("RECEIPT_FONT") or DEFAULT_FONT
    raqm = features.check("raqm")
    if engine is None:
        engine = ImageFont.Layout.RAQM if raqm else ImageFont.Layout.BASIC
    raqm = raqm and engine == ImageFont.Layout.RAQM  # Pillow falls back to basic layout without libraqm
    if not Path(path).is_file():
        log(f"font {path} not found; using Pillow's default font (no Devanagari)")
        fonts = Fonts("", raqm, engine, None)
    else:
        bold_index = None
        if path.lower().endswith(".ttc"):
            try:
                if "bold" in ImageFont.truetype(path, 10, index=1).getname()[1].lower():
                    bold_index = 1
            except OSError:
                pass
        fonts = Fonts(path, raqm, engine, bold_index)
    for size, bold in ((TITLE_PX, True), (BODY_PX, False), (BODY_PX, True), (LABEL_PX, False), (SMALL_PX, False)):
        fonts.get(size, bold)
    return fonts


def _fits_before(word: str, i: int) -> bool:
    """A character break at i may not split a vowel sign or mark from its letter, or a conjunct."""
    return not (unicodedata.category(word[i]).startswith("M") or word[i - 1] == "\u094d")


def wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """Greedy word wrap by measured (shaped) width at ordinary spaces; a word wider than the line is broken
    between letters."""
    lines: list[str] = []
    line = ""
    for word in filter(None, re.split(r"[ \t\r\n]+", text)):  # not str.split(), which also splits at NBSP
        candidate = f"{line} {word}" if line else word
        if font.getlength(candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        while font.getlength(word) > width:
            cut = 1
            for i in range(1, len(word)):
                if not _fits_before(word, i):
                    continue
                if font.getlength(word[:i]) > width:
                    break
                cut = i
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    lines.append(line)
    return lines


class Sheet:
    """Lays the receipt out top to bottom, then draws it on an image of exactly the needed height."""

    def __init__(self) -> None:
        self.y = 10
        self.ops: list[tuple] = []
        self.inner = WIDTH - 2 * MARGIN

    @staticmethod
    def line_height(font: ImageFont.FreeTypeFont) -> int:
        ascent, descent = font.getmetrics()
        return ascent + descent

    def gap(self, px: int) -> None:
        self.y += px

    def text(self, text: str, font: ImageFont.FreeTypeFont, align: str = "left") -> None:
        x, anchor = {"left": (MARGIN, "la"), "center": (WIDTH // 2, "ma"), "right": (WIDTH - MARGIN, "ra")}[align]
        for line in wrap(text, font, self.inner):
            self.ops.append(("text", (x, self.y), line, font, anchor))
            self.y += self.line_height(font)

    def row(self, left: str, right: str, font: ImageFont.FreeTypeFont) -> None:
        """left wrapped over the full width, right aligned on its last line (or on a line of its own)."""
        lines = wrap(left, font, self.inner)
        for line in lines[:-1]:
            self.ops.append(("text", (MARGIN, self.y), line, font, "la"))
            self.y += self.line_height(font)
        last = lines[-1]
        self.ops.append(("text", (MARGIN, self.y), last, font, "la"))
        if font.getlength(last) + 16 + font.getlength(right) > self.inner:
            self.y += self.line_height(font)
        self.ops.append(("text", (WIDTH - MARGIN, self.y), right, font, "ra"))
        self.y += self.line_height(font)

    def rule(self, thickness: int = 3) -> None:
        self.ops.append(("rect", (MARGIN, self.y, WIDTH - MARGIN - 1, self.y + thickness - 1)))
        self.y += thickness

    def image(self, img: Image.Image) -> None:
        self.ops.append(("image", img, ((WIDTH - img.width) // 2, self.y)))
        self.y += img.height

    def render(self, bottom: int = 12) -> Image.Image:
        img = Image.new("L", (WIDTH, self.y + bottom), 255)
        draw = ImageDraw.Draw(img)
        for op in self.ops:
            if op[0] == "text":
                _, xy, text, font, anchor = op
                draw.text(xy, text, font=font, fill=0, anchor=anchor)
            elif op[0] == "rect":
                draw.rectangle(op[1], fill=0)
            else:
                img.paste(op[1], op[2])
        return img.convert("1", dither=Image.Dither.NONE)


def qr_image(data: str, max_px: int) -> Image.Image:
    """The QR code at up to QR_MAX_BOX dots per module, never wider than max_px."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    box = max(1, min(QR_MAX_BOX, max_px // n))
    img = Image.new("L", (n, n), 255)
    img.putdata([0 if dark else 255 for row in matrix for dark in row])
    return img.resize((n * box, n * box), Image.Resampling.NEAREST)


def render_receipt(receipt: Receipt | dict, fonts: Fonts) -> Image.Image:
    """The whole receipt as a 1-bit image, 384 dots wide."""
    r = receipt if isinstance(receipt, Receipt) else Receipt.model_validate(receipt)
    lang = r.lang if r.lang in TEXT else "en"
    t = TEXT[lang]
    title, body, body_bold = fonts.get(TITLE_PX, True), fonts.get(BODY_PX), fonts.get(BODY_PX, True)
    label, small = fonts.get(LABEL_PX), fonts.get(SMALL_PX)

    s = Sheet()
    store = merchant_name(r.merchant)
    if store:
        s.text(store, title, "center")
    s.gap(12)
    amounts = line_amounts(r)
    for item, amount in zip(r.items, amounts):
        s.row(f"{max(1, item.qty or 1)} x {(item.name or '').strip()}", money(amount), body)
        s.gap(8)
    s.gap(4)
    s.rule()
    s.gap(10)
    s.row(t["total"], money(r.total if r.total is not None else sum(amounts)), body_bold)
    s.gap(12)
    if r.bill is not None:  # a bill: who was paid and which account, and nothing to pick up
        s.text(t["paid_to"].format(store=store or "the biller"), body_bold)
        ref = str((r.bill or {}).get("account_ref") or "").strip()
        if ref:
            s.text(t["account"].format(ref=ref), body)
    elif r.pickup != "":  # "" means nothing to pick up; None is an older receipt, picked up after 3 pm
        s.text(pickup_line(r.pickup, lang), body)
    code = "".join(ch for ch in (r.pickup_code or "") if ch.isalnum()) if r.bill is None else ""
    if code:
        s.gap(8)
        s.text(t["code"], label, "center")
        s.text(" ".join(code), title, "center")  # large, digit by digit
    if r.savings and r.savings > 0:  # only real savings are printed
        s.gap(8)
        s.text(t["saved"].format(amount=money(r.savings)), body_bold)
    if r.loyalty_points and r.loyalty_points > 0:
        s.text(t["points"].format(n=r.loyalty_points), label)
    s.gap(16)
    if r.order_id:
        s.text(t["order"].format(id=r.order_id), small)
    if r.decision_id:
        s.text(t["decision"].format(id=r.decision_id), small)
    s.text(time_line(r.paid_at, lang), small)
    s.gap(6)
    s.text(t["sandbox"], small)
    url = (r.session_url or "").strip()
    if url:
        try:
            code = qr_image(url, WIDTH - 2 * MARGIN)
        except Exception as exc:  # data too long for any QR version
            log(f"QR code skipped: {exc!r}")
        else:
            s.gap(24)
            s.text(t["scan"], label, "center")
            s.gap(16)
            s.image(code)
    return s.render(bottom=24)


def png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------------
# The Windows print queue

JOB_FAILURES = (
    ("JOB_STATUS_ERROR", "error"),
    ("JOB_STATUS_OFFLINE", "printer offline"),
    ("JOB_STATUS_PAPEROUT", "out of paper"),
    ("JOB_STATUS_BLOCKED_DEVQ", "blocked"),
    ("JOB_STATUS_USER_INTERVENTION", "needs attention"),
)
PRINTER_STATES = (
    ("PRINTER_STATUS_PAUSED", "queue paused"),
    ("PRINTER_STATUS_OFFLINE", "offline"),
    ("PRINTER_STATUS_ERROR", "error"),
    ("PRINTER_STATUS_PAPER_OUT", "out of paper"),
    ("PRINTER_STATUS_PAPER_JAM", "paper jam"),
    ("PRINTER_STATUS_DOOR_OPEN", "cover open"),
    ("PRINTER_STATUS_NOT_AVAILABLE", "not available"),
    ("PRINTER_STATUS_USER_INTERVENTION", "needs attention"),
)
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x400  # "Use printer offline" is ticked


@dataclass
class Backend:
    win32print: Any = None
    printer_cls: Any = None  # escpos.printer.Win32Raw
    error: str | None = None


def load_backend() -> Backend:
    try:
        import win32print
    except ImportError:
        return Backend(error="pywin32 is not installed (printing needs Windows)")
    try:
        from escpos.printer import Win32Raw
    except ImportError as exc:
        return Backend(win32print, error=f"python-escpos is not installed ({exc})")
    return Backend(win32print, Win32Raw)


def list_queues(w32: Any) -> list[str]:
    # The same enumeration escpos's Win32Raw checks the name against before it opens a job.
    return [p["pPrinterName"] for p in w32.EnumPrinters(w32.PRINTER_ENUM_NAME, "", 4)]


def _delete_job(w32: Any, handle: Any, job_id: int) -> None:
    try:
        w32.SetJob(handle, job_id, 0, None, w32.JOB_CONTROL_DELETE)
    except Exception:  # already gone
        pass


def _printer_state(w32: Any, handle: Any) -> str:
    try:
        info = w32.GetPrinter(handle, 2)
    except Exception:
        return ""
    states = [text for name, text in PRINTER_STATES if info.get("Status", 0) & getattr(w32, name, 0)]
    if info.get("Attributes", 0) & PRINTER_ATTRIBUTE_WORK_OFFLINE:
        states.append("set to use the printer offline")
    return f" (printer: {', '.join(states)})" if states else ""


def watch_job(w32: Any, printer_name: str, job_id: int, timeout: float = 4.0,
              interval: float = 0.2) -> tuple[bool, str]:
    """Follow a spooled job until it leaves the queue (printed) or fails; a failed job is deleted."""
    handle = w32.OpenPrinter(printer_name)
    try:
        deadline = time.monotonic() + timeout
        done = getattr(w32, "JOB_STATUS_PRINTED", 0x80) | getattr(w32, "JOB_STATUS_COMPLETE", 0x1000)
        while True:
            job = next((j for j in w32.EnumJobs(handle, 0, 999, 1) if j.get("JobId") == job_id), None)
            if job is None:
                return True, "printed"
            status = int(job.get("Status") or 0)
            problems = [text for name, text in JOB_FAILURES if status & getattr(w32, name)]
            if problems:
                detail = f" ({job['pStatus']})" if job.get("pStatus") else ""
                reason = f"the printer reported {', '.join(problems)}{detail}{_printer_state(w32, handle)}"
                _delete_job(w32, handle, job_id)
                return False, reason
            if status & done:  # kept in the queue after printing ("Keep printed documents")
                return True, "printed"
            if time.monotonic() >= deadline:
                reason = f"the job was still queued after {timeout:g} s{_printer_state(w32, handle)}"
                _delete_job(w32, handle, job_id)
                return False, reason
            time.sleep(interval)
    finally:
        w32.ClosePrinter(handle)


def send_receipt(backend: Backend, printer_name: str, img: Image.Image, job_name: str,
                 timeout: float, interval: float) -> dict:
    """Send the image as one RAW ESC/POS job, then watch it. Never raises."""
    w32 = backend.win32print
    job_id = None
    p = None
    try:
        p = backend.printer_cls(printer_name, profile=PROFILE)
        p.open(job_name=job_name)
        job_id = p.current_job
        p.hw("INIT")
        p.image(img)  # GS v 0 raster, split into 960-dot bands by escpos
        p.ln(4)  # room to tear: most POS-58 printers have no cutter
        p.close()
    except Exception as exc:
        if job_id:
            try:
                handle = w32.OpenPrinter(printer_name)
                _delete_job(w32, handle, job_id)
                w32.ClosePrinter(handle)
            except Exception:
                pass
        if p is not None:
            try:
                p.close()
            except Exception:
                pass
        return {"ok": False, "reason": f"could not send the job: {type(exc).__name__}: {exc}"}
    try:
        ok, reason = watch_job(w32, printer_name, job_id, timeout, interval)
    except Exception as exc:
        return {"ok": False, "reason": f"could not follow job {job_id}: {type(exc).__name__}: {exc}"}
    if ok:
        return {"ok": True, "via": "printer", "job_id": job_id}
    return {"ok": False, "reason": reason, "job_id": job_id}


# ---------------------------------------------------------------------------------------------------------
# App

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def safe_name(order_id: str | None) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", order_id or "").strip("_")[:64]
    if not stem:
        stem = time.strftime("receipt_%Y%m%d_%H%M%S")
    if stem.upper() in _RESERVED:
        stem = f"_{stem}"
    return stem


def shown_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Settings:
    printer_name: str = "POS58"
    receipts_dir: Path = ROOT / "sessions" / "receipts"
    cached_dir: Path = ROOT / "sessions" / "cached"
    font_path: str | None = None
    job_timeout_s: float = 4.0  # how long a job may sit in the queue
    poll_s: float = 0.2
    answer_within_s: float = 4.8  # /print always answers before this
    pdf_fallback: bool = True  # no printer: the PDF is the receipt (answered as via "pdf")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(printer_name=os.environ.get("PRINTER_NAME", "").strip() or "POS58",
                   pdf_fallback=os.environ.get("PRINTER_PDF", "1").strip() != "0")

    @property
    def pdf_only(self) -> bool:
        return self.printer_name.lower() == "pdf"


PAPER_DOTS = 463  # 58 mm at 203 dpi: the receipt image centred on a page as wide as the paper
PAGE_MARGIN = 24


def save_pdf(img: Image.Image, path: Path) -> None:
    """The printed image on a 58 mm page, 1-bit so the PDF is lossless (CCITT) and small (about 10 KB)."""
    bw = img.convert("1")
    page = Image.new("1", (max(PAPER_DOTS, bw.width), bw.height + 2 * PAGE_MARGIN), 1)
    page.paste(bw, ((page.width - bw.width) // 2, PAGE_MARGIN))
    tmp = path.with_suffix(".pdf.tmp")
    page.save(tmp, "PDF", resolution=203, title="Receipt")
    os.replace(tmp, path)


RECEIPT_FILE = re.compile(r"^[A-Za-z0-9_-]{1,70}\.(png|pdf)$")

WARMUP = {
    "merchant": "Corner Market", "items": [{"name": "Honey Wheat Bread", "qty": 1, "price": 3.49}], "total": 3.49,
    "pickup": "after 3 pm", "order_id": "warmup", "decision_id": "warmup", "paid_at": 0,
    "session_url": "https://example.com/s/warmup",
}


def create_app(settings: Settings | None = None, backend: Backend | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Any] = {}
    render_lock = threading.Lock()  # one FreeType face is shared; render one receipt at a time

    def render(receipt: Receipt | dict) -> Image.Image:
        with render_lock:
            return render_receipt(receipt, state["fonts"])

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state["fonts"] = load_fonts(settings.font_path)
        state["backend"] = backend or load_backend()
        timings = []
        for lang in ("hi", "es", "en"):
            t0 = time.perf_counter()
            render({**WARMUP, "lang": lang})
            timings.append((time.perf_counter() - t0) * 1000)
        fonts, be = state["fonts"], state["backend"]
        queues = _queues(be)
        log(f"font {fonts.path or 'Pillow default'} raqm={fonts.raqm}; receipt render {timings[0]:.0f} ms cold, "
            f"{timings[-1]:.0f} ms warm")
        if be.error:
            log(f"printing unavailable: {be.error}")
        else:
            found = settings.printer_name in queues
            log(f"queue {settings.printer_name!r} {'found' if found else 'NOT FOUND'}; installed: {queues}")
        yield

    def _queues(be: Backend) -> list[str]:
        if be.error:
            return []
        try:
            return list_queues(be.win32print)
        except Exception as exc:
            log(f"could not list printers: {exc!r}")
            return []

    app = FastAPI(title="Chaperone receipt printer", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        queues = _queues(state["backend"])
        return {"ok": True, "printer": settings.printer_name, "found": settings.printer_name in queues,
                "pdf_fallback": settings.pdf_fallback or settings.pdf_only,
                "raqm": state["fonts"].raqm, "font": state["fonts"].path or "Pillow default", "queues": queues}

    def print_job(receipt: Receipt, dry_run: bool, png_path: Path) -> dict:
        t0 = time.perf_counter()
        try:
            img = render(receipt)
        except Exception as exc:
            return {"ok": False, "reason": f"could not render the receipt: {type(exc).__name__}: {exc}"}
        render_ms = (time.perf_counter() - t0) * 1000
        try:
            png_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(png_path, "PNG")
            save_pdf(img, png_path.with_suffix(".pdf"))
        except OSError as exc:
            log(f"could not save {png_path}: {exc!r}")
        log(f"rendered {png_path.name} ({img.height} dots) in {render_ms:.0f} ms")
        if dry_run:
            return {"ok": False, "reason": "dry run: not sent to the printer"}
        if receipt.lang == "hi" and not state["fonts"].raqm:
            return {"ok": False, "reason": "Hindi needs Pillow's RAQM layout engine, which is not available"}

        def as_pdf(why: str) -> dict:
            # No printer: the saved PDF is the receipt, shown on the station's screen.
            if not settings.pdf_fallback:
                return {"ok": False, "reason": why}
            if not png_path.with_suffix(".pdf").is_file():
                return {"ok": False, "reason": f"{why}; the PDF could not be saved either"}
            log(f"{png_path.stem}: PDF receipt ({why})")
            return {"ok": True, "via": "pdf", "reason": why}

        if settings.pdf_only:
            return as_pdf("PRINTER_NAME=pdf")
        be = state["backend"]
        if be.error:
            return as_pdf(be.error)
        try:
            queues = list_queues(be.win32print)
        except Exception as exc:
            return as_pdf(f"could not list printers: {type(exc).__name__}: {exc}")
        if settings.printer_name not in queues:
            return as_pdf(f"no printer queue named {settings.printer_name!r} (installed: {', '.join(queues) or 'none'})")
        out = send_receipt(be, settings.printer_name, img, f"Chaperone receipt {png_path.stem}",
                           settings.job_timeout_s, settings.poll_s)
        log(f"job {out.get('job_id')}: {'printed' if out['ok'] else out['reason']}")
        return out

    @app.post("/print")
    async def print_receipt(receipt: Receipt, dry_run: bool = False) -> dict:
        t0 = time.perf_counter()
        png_path = settings.receipts_dir / f"{safe_name(receipt.order_id)}.png"
        try:
            out = await asyncio.wait_for(asyncio.to_thread(print_job, receipt, dry_run, png_path),
                                         timeout=settings.answer_within_s)
        except asyncio.TimeoutError:  # the job is still watched, and deleted, in the background
            out = {"ok": False, "reason": f"no answer from the printer within {settings.answer_within_s:g} s"}
        except Exception as exc:
            out = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        out["ms"] = int((time.perf_counter() - t0) * 1000)
        if png_path.is_file():
            out["png"] = shown_path(png_path)
            out["png_url"] = f"/receipts/{png_path.name}"
        if png_path.with_suffix(".pdf").is_file():
            out["pdf_url"] = f"/receipts/{png_path.stem}.pdf"
        return out

    @app.get("/receipts/{name}")
    def receipt_file(name: str):
        if not RECEIPT_FILE.match(name) or not (settings.receipts_dir / name).is_file():
            return JSONResponse({"error": "no such receipt"}, status_code=404)
        media = "application/pdf" if name.endswith(".pdf") else "image/png"
        # inline: the station opens the PDF in a browser tab rather than downloading it
        return FileResponse(settings.receipts_dir / name, media_type=media, filename=name,
                            content_disposition_type="inline", headers={"Cache-Control": "no-cache"})

    @app.post("/render")
    async def render_png(receipt: Receipt) -> Response:
        img = await asyncio.to_thread(render, receipt)
        return Response(png_bytes(img), media_type="image/png")

    # Recorded voice sessions, replayed by the station when the network is down.

    def cached_path(lang: str) -> Path:
        return settings.cached_dir / f"{lang}.json"

    def bad_lang(lang: str) -> JSONResponse:
        return JSONResponse({"error": f"unknown language {lang!r}; use es, hi or en"}, status_code=400)

    @app.get("/cached")
    def cached_index() -> dict:
        return {lang: cached_path(lang).is_file() for lang in LANGS}

    @app.get("/cached/{lang}")
    def cached_get(lang: str):
        if lang not in LANGS:
            return bad_lang(lang)
        path = cached_path(lang)
        if not path.is_file():
            return JSONResponse({"error": f"no cached session for {lang}"}, status_code=404)
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})

    @app.put("/cached/{lang}")
    async def cached_put(lang: str, request: Request):
        if lang not in LANGS:
            return bad_lang(lang)
        too_big = JSONResponse({"error": "session too large (limit 60 MB)"}, status_code=413)
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > CACHED_LIMIT:
            return too_big
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > CACHED_LIMIT:
                return too_big
        try:
            parsed = await asyncio.to_thread(json.loads, bytes(body))
        except (ValueError, RecursionError):
            return JSONResponse({"error": "body is not valid JSON"}, status_code=400)
        if not isinstance(parsed, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        path = cached_path(lang)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_bytes(body)
            os.replace(tmp, path)

        await asyncio.to_thread(write)
        log(f"cached session {lang}: {len(body)} bytes")
        return {"ok": True, "lang": lang, "bytes": len(body)}

    return app


app = create_app()
