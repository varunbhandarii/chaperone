"""Receipt print helper tests. No printer is needed: the Windows print queue is replaced by fakes."""
from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path

import pytest
from escpos.printer import Dummy
from fastapi.testclient import TestClient
from PIL import Image, ImageFont

from station import printer as P

SAMPLE = {
    "merchant": "Corner Market",
    "items": [
        {"name": "Lisinopril 10 mg, 30 tablets (pharmacy pickup)", "qty": 1, "price": 8.0},
        {"name": "Nature's Own Honey Wheat Bread", "qty": 1, "price": 3.49},
    ],
    "total": 11.49,
    "pickup": "after 3 pm",
    "order_id": "o_demo01",
    "decision_id": "d_demo01",
    "paid_at": "2026-09-26T09:15:00Z",
    "session_url": "https://example.ngrok-free.app/s/s_demo",
    "lang": "en",
}
JOB_ID = 42


@pytest.fixture(scope="module")
def fonts() -> P.Fonts:
    return P.load_fonts()


# --- Rendering ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es", "hi"])
def test_render_is_384_wide_1bit_and_grows_with_items(fonts, lang):
    img = P.render_receipt({**SAMPLE, "lang": lang}, fonts)
    assert img.width == 384
    assert img.mode == "1"
    assert img.histogram()[0] > 1000  # there is black ink
    longer = P.render_receipt({**SAMPLE, "lang": lang, "items": SAMPLE["items"] * 3}, fonts)
    assert longer.width == 384
    assert longer.height > img.height


def test_hindi_renders_and_is_shaped(fonts):
    receipt = {**SAMPLE, "lang": "hi"}
    shaped = P.render_receipt(receipt, fonts)  # must not raise, with or without RAQM
    assert shaped.width == 384
    if not fonts.raqm:
        pytest.skip("RAQM is not available here, so Hindi is not shaped")
    unshaped = P.render_receipt(receipt, P.load_fonts(engine=ImageFont.Layout.BASIC))
    assert shaped.tobytes() != unshaped.tobytes()


def test_long_session_url_still_fits(fonts):
    img = P.render_receipt({**SAMPLE, "session_url": "https://example.ngrok-free.app/s/" + "x" * 300}, fonts)
    assert img.width == 384


def test_receipt_without_optional_fields_renders(fonts):
    minimal = {k: v for k, v in SAMPLE.items() if k not in ("pickup", "paid_at", "session_url")}
    full = P.render_receipt(SAMPLE, fonts)
    img = P.render_receipt(minimal, fonts)
    assert img.width == 384
    assert img.height < full.height  # no QR code


def test_the_logo_heads_the_receipt_and_a_missing_logo_is_skipped(fonts, monkeypatch, tmp_path):
    with_logo = P.render_receipt(SAMPLE, fonts)
    assert P.receipt_logo() is not None and P.receipt_logo().width <= P.WIDTH - 2 * P.MARGIN
    monkeypatch.setattr(P, "LOGO_PATH", tmp_path / "missing.png")
    monkeypatch.setattr(P, "_logo_cache", {})
    without = P.render_receipt(SAMPLE, fonts)
    assert without.width == with_logo.width == 384
    assert without.height < with_logo.height


def test_localized_lines():
    assert P.pickup_line(None, "en") == "Pickup after 3 pm"
    assert P.pickup_line("after 3pm", "es") == "Recogida después de las 3 p. m."
    assert P.pickup_line("after 1 pm", "es") == "Recogida después de la 1 p. m."
    assert P.pickup_line("after 3 pm", "hi") == "पिकअप: दोपहर 3 बजे के बाद"
    assert P.pickup_line("tomorrow", "en") == "Pickup: tomorrow"
    assert P.time_line("garbage", "en") == "Paid garbage"
    assert P.time_line(None, "es").startswith("Impreso el ")
    seconds = P.parse_time(1790000000)
    assert seconds == P.parse_time(1790000000000) == P.parse_time("1790000000")
    assert P.merchant_name("corner_market") == "Corner Market"


