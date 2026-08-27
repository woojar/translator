"""GoldenDict external "program" dictionary backed by deep-translator.

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
"""

from __future__ import annotations

import html
import os
import sys

from deep_translator import GoogleTranslator


def _contains_cjk(text: str) -> bool:
    """Return True if the text contains any CJK ideographs."""
    return any(
        "\u4e00" <= ch <= "\u9fff"  # CJK Unified Ideographs
        or "\u3400" <= ch <= "\u4dbf"  # CJK Extension A
        or "\uf900" <= ch <= "\ufaff"  # CJK Compatibility Ideographs
        for ch in text
    )


def _pick_languages(text: str) -> tuple[str, str]:
    """Choose (source, target) languages for the given text."""
    src = os.environ.get("GD_SRC")
    tgt = os.environ.get("GD_TGT")
    if src and tgt:
        return src, tgt
    if _contains_cjk(text):
        return "auto", "en"
    return "auto", "zh-CN"


def _render_html(word: str, translation: str, source: str, target: str) -> str:
    """Wrap the translation in minimal, readable HTML for GoldenDict."""
    word_e = html.escape(word)
    translation_e = html.escape(translation).replace("\n", "<br>")
    return (
        '<div style="font-family: sans-serif; line-height: 1.5;">'
        f'<div style="color:#888; font-size: 0.85em;">'
        f"{html.escape(source)} &rarr; {html.escape(target)}</div>"
        f'<div style="font-weight:bold; margin: 2px 0;">{word_e}</div>'
        f'<div style="font-size: 1.1em;">{translation_e}</div>'
        "</div>"
    )


def translate(word: str) -> str:
    """Translate a word/phrase and return HTML output."""
    word = word.strip()
    if not word:
        return ""

    source, target = _pick_languages(word)
    try:
        result = GoogleTranslator(source=source, target=target).translate(word)
    except Exception as exc:  # noqa: BLE001 - surface any failure in GoldenDict
        return (
            '<div style="color:#c00; font-family: sans-serif;">'
            f"Translation failed: {html.escape(str(exc))}</div>"
        )

    if not result:
        return ""
    return _render_html(word, result, source, target)


def main() -> None:
    # GoldenDict passes the word as the first argument (%GDWORD%);
    # fall back to stdin so the script also works in a pipe.
    if len(sys.argv) > 1:
        word = " ".join(sys.argv[1:])
    else:
        word = sys.stdin.read()

    output = translate(word)
    if output:
        sys.stdout.write(output)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
