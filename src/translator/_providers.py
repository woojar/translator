"""Translation back ends.

Each provider is a callable ``(text, source, target, session, timeout) ->
Translation`` that either returns a non-empty translation or raises
:class:`ProviderError`. They are tried in order by
:func:`translator.translate`, so a single flaky or rate-limited endpoint no
longer means a failed lookup.
"""

from __future__ import annotations

import html
import json
from contextlib import contextmanager
from typing import Callable, Iterator, NamedTuple

import requests
from bs4 import BeautifulSoup

from ._lang import fallback_source

GOOGLE_M_URL = "https://translate.google.com/m"
GOOGLE_JSON_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"

#: MyMemory rejects longer queries outright.
MYMEMORY_MAX_CHARS = 500


class Translation(NamedTuple):
    """A successful translation and where it came from."""

    text: str
    detected: str | None
    provider: str


class ProviderError(RuntimeError):
    """A provider could not produce a translation."""


Provider = Callable[[str, str, str, requests.Session, tuple[float, float]], Translation]


def _check_response(response: requests.Response, provider: str) -> None:
    """Turn HTTP-level failures into readable ProviderErrors."""
    if response.status_code == 429:
        raise ProviderError(f"{provider}: rate limited (HTTP 429)")
    if not response.ok:
        raise ProviderError(f"{provider}: HTTP {response.status_code}")


def google_m(
    text: str,
    source: str,
    target: str,
    session: requests.Session,
    timeout: tuple[float, float],
) -> Translation:
    """Scrape Google's lightweight mobile page (the most reliable in practice)."""
    provider = "google-m"
    response = session.get(
        GOOGLE_M_URL,
        params={"sl": source, "tl": target, "hl": target, "q": text},
        timeout=timeout,
    )
    _check_response(response, provider)

    soup = BeautifulSoup(response.text, "html.parser")
    element = soup.find("div", {"class": "result-container"}) or soup.find(
        "div", {"class": "t0"}
    )
    if element is None:
        # Either Google changed its markup or we got a consent/captcha page.
        raise ProviderError(f"{provider}: unexpected page layout")

    translated = element.get_text(strip=True)
    if not translated:
        raise ProviderError(f"{provider}: empty translation")
    return Translation(translated, None, provider)


def google_json(
    text: str,
    source: str,
    target: str,
    session: requests.Session,
    timeout: tuple[float, float],
) -> Translation:
    """Use the JSON endpoint; it also reports the detected source language."""
    provider = "google-json"
    response = session.get(
        GOOGLE_JSON_URL,
        params={
            "client": "gtx",
            "sl": source,
            "tl": target,
            "dt": "t",
            "ie": "UTF-8",
            "oe": "UTF-8",
            "q": text,
        },
        timeout=timeout,
    )
    _check_response(response, provider)

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProviderError(f"{provider}: malformed response ({exc})") from exc

    if not isinstance(payload, list) or not payload:
        raise ProviderError(f"{provider}: unexpected response shape")

    segments = payload[0]
    if not isinstance(segments, list):
        raise ProviderError(f"{provider}: unexpected response shape")

    parts = [
        segment[0]
        for segment in segments
        if isinstance(segment, list) and segment and isinstance(segment[0], str)
    ]
    translated = "".join(parts).strip()
    if not translated:
        raise ProviderError(f"{provider}: empty translation")

    detected = payload[2] if len(payload) > 2 and isinstance(payload[2], str) else None
    return Translation(translated, detected, provider)


def mymemory(
    text: str,
    source: str,
    target: str,
    session: requests.Session,
    timeout: tuple[float, float],
) -> Translation:
    """Independent fallback service, so a Google-wide block is survivable."""
    provider = "mymemory"
    if len(text) > MYMEMORY_MAX_CHARS:
        raise ProviderError(f"{provider}: text too long")

    # MyMemory has no auto-detection; resolve the direction ourselves.
    resolved = fallback_source(text, source)
    if resolved.lower() == target.lower():
        raise ProviderError(f"{provider}: source and target are identical")

    response = session.get(
        MYMEMORY_URL,
        params={"q": text, "langpair": f"{resolved}|{target}"},
        timeout=timeout,
    )
    _check_response(response, provider)

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProviderError(f"{provider}: malformed response ({exc})") from exc

    data = payload.get("responseData") if isinstance(payload, dict) else None
    translated = (data or {}).get("translatedText") or ""
    status = str(payload.get("responseStatus")) if isinstance(payload, dict) else ""
    if status != "200" or not translated:
        detail = (payload or {}).get("responseDetails") or f"status {status}"
        raise ProviderError(f"{provider}: {detail}")

    return Translation(html.unescape(translated).strip(), resolved, provider)


@contextmanager
def _bounded_timeout(module, timeout: tuple[float, float]) -> Iterator[None]:
    """Force a timeout onto a module that calls ``requests.get`` directly.

    deep-translator issues requests without any timeout, which can hang a
    GoldenDict lookup indefinitely. Swapping the module's ``requests``
    reference for a thin shim fixes that without patching global state.
    """

    class _Shim:
        def __getattr__(self, name):
            return getattr(requests, name)

        def get(self, *args, **kwargs):
            kwargs.setdefault("timeout", timeout)
            return requests.get(*args, **kwargs)

    original = module.requests
    module.requests = _Shim()
    try:
        yield
    finally:
        module.requests = original


def deep_translator_google(
    text: str,
    source: str,
    target: str,
    session: requests.Session,
    timeout: tuple[float, float],
) -> Translation:
    """Opt-in provider that defers to deep-translator's GoogleTranslator."""
    provider = "deep-translator"
    try:
        from deep_translator import GoogleTranslator
        from deep_translator import google as google_module
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ProviderError(f"{provider}: not installed ({exc})") from exc

    try:
        with _bounded_timeout(google_module, timeout):
            result = GoogleTranslator(
                source=source, target=target, proxies=dict(session.proxies) or None
            ).translate(text)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - deep-translator raises broadly
        raise ProviderError(f"{provider}: {exc or type(exc).__name__}") from exc

    # GoogleTranslator.translate() can fall off the end of its own control
    # flow and return None, so treat a falsy result as a failure.
    if not result or not result.strip():
        raise ProviderError(f"{provider}: no translation returned")
    return Translation(result.strip(), None, provider)


#: Registry used by ``GD_PROVIDERS``.
REGISTRY: dict[str, Provider] = {
    "google-m": google_m,
    "google-json": google_json,
    "mymemory": mymemory,
    "deep-translator": deep_translator_google,
}

#: Tried in this order. ``google-m`` first because it is the endpoint that
#: survives shared/corporate egress IPs best; the JSON endpoint is more often
#: rate limited, and MyMemory is an independent last resort.
DEFAULT_ORDER = ("google-m", "google-json", "mymemory")