def test_line_amounts_accept_unit_or_line_prices():
    unit = P.Receipt(items=[{"name": "Ensure", "qty": 5, "price": 9.99}], total=49.95)
    line = P.Receipt(items=[{"name": "Ensure", "qty": 5, "price": 49.95}], total=49.95)
    assert P.line_amounts(unit) == pytest.approx([49.95])
    assert P.line_amounts(line) == pytest.approx([49.95])


# --- Fakes for the Windows print queue -----------------------------------------------------------------------


class FakeWin32Print:
    """The parts of pywin32's win32print the helper uses, with the real constant values."""

    PRINTER_ENUM_NAME = 8
    JOB_CONTROL_DELETE = 5
    JOB_STATUS_PAUSED = 0x1
    JOB_STATUS_ERROR = 0x2
    JOB_STATUS_DELETING = 0x4
    JOB_STATUS_SPOOLING = 0x8
    JOB_STATUS_PRINTING = 0x10
    JOB_STATUS_OFFLINE = 0x20
    JOB_STATUS_PAPEROUT = 0x40
    JOB_STATUS_PRINTED = 0x80
    JOB_STATUS_BLOCKED_DEVQ = 0x200
    JOB_STATUS_USER_INTERVENTION = 0x400
    JOB_STATUS_COMPLETE = 0x1000
    PRINTER_STATUS_PAUSED = 0x1
    PRINTER_STATUS_ERROR = 0x2
    PRINTER_STATUS_PAPER_OUT = 0x10
    PRINTER_STATUS_OFFLINE = 0x80

    def __init__(self, statuses=(), stuck=False, queues=("POS58",), printer_status=0):
        self.script = list(statuses)  # the job's Status on each poll; after that it has left the queue
        self.stuck = stuck  # or it never leaves
        self.queues = list(queues)
        self.printer_status = printer_status
        self.deleted: list[int] = []
        self.polls = 0
        self.open_handles = 0

    def EnumPrinters(self, flags, name, level):
        assert (flags, name, level) == (self.PRINTER_ENUM_NAME, "", 4)
        return tuple({"pPrinterName": q, "pServerName": None, "Attributes": 64} for q in self.queues)

    def OpenPrinter(self, name):
        assert name in self.queues
        self.open_handles += 1
        return object()

    def ClosePrinter(self, handle):
        self.open_handles -= 1

    def EnumJobs(self, handle, first, count, level):
        self.polls += 1
        if JOB_ID in self.deleted:
            return ()
        if self.script:
            status = self.script.pop(0)
        elif self.stuck:
            status = self.JOB_STATUS_PRINTING
        else:
            return ()
        return ({"JobId": JOB_ID, "Status": status, "pStatus": None, "pDocument": "Chaperone receipt"},)

    def SetJob(self, handle, job_id, level, info, command):
        assert (level, info, command) == (0, None, self.JOB_CONTROL_DELETE)
        self.deleted.append(job_id)

    def GetPrinter(self, handle, level):
        return {"Status": self.printer_status, "Attributes": 0}


class FakeWin32Raw(Dummy):
    """escpos's Win32Raw with the queue replaced: records the ESC/POS bytes it would have spooled."""

    last: "FakeWin32Raw | None" = None

    def __init__(self, printer_name, profile=None):
        super().__init__(profile=profile)
        self.printer_name = printer_name
        self.current_job = None
        self.closed = False
        FakeWin32Raw.last = self

    def open(self, job_name="python-escpos", raise_not_found=True):
        self.job_name = job_name
        self.current_job = JOB_ID

    def close(self):
        self.closed = True


# --- The job monitor -----------------------------------------------------------------------------------------


def test_job_that_leaves_the_queue_is_printed():
    w32 = FakeWin32Print(statuses=[FakeWin32Print.JOB_STATUS_SPOOLING, FakeWin32Print.JOB_STATUS_PRINTING])
    assert P.watch_job(w32, "POS58", JOB_ID, timeout=2, interval=0.01) == (True, "printed")
    assert w32.deleted == []
    assert w32.open_handles == 0


def test_job_kept_after_printing_is_printed():
    w32 = FakeWin32Print(statuses=[FakeWin32Print.JOB_STATUS_PRINTED | FakeWin32Print.JOB_STATUS_COMPLETE],
                         stuck=True)
    assert P.watch_job(w32, "POS58", JOB_ID, timeout=2, interval=0.01) == (True, "printed")
    assert w32.deleted == []


