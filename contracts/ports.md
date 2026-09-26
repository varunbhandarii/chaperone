# Ports

Frozen for the weekend. A change needs all four people at the table.

| Service | Port | Bind |
|---|---|---|
| relay | 8000 | `0.0.0.0` |
| policy | 8001 | `0.0.0.0` |
| merchant | 8002 | `0.0.0.0` |
| catalog | 8003 | `0.0.0.0` |
| station | 5173 | `0.0.0.0` |
| caregiver | 5175 | tunnel (`ngrok http 5175 --url https://<tunnel-host>`) |
| wall | 5176 | `0.0.0.0` |

LAN reservations: services `192.168.8.10`, station `192.168.8.11`, caregiver phone `192.168.8.20`.

Service endpoints each part calls:

- Catalog for the station: `GET http://192.168.8.10:8003/search?q=`
- Policy for the station and merchant: `POST http://192.168.8.10:8001/checkout`
- Merchant for the signer: `POST http://192.168.8.10:8002/orders`
- Judge for policy: `POST http://192.168.8.10:8001/judge` (score `0.05` when `XAI_API_KEY` is unset and `JUDGE_FAKE=1`)
