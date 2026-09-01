"""GoldenDict external "program" dictionary backed by online translators.

GoldenDict (Edit -> Dictionaries -> Programs) runs an external command,
substitutes the looked-up word for %GDWORD%, and renders the program's
stdout. Configure this script as an "Html" type program, e.g.:

    translator "%GDWORD%"

or, if the console script is not on PATH:

    /path/to/.venv/bin/python -m translator "%GDWORD%"

Behaviour:
  * Chinese text (contains CJK characters) is translated to English.
  * Anything else is translated to Chinese (Simplified).
Override the languages with the GD_SRC / GD_TGT environment variables
(use "auto" for automatic source detection).

Reliability:
  * Successful lookups are cached on disk, so repeat lookups never hit the
    network (GD_NO_CACHE / GD_CACHE_TTL / GD_CACHE_DIR).
  * Several providers are tried in turn (GD_PROVIDERS), because the free
    Google endpoints rate-limit shared IP addresses.
  * Every request has a timeout (GD_TIMEOUT) and the whole lookup has a
    deadline (GD_DEADLINE), so a lookup can never hang GoldenDict.
  * Network requests honour a proxy when configured: GD_PROXY (applied to
    both http and https) takes precedence, otherwise the standard
    HTTP_PROXY / HTTPS_PROXY variables are used.
  * Set GD_DEBUG=1 to show which provider answered and why others failed.
"""

from __future__ import annotations

import html
import os
import sys
import time

from . import _cache, _net, _providers
from ._lang import contains_cjk, pick_languages
from ._providers import ProviderError, Translation

__all__ = ["translate", "main"]

#: Google rejects anything longer; truncate instead of failing the lookup.
MAX_INPUT_CHARS = 4800

#: Raw network errors can be paragraphs long; keep the popup readable.
MAX_DETAIL_CHARS = 240

_TRUTHY = {"1", "true", "yes", "on"}


class TranslationFailed(RuntimeError):
    """No provider could translate the text."""


def _debug_enabled() -> bool:
    return os.environ.get("GD_DEBUG", "").strip().lower() in _TRUTHY