@pytest.mark.parametrize("flag, words", [
    ("JOB_STATUS_ERROR", "error"),
    ("JOB_STATUS_PAPEROUT", "out of paper"),
    ("JOB_STATUS_OFFLINE", "offline"),
    ("JOB_STATUS_BLOCKED_DEVQ", "blocked"),
    ("JOB_STATUS_USER_INTERVENTION", "needs attention"),
])
def test_job_with_an_error_status_fails_and_is_deleted(flag, words):
    status = getattr(FakeWin32Print, flag) | FakeWin32Print.JOB_STATUS_PRINTING
    w32 = FakeWin32Print(statuses=[FakeWin32Print.JOB_STATUS_PRINTING, status], stuck=True)
    ok, reason = P.watch_job(w32, "POS58", JOB_ID, timeout=2, interval=0.01)
    assert not ok
    assert words in reason
    assert w32.deleted == [JOB_ID]
    assert w32.open_handles == 0


def test_job_stuck_past_the_timeout_fails_and_is_deleted():
    w32 = FakeWin32Print(stuck=True, printer_status=FakeWin32Print.PRINTER_STATUS_OFFLINE)
    t0 = time.monotonic()
    ok, reason = P.watch_job(w32, "POS58", JOB_ID, timeout=0.1, interval=0.01)
    assert time.monotonic() - t0 < 1
    assert not ok
    assert "still queued" in reason and "offline" in reason
    assert w32.deleted == [JOB_ID]
    assert w32.polls > 2


# --- The app -------------------------------------------------------------------------------------------------


@pytest.fixture
def make_client(tmp_path):
    def make(printer_name="Chaperone test queue that does not exist", backend=None, **settings):
        s = P.Settings(printer_name=printer_name, receipts_dir=tmp_path / "receipts",
                       cached_dir=tmp_path / "cached", **settings)
        return TestClient(P.create_app(s, backend))
    return make


def test_health_answers(make_client):
    with make_client() as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["printer"] == "Chaperone test queue that does not exist"
    assert body["found"] is False
    assert isinstance(body["raqm"], bool)
    assert body["font"]
    assert isinstance(body["queues"], list)


def test_health_finds_the_queue(make_client):
    backend = P.Backend(FakeWin32Print(), FakeWin32Raw)
    with make_client("POS58", backend) as client:
        body = client.get("/health").json()
    assert body["found"] is True
    assert body["queues"] == ["POS58"]


