"""HTTP plumbing: bounded timeouts, retries, proxies.

Every network call made by this package goes through :func:`build_session`,
which guarantees a connect/read timeout and a small number of retries for
*transient* failures only. Rate limiting (HTTP 429) is deliberately **not**
retried: failing over to the next provider is faster and more likely to
succeed than backing off against the same endpoint.
"""

from __future__ import annotations

import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

#: Connect timeouts are capped: a TCP handshake either happens quickly or not
#: at all. The read timeout is the tunable part (``GD_TIMEOUT``).
CONNECT_TIMEOUT = 3.05
DEFAULT_READ_TIMEOUT = 5.0

#: Upper bound for the whole lookup, across all providers (``GD_DEADLINE``).
DEFAULT_DEADLINE = 12.0

#: Google's mobile endpoint returns a consent/challenge page to unknown
#: clients, so present a browser-ish UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_TRANSIENT_STATUS = (408, 500, 502, 503, 504)


def _positive_float_env(name: str, default: float) -> float:
    """Read a positive float from the environment, ignoring bad values."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def request_timeout() -> tuple[float, float]:
    """Return the ``(connect, read)`` timeout tuple for a single request."""
    read = _positive_float_env("GD_TIMEOUT", DEFAULT_READ_TIMEOUT)
    return (min(CONNECT_TIMEOUT, read), read)


def deadline_seconds() -> float:
    """Return the wall-clock budget for one lookup, across all providers."""
    return _positive_float_env("GD_DEADLINE", DEFAULT_DEADLINE)


def pick_proxies() -> dict[str, str] | None:
    """Build a requests-style proxies dict from the environment.

    ``GD_PROXY`` overrides both protocols. Otherwise the standard
    ``HTTP_PROXY`` / ``HTTPS_PROXY`` variables (upper or lower case) are
    used. Returns ``None`` when no proxy is configured.
    """
    override = os.environ.get("GD_PROXY")
    if override:
        return {"http": override, "https": override}

    http = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not http and not https:
        return None

    proxies: dict[str, str] = {}
    if http:
        proxies["http"] = http
    if https:
        proxies["https"] = https
    return proxies


def build_session(retries: int = 1) -> requests.Session:
    """Return a session that retries transient errors and never hangs."""
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        status_forcelist=_TRANSIENT_STATUS,
        allowed_methods=frozenset({"GET"}),
        backoff_factor=0.4,
        backoff_max=1.5,
        # Google's 429 pages carry no useful Retry-After; obeying one would
        # stall GoldenDict for seconds.
        respect_retry_after_header=False,
        # Let the caller inspect the response instead of raising deep inside
        # urllib3, so a 429 can fail over immediately.
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)

    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    proxies = pick_proxies()
    if proxies:
        session.proxies.update(proxies)
    return session
