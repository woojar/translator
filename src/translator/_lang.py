"""Language detection heuristics and source/target selection."""

from __future__ import annotations

import os

#: Ranges that are good enough to say "this is Chinese/Japanese/Korean text"
#: for the purpose of picking a translation direction.
_CJK_RANGES = (
    ("\u3400", "\u4dbf"),  # CJK Unified Ideographs Extension A
    ("\u4e00", "\u9fff"),  # CJK Unified Ideographs
    ("\uf900", "\ufaff"),  # CJK Compatibility Ideographs
)


def contains_cjk(text: str) -> bool:
    """Return True if the text contains any CJK ideographs."""
    return any(low <= ch <= high for ch in text for low, high in _CJK_RANGES)


def pick_languages(text: str) -> tuple[str, str]:
    """Choose ``(source, target)`` languages for the given text.

    ``GD_SRC`` / ``GD_TGT`` win when set, and are honoured *independently* so
    that setting only one still has an effect. Otherwise Chinese input is
    translated to English and everything else to Simplified Chinese.
    """
    default_target = "en" if contains_cjk(text) else "zh-CN"
    source = (os.environ.get("GD_SRC") or "auto").strip() or "auto"
    target = (os.environ.get("GD_TGT") or default_target).strip() or default_target
    return source, target


def fallback_source(text: str, source: str) -> str:
    """Resolve ``auto`` to a concrete code for providers without detection."""
    if source and source != "auto":
        return source
    return "zh-CN" if contains_cjk(text) else "en"
