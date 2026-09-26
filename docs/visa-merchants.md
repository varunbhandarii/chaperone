# Visa sandbox merchants

Registry: `contracts/merchants.json`. Each storefront makes its Pay by Link on its own Cybersource sandbox account
when `<prefix>MERCHANT_ID`, `<prefix>API_KEY_ID` and `<prefix>SECRET_KEY` are in `.env`; otherwise on the main
account (`VISA_ACCEPTANCE_*`), tagged by the purchase number prefix and the store's name on the line
("Parkside Pharmacy - Lisinopril ..."). `GET :8002/panel` → `storefronts` shows which, live.

Re-run: `python -m merchant.spike_link --stores --no-open` (one real $1.00 link per storefront).

## Sat Sep 26, ~5:50pm: one real link per storefront

| Store | Account | Mode | Purchase number / link id |
|---|---|---|---|
| Corner Market | `…6462` | own account | `CM6AB833BC6B464F` |
| Parkside Pharmacy | `…6462` | main account, tagged `PHARM` | `PHARM6AB833C044D5C7` |
| Main Street Home | `…6462` | main account, tagged `HOME` | `HOME6AB833C0135D30` |
| Peachtree Power | `…6462` | main account, tagged `POWER` | `POWER6AB833C1F6FB12` |

All four on `ebc2test.cybersource.com/ebc2/payByLink/pay/...`.

## Sat Sep 26, ~5:51pm: Parkside and Main Street on their own accounts

Parkside's and Main Street's own keys are in `.env`. Pay by Link check on both: signed
`GET /ipl/v2/payment-links?offset=0&limit=1` → 404 "No payment links found" (authenticated, service on, empty);
creating a link then worked on each.

| Store | Account | Mode | Purchase number / link id |
|---|---|---|---|
| Corner Market | `…6462` | own account | `CM6AB83E35AC19F2` |
| Parkside Pharmacy | `…7937` | **own account** | `PHARM6AB83E386B696E` |
| Main Street Home | `…8309` | **own account** | `HOME6AB83E3A23581D` |
| Peachtree Power | `…6462` | main account, tagged `POWER` (own account pending) | `POWER6AB83E3CE87365` |
 When a teammate's account keys arrive, add them
under `CYBS_PARKSIDE_*`, `CYBS_MAINST_*` or `CYBS_PEACHTREE_*`, restart the merchant, re-run the spike, and
update this table (check Pay by Link first: signed `GET /ipl/v2/payment-links?offset=0&limit=1` → 200).
