"""One TLS context for every outgoing httpx client.

httpx builds a new SSL context for each client and reads the CA bundle again: about half a second on Windows,
during which an async service answers nothing else. The relay makes a client per request while the wall and
the Host page poll it, so the context is built once here and passed as `verify`.
"""

import ssl
from functools import cache

import certifi


@cache
def context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())