def _configure_streams() -> None:
    """Force UTF-8 on stdio so CJK cannot raise UnicodeEncodeError.

    GoldenDict inherits a legacy code page on Windows, which used to make
    lookups fail with "'charmap' codec can't encode characters". Streams can
    also be missing entirely (pythonw.exe), hence the guards.
    """
    for name in ("stdout", "stdin"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


#: Characters that shells and pipes like to prepend but Google should not see.
_INVISIBLE = str.maketrans(
    {
        "\ufeff": None,  # byte-order mark (PowerShell pipes love these)
        "\u200b": None,  # zero-width space
        "\u200e": None,  # left-to-right mark
        "\u200f": None,  # right-to-left mark
        "\u00a0": " ",  # non-breaking space
    }
)


def _normalise(word: str) -> str:
    """Trim and bound the incoming word so providers cannot reject it."""
    word = (word or "").translate(_INVISIBLE).strip()
    if len(word) > MAX_INPUT_CHARS:
        word = word[:MAX_INPUT_CHARS]
    return word


def _provider_order() -> tuple[str, ...]:
    """Provider names to try, from ``GD_PROVIDERS`` or the default order."""
    raw = os.environ.get("GD_PROVIDERS")
    if not raw:
        return _providers.DEFAULT_ORDER
    names = tuple(name.strip() for name in raw.split(",") if name.strip())
    return names or _providers.DEFAULT_ORDER


def _fetch(word: str, source: str, target: str) -> tuple[Translation, list[str]]:
    """Try each provider in order until one succeeds.

    Returns the translation plus the diagnostics collected on the way, and
    raises :class:`TranslationFailed` when every provider failed.
    """
    deadline = time.monotonic() + _net.deadline_seconds()
    connect_timeout, read_timeout = _net.request_timeout()
    problems: list[str] = []
    session = _net.build_session()
    try:
        for name in _provider_order():
            provider = _providers.REGISTRY.get(name)
            if provider is None:
                problems.append(f"{name}: unknown provider")
                continue

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                problems.append("deadline exceeded before all providers were tried")
                break
            # Shrink the per-request timeout to fit the remaining budget so the
            # whole lookup stays close to GD_DEADLINE. Halved because the
            # session may retry a transient failure once.
            timeout = (
                min(connect_timeout, remaining),
                max(1.0, min(read_timeout, remaining / 2)),
            )

            try:
                return provider(word, source, target, session, timeout), problems
            except ProviderError as exc:
                problems.append(str(exc))
            except Exception as exc:  # noqa: BLE001 - network stacks raise widely
                problems.append(f"{name}: {type(exc).__name__}: {exc}")
    finally:
        session.close()

    raise TranslationFailed("; ".join(problems) or "no providers configured")


def _render_html(
    word: str,
    translation: str,
    source: str,
    target: str,
    note: str = "",
) -> str:
    """Wrap the translation in minimal, readable HTML for GoldenDict."""
    translation_e = html.escape(translation).replace("\n", "<br>")
    suffix = f" &middot; {html.escape(note)}" if note else ""
    return (
        '<div style="font-family: sans-serif; line-height: 1.5;">'
        '<div style="color:#888; font-size: 0.85em;">'
        f"{html.escape(source)} &rarr; {html.escape(target)}{suffix}</div>"
        f'<div style="font-weight:bold; margin: 2px 0;">{html.escape(word)}</div>'
        f'<div style="font-size: 1.1em;">{translation_e}</div>'
        "</div>"
    )


def _render_error(message: str, detail: str = "") -> str:
    """Render a failure so GoldenDict shows *something* actionable."""
    if detail and not _debug_enabled() and len(detail) > MAX_DETAIL_CHARS:
        detail = detail[:MAX_DETAIL_CHARS].rstrip() + "… (set GD_DEBUG=1 for details)"
    body = f'<div style="color:#c00; font-family: sans-serif;">{html.escape(message)}'
    if detail:
        body += (
            '<div style="color:#888; font-size: 0.85em; margin-top: 2px;">'
            f"{html.escape(detail)}</div>"
        )
    return body + "</div>"


def translate(word: str) -> str:
    """Translate a word/phrase and return HTML output.

    Never raises: any failure is rendered as HTML so GoldenDict always gets a
    well-formed entry and a non-zero exit code is never needed.
    """
    word = _normalise(word)
    if not word:
        return ""

    source, target = pick_languages(word)
    key = _cache.make_key(word, source, target)

    cached = _cache.get(key)
    if cached is not None:
        return _render_html(
            word,
            cached.text,
            cached.detected or source,
            target,
            note=cached.provider if _debug_enabled() else "",
        )

    started = time.monotonic()
    try:
        result, problems = _fetch(word, source, target)
    except TranslationFailed as exc:
        return _render_error("Translation failed", str(exc))

    _cache.put(key, result)

    note = ""
    if _debug_enabled():
        elapsed_ms = (time.monotonic() - started) * 1000
        note = f"{result.provider} in {elapsed_ms:.0f} ms"
        if problems:
            note += f" (after {len(problems)} failure(s): {'; '.join(problems)})"
    return _render_html(word, result.text, result.detected or source, target, note=note)


def main() -> None:
    """Entry point: read the word, print HTML, and never crash."""
    _configure_streams()

    # GoldenDict passes the word as the first argument (%GDWORD%);
    # fall back to stdin so the script also works in a pipe.
    if len(sys.argv) > 1:
        word = " ".join(sys.argv[1:])
    else:
        try:
            word = sys.stdin.read() if sys.stdin is not None else ""
        except (OSError, UnicodeDecodeError):
            word = ""

    try:
        output = translate(word)
    except Exception as exc:  # noqa: BLE001 - a traceback is useless to GoldenDict
        output = _render_error("Translator error", f"{type(exc).__name__}: {exc}")

    if not output:
        return
    try:
        sys.stdout.write(output + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        # GoldenDict closed the pipe early (e.g. the popup was dismissed).
        pass


# Backwards-compatible aliases for the previous single-module layout.
_contains_cjk = contains_cjk
_pick_languages = pick_languages
_pick_proxies = _net.pick_proxies


if __name__ == "__main__":
    main()
