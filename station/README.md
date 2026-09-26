# Station receipt helper

`station/printer.py` renders the paid receipt as a 58 mm thermal receipt, prints it when a POS-58 class printer is attached to the station laptop, and otherwise saves it as a PDF that the station shows on screen. It also stores the recorded voice sessions the station replays offline. It listens on `127.0.0.1:8004`; the station page calls it through its dev-server proxy as `/svc/printer`.

The whole receipt, QR code included, is drawn as one 384-dot-wide black-and-white image (Nirmala UI, RAQM shaping for Hindi) and sent as a RAW ESC/POS job through the Windows print queue. `POST /print` answers within 5 s: `{"ok": true, "via": "printer", "job_id", "ms"}`, or `{"ok": false, "reason", "ms", "png"}` when the queue is missing, the printer reports a problem, or the job is still queued after 4 s (that job is deleted). Every receipt is saved to `sessions/receipts/<order_id>.png` and `<order_id>.pdf` (the same image on a 58 mm page, lossless, about 10 KB), served at `GET /receipts/<name>`.

**No printer (the default on a laptop without one).** When `PRINTER_NAME=pdf`, or there is no queue with that name, `POST /print` answers `{"ok": true, "via": "pdf", "reason", "png_url", "pdf_url"}` at once: the station shows the rendered slip beside its large-type receipt with an **Open PDF** button, and logs the receipt as shown on screen. `PRINTER_PDF=0` turns this off, so a missing queue is reported as a failure.

## Run

From the repo root, with the root requirements already in `.venv`:

```bash
.venv/Scripts/python -m pip install -r station/requirements.txt
.venv/Scripts/python -m pip show escpos     # must say "not found"; the unrelated escpos package shadows python-escpos
.venv/Scripts/python -c "from PIL import features; print(features.check('raqm'))"   # True, or Hindi receipts are not printed
.venv/Scripts/python -m uvicorn station.printer:app --host 127.0.0.1 --port 8004
```

`PRINTER_NAME` (default `POS58`; `pdf` never prints) names the Windows print queue. `RECEIPT_FONT` overrides `C:\Windows\Fonts\Nirmala.ttc`. RAQM needs `libfribidi-0.dll` on `PATH` (the GTK3 runtime provides it).

## Printer setup (Windows, only with a printer)

1. Plug the printer in and install the vendor POS-58 driver. Without one: Settings, Bluetooth & devices, Printers & scanners, Add device, "Add manually", "Add a local printer or network printer with manual settings", existing port `USB001` (the printer's USB port), manufacturer "Generic", printer "Generic / Text Only".
2. Name the queue `POS58` in PowerShell: `Get-Printer` to see what it installed as, then `Rename-Printer -Name "<installed name>" -NewName "POS58"`. A second USB port can create a "(Copy 1)" queue; rename the one that prints.
3. Print a Windows test page from the printer's properties.
4. Set the paper size to 58 mm (roll) in the printer's preferences.

## Check

```bash
curl http://127.0.0.1:8004/health
# {"ok":true,"printer":"POS58","found":true,"raqm":true,"font":"C:\\Windows\\Fonts\\Nirmala.ttc","queues":[...]}

curl -X POST "http://127.0.0.1:8004/print?dry_run=1" -H "content-type: application/json" \
  -d '{"merchant":"Corner Market","items":[{"name":"Honey Wheat Bread","qty":1,"price":3.49}],"total":3.49,"order_id":"o_check","decision_id":"d_check","session_url":"https://example.com/s/check","lang":"en"}'
# {"ok":false,"reason":"dry run: not sent to the printer","ms":...,"png":"sessions/receipts/o_check.png"}
```

Open the PNG to check the layout, then repeat without `?dry_run=1` to print. `POST /render` with the same body returns the PNG directly.

Recorded voice sessions: `PUT /cached/{es|hi|en}` with a JSON object (up to 60 MB) stores `sessions/cached/<lang>.json`; `GET /cached/<lang>` returns it and `GET /cached` lists which exist.

Tests: `.venv/Scripts/python -m pytest -q station` (no printer needed).