def test_render_returns_a_png(make_client):
    with make_client() as client:
        r = client.post("/render", json={**SAMPLE, "lang": "hi"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    img = Image.open(BytesIO(r.content))
    assert img.width == 384


def test_dry_run_saves_the_png_and_does_not_print(make_client, tmp_path):
    backend = P.Backend(FakeWin32Print(), FakeWin32Raw)
    FakeWin32Raw.last = None
    with make_client("POS58", backend) as client:
        body = client.post("/print?dry_run=1", json=SAMPLE).json()
    assert body["ok"] is False
    assert "dry run" in body["reason"]
    assert isinstance(body["ms"], int)
    assert Path(body["png"]) == tmp_path / "receipts" / "o_demo01.png"
    assert Path(body["png"]).is_file()
    assert FakeWin32Raw.last is None


def test_order_id_is_sanitized(make_client, tmp_path):
    with make_client() as client:
        body = client.post("/print?dry_run=1", json={**SAMPLE, "order_id": "../../CON\\x"}).json()
    saved = Path(body["png"])
    assert saved.parent == tmp_path / "receipts"
    assert saved.is_file()


def test_missing_queue_fails_fast_without_the_pdf_fallback(make_client):
    with make_client(pdf_fallback=False) as client:  # the real backend: pywin32 on Windows, none elsewhere
        t0 = time.perf_counter()
        body = client.post("/print", json=SAMPLE).json()
        elapsed = time.perf_counter() - t0
    assert elapsed < 1
    assert body["ok"] is False
    assert body["reason"]
    assert Path(body["png"]).is_file()


def test_no_printer_makes_the_pdf_the_receipt(make_client, tmp_path):
    with make_client() as client:  # no queue with that name: the PDF stands in for the paper
        t0 = time.perf_counter()
        body = client.post("/print", json=SAMPLE).json()
        elapsed = time.perf_counter() - t0
        assert elapsed < 1
        assert body["ok"] is True and body["via"] == "pdf"
        assert "no printer queue" in body["reason"]
        assert body["png_url"] == "/receipts/o_demo01.png" and body["pdf_url"] == "/receipts/o_demo01.pdf"
        pdf = client.get(body["pdf_url"])
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["content-disposition"].startswith("inline")
        assert pdf.content.startswith(b"%PDF") and b"CCITTFaxDecode" in pdf.content  # lossless 1-bit
        assert b"/MediaBox [ 0 0 164.2" in pdf.content  # 58 mm wide at 203 dpi
        png = client.get(body["png_url"])
        assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert (tmp_path / "receipts" / "o_demo01.pdf").is_file()


def test_printer_name_pdf_always_makes_a_pdf(make_client):
    with make_client(printer_name="pdf") as client:
        body = client.post("/print", json={**SAMPLE, "lang": "hi"}).json()
    assert body["ok"] is True and body["via"] == "pdf"


def test_receipt_files_are_only_saved_receipts(make_client):
    with make_client() as client:
        client.post("/print?dry_run=1", json=SAMPLE)
        assert client.get("/receipts/o_demo01.pdf").status_code == 200
        for bad in ("..%2F..%2F.env", "o_demo01.txt", "missing.pdf", "a.b.pdf"):
            assert client.get(f"/receipts/{bad}").status_code == 404, bad


def test_print_sends_the_image_and_reports_the_job(make_client):
    w32 = FakeWin32Print(statuses=[FakeWin32Print.JOB_STATUS_PRINTING])
    with make_client("POS58", P.Backend(w32, FakeWin32Raw), poll_s=0.01) as client:
        body = client.post("/print", json=SAMPLE).json()
    assert body["ok"] is True
    assert body["via"] == "printer"
    assert body["job_id"] == JOB_ID
    assert body["ms"] < 5000
    sent = FakeWin32Raw.last
    assert sent.closed and sent.job_name == "Chaperone receipt o_demo01"
    assert sent.output.startswith(b"\x1b@")  # ESC @
    assert b"\x1dv0" in sent.output  # GS v 0 raster image
    assert sent.output.endswith(b"\n\n\n\n")
    assert w32.deleted == []


def test_print_reports_a_stuck_job_and_deletes_it(make_client):
    w32 = FakeWin32Print(stuck=True)
    with make_client("POS58", P.Backend(w32, FakeWin32Raw), poll_s=0.01, job_timeout_s=0.1) as client:
        body = client.post("/print", json=SAMPLE).json()
    assert body["ok"] is False
    assert "still queued" in body["reason"]
    assert body["png"]
    assert w32.deleted == [JOB_ID]


def test_print_deletes_a_half_sent_job(make_client):
    class Unplugged(FakeWin32Raw):
        def image(self, *args, **kwargs):
            raise OSError("the device is not connected")

    w32 = FakeWin32Print()
    with make_client("POS58", P.Backend(w32, Unplugged)) as client:
        body = client.post("/print", json=SAMPLE).json()
    assert body["ok"] is False
    assert "not connected" in body["reason"]
    assert w32.deleted == [JOB_ID]


def test_print_answers_even_if_the_printer_hangs(make_client):
    class Hangs(FakeWin32Raw):
        def open(self, *args, **kwargs):
            time.sleep(0.6)
            super().open(*args, **kwargs)

    backend = P.Backend(FakeWin32Print(), Hangs)
    with make_client("POS58", backend, answer_within_s=0.2) as client:
        t0 = time.perf_counter()
        body = client.post("/print", json=SAMPLE).json()
        elapsed = time.perf_counter() - t0
    assert elapsed < 0.5
    assert body["ok"] is False
    assert "no answer" in body["reason"]


def test_hindi_is_not_printed_without_raqm(make_client, monkeypatch):
    real = P.load_fonts
    monkeypatch.setattr(P, "load_fonts", lambda path=None: real(path, engine=ImageFont.Layout.BASIC))
    w32 = FakeWin32Print()
    with make_client("POS58", P.Backend(w32, FakeWin32Raw)) as client:
        assert client.get("/health").json()["raqm"] is False
        body = client.post("/print", json={**SAMPLE, "lang": "hi"}).json()
    assert body["ok"] is False
    assert "RAQM" in body["reason"]
    assert w32.polls == 0


# --- Cached voice sessions -----------------------------------------------------------------------------------


def test_cached_session_round_trip(make_client, tmp_path):
    session = json.dumps({"lang": "es", "events": [{"t": 0, "type": "heard", "text": "Buenos días"}]},
                         ensure_ascii=False).encode()
    with make_client() as client:
        assert client.get("/cached").json() == {"es": False, "hi": False, "en": False}
        r = client.put("/cached/es", content=session, headers={"content-type": "application/json"})
        assert r.status_code == 200
        assert r.json()["bytes"] == len(session)
        got = client.get("/cached/es")
        assert got.status_code == 200
        assert got.content == session
        assert got.headers["content-type"].startswith("application/json")
        assert client.get("/cached").json() == {"es": True, "hi": False, "en": False}
    assert (tmp_path / "cached" / "es.json").read_bytes() == session


def test_cached_session_missing_is_404(make_client):
    with make_client() as client:
        r = client.get("/cached/hi")
    assert r.status_code == 404
    assert r.json() == {"error": "no cached session for hi"}


def test_cached_session_rejects_unknown_language(make_client):
    with make_client() as client:
        assert client.get("/cached/fr").status_code == 400
        assert client.put("/cached/fr", content=b"{}").status_code == 400


@pytest.mark.parametrize("body", [b"[1, 2]", b"\"text\"", b"not json", b""])
def test_cached_session_must_be_a_json_object(make_client, body):
    with make_client() as client:
        r = client.put("/cached/en", content=body)
        assert r.status_code == 400
        assert "error" in r.json()
        assert client.get("/cached").json()["en"] is False


def test_cached_session_size_limit(make_client, monkeypatch):
    monkeypatch.setattr(P, "CACHED_LIMIT", 16)
    with make_client() as client:
        r = client.put("/cached/en", content=json.dumps({"events": "x" * 64}).encode())
    assert r.status_code == 413


def test_savings_points_and_pickup_code_make_the_receipt_longer(fonts):
    plain = P.render_receipt(SAMPLE, fonts)
    extra = P.render_receipt({**SAMPLE, "savings": 0.50, "loyalty_points": 11, "pickup_code": "472"}, fonts)
    assert extra.width == 384 and extra.height > plain.height
    # nothing saved: the savings line is left out, so only the code and points add height
    no_savings = P.render_receipt({**SAMPLE, "savings": 0, "loyalty_points": 11, "pickup_code": "472"}, fonts)
    assert plain.height < no_savings.height < extra.height
    for lang in ("es", "hi"):
        P.render_receipt({**SAMPLE, "lang": lang, "savings": 0.5, "loyalty_points": 3, "pickup_code": "472"}, fonts)


def test_a_bill_says_who_was_paid_and_has_no_pickup_or_code(fonts):
    bill = {**SAMPLE, "merchant": "Peachtree Power", "items": [{"name": "Peachtree Power bill", "qty": 1, "price": 86.4}], "total": 86.4,
            "pickup": "", "pickup_code": "472", "bill": {"account_ref": "…0098"}}
    img = P.render_receipt(bill, fonts)
    assert img.width == 384
    for lang in ("es", "hi"):
        P.render_receipt({**bill, "lang": lang}, fonts)
    assert P.TEXT["en"]["paid_to"].format(store="Peachtree Power") == "Paid to Peachtree Power"
    assert P.TEXT["en"]["points"].format(n=11) == "+11 rewards points"
    # no store is assumed: an empty name prints no title
    assert P.merchant_name("") == ""
    P.render_receipt({**SAMPLE, "merchant": ""}, fonts)
