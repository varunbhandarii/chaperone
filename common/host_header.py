"""X-Chaperone-Host: 1 on every request that changes demo state from the Host's side (reset, mark paid).

A cross-site form or fetch without CORS cannot set a custom header, so a page open in some LAN browser cannot
make these calls. The relay's Host page and reset fan-out send it; see relay/host.py.
"""

from fastapi import HTTPException, Request

NAME = "X-Chaperone-Host"
HEADERS = {NAME: "1"}


def require(request: Request) -> None:
    if request.headers.get(NAME) != "1":
        raise HTTPException(403, f"missing {NAME}: 1")
