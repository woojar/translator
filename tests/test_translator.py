"""Offline tests: every network call is stubbed."""

from __future__ import annotations

import json

import pytest

import translator
from translator import _cache, _lang, _net, _providers
from translator._providers import ProviderError, Translation


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records requests and replays queued responses."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.proxies = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, params, timeout))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


TIMEOUT = (3.05, 5.0)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Keep every test away from the network and the real cache file."""
    for name in (
        "GD_SRC",
        "GD_TGT",
        "GD_PROXY",
        "GD_PROVIDERS",
        "GD_DEBUG",
        "GD_NO_CACHE",
        "GD_CACHE_TTL",
        "GD_TIMEOUT",
        "GD_DEADLINE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GD_CACHE_DIR", str(tmp_path / "cache"))


# --------------------------------------------------------------------------- #
# Language selection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["你好世界", "mixed 中文 text", "漢字"])
def test_contains_cjk_true(text):
    assert _lang.contains_cjk(text)


@pytest.mark.parametrize("text", ["hello", "", "123 !?", "こんにちは"])
def test_contains_cjk_false(text):
    # Kana is deliberately not treated as CJK ideographs.
    assert not _lang.contains_cjk(text)


def test_pick_languages_defaults():
    assert _lang.pick_languages("hello") == ("auto", "zh-CN")
    assert _lang.pick_languages("你好") == ("auto", "en")


def test_pick_languages_honours_single_override(monkeypatch):
    monkeypatch.setenv("GD_TGT", "ja")
    assert _lang.pick_languages("hello") == ("auto", "ja")

    monkeypatch.delenv("GD_TGT")
    monkeypatch.setenv("GD_SRC", "en")
    assert _lang.pick_languages("hello") == ("en", "zh-CN")


def test_pick_languages_ignores_blank_override(monkeypatch):
    monkeypatch.setenv("GD_SRC", "  ")
    monkeypatch.setenv("GD_TGT", "")
    assert _lang.pick_languages("hello") == ("auto", "zh-CN")


def test_fallback_source_resolves_auto():
    assert _lang.fallback_source("hello", "auto") == "en"
    assert _lang.fallback_source("你好", "auto") == "zh-CN"
    assert _lang.fallback_source("hello", "de") == "de"


# --------------------------------------------------------------------------- #
# Network configuration
# --------------------------------------------------------------------------- #
def test_timeout_is_always_bounded(monkeypatch):
    assert _net.request_timeout() == (_net.CONNECT_TIMEOUT, _net.DEFAULT_READ_TIMEOUT)

    monkeypatch.setenv("GD_TIMEOUT", "2")
    assert _net.request_timeout() == (2.0, 2.0)

    for bad in ("garbage", "0", "-5"):
        monkeypatch.setenv("GD_TIMEOUT", bad)
        assert _net.request_timeout()[1] == _net.DEFAULT_READ_TIMEOUT


def test_proxy_precedence(monkeypatch):
    assert _net.pick_proxies() is None

    monkeypatch.setenv("HTTPS_PROXY", "http://corp:8080")
    assert _net.pick_proxies() == {"https": "http://corp:8080"}

    monkeypatch.setenv("GD_PROXY", "http://override:3128")
    assert _net.pick_proxies() == {
        "http": "http://override:3128",
        "https": "http://override:3128",
    }


def test_session_retries_exclude_rate_limiting():
    session = _net.build_session()
    try:
        retry = session.get_adapter("https://example.com").max_retries
        assert 429 not in retry.status_forcelist
        assert 503 in retry.status_forcelist
        assert retry.total == 1
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def test_google_m_parses_result_container():
    html_page = '<html><body><div class="result-container">早上好</div></body></html>'
    session = FakeSession(FakeResponse(text=html_page))
    result = _providers.google_m("good morning", "auto", "zh-CN", session, TIMEOUT)
    assert result == Translation("早上好", None, "google-m")
    assert session.calls[0][1]["q"] == "good morning"


def test_google_m_reports_layout_change():
    session = FakeSession(FakeResponse(text="<html><body>captcha</body></html>"))
    with pytest.raises(ProviderError, match="unexpected page layout"):
        _providers.google_m("hi", "auto", "zh-CN", session, TIMEOUT)


def test_google_m_reports_rate_limit():
    session = FakeSession(FakeResponse(status_code=429, text="Sorry..."))
    with pytest.raises(ProviderError, match="rate limited"):
        _providers.google_m("hi", "auto", "zh-CN", session, TIMEOUT)


def test_google_json_joins_segments_and_detects_source():
    payload = [[["早上", "good", None, None], ["好", "morning"]], None, "en"]
    session = FakeSession(FakeResponse(payload=payload))
    result = _providers.google_json("good morning", "auto", "zh-CN", session, TIMEOUT)
    assert result == Translation("早上好", "en", "google-json")


def test_google_json_rejects_html_error_page():
    session = FakeSession(FakeResponse(text="<html>Sorry...</html>"))
    with pytest.raises(ProviderError, match="malformed response"):
        _providers.google_json("hi", "auto", "zh-CN", session, TIMEOUT)


def test_google_json_rejects_unexpected_shape():
    session = FakeSession(FakeResponse(payload={"error": "nope"}))
    with pytest.raises(ProviderError, match="unexpected response shape"):
        _providers.google_json("hi", "auto", "zh-CN", session, TIMEOUT)


def test_mymemory_unescapes_and_resolves_auto():
    payload = {
        "responseData": {"translatedText": "Tom &amp; Jerry"},
        "responseStatus": 200,
    }
    session = FakeSession(FakeResponse(payload=payload))
    result = _providers.mymemory("汤姆和杰瑞", "auto", "en", session, TIMEOUT)
    assert result.text == "Tom & Jerry"
    assert session.calls[0][1]["langpair"] == "zh-CN|en"


def test_mymemory_surfaces_api_error():
    payload = {
        "responseData": {"translatedText": ""},
        "responseStatus": 403,
        "responseDetails": "INVALID LANGUAGE PAIR",
    }
    session = FakeSession(FakeResponse(payload=payload))
    with pytest.raises(ProviderError, match="INVALID LANGUAGE PAIR"):
        _providers.mymemory("hi", "en", "zh-CN", session, TIMEOUT)


def test_mymemory_rejects_long_text():
    session = FakeSession()
    with pytest.raises(ProviderError, match="too long"):
        _providers.mymemory("x" * 600, "en", "zh-CN", session, TIMEOUT)


def test_deep_translator_none_result_is_an_error(monkeypatch):
    """GoogleTranslator.translate() can return None; that must not leak out."""
    from deep_translator import google as google_module

    class Dummy:
        def __init__(self, **kwargs):
            pass

        def translate(self, text):
            return None

    monkeypatch.setattr(google_module, "GoogleTranslator", Dummy)
    monkeypatch.setattr("deep_translator.GoogleTranslator", Dummy)
    session = FakeSession()
    with pytest.raises(ProviderError, match="no translation returned"):
        _providers.deep_translator_google("hi", "auto", "zh-CN", session, TIMEOUT)


def test_bounded_timeout_restores_module_state():
    from deep_translator import google as google_module

    original = google_module.requests
    with _providers._bounded_timeout(google_module, TIMEOUT):
        assert google_module.requests is not original
    assert google_module.requests is original


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_cache_roundtrip():
    key = _cache.make_key("hello", "auto", "zh-CN")
    assert _cache.get(key) is None

    _cache.put(key, Translation("你好", "en", "google-m"))
    cached = _cache.get(key)
    assert cached is not None
    assert cached.text == "你好"
    assert cached.detected == "en"
    assert "cached" in cached.provider


def test_cache_respects_ttl(monkeypatch):
    key = _cache.make_key("hello", "auto", "zh-CN")
    _cache.put(key, Translation("你好", None, "google-m"))

    fake_now = [_cache.time.time() + 10_000]
    monkeypatch.setattr(_cache.time, "time", lambda: fake_now[0])
    monkeypatch.setenv("GD_CACHE_TTL", "1")
    assert _cache.get(key) is None

    monkeypatch.setenv("GD_CACHE_TTL", "0")  # 0 == never expire
    assert _cache.get(key) is not None


def test_cache_can_be_disabled(monkeypatch):
    monkeypatch.setenv("GD_NO_CACHE", "1")
    key = _cache.make_key("hello", "auto", "zh-CN")
    _cache.put(key, Translation("你好", None, "google-m"))
    assert _cache.get(key) is None


def test_cache_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        _cache, "_connect", lambda: (_ for _ in ()).throw(OSError("disk gone"))
    )
    key = _cache.make_key("hello", "auto", "zh-CN")
    _cache.put(key, Translation("你好", None, "google-m"))  # must not raise
    assert _cache.get(key) is None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _stub_provider(result=None, error=None, calls=None, name="stub"):
    def provider(text, source, target, session, timeout):
        if calls is not None:
            calls.append(name)
        if error is not None:
            raise error
        return result

    return provider


def test_translate_falls_back_to_next_provider(monkeypatch):
    calls = []
    monkeypatch.setitem(
        _providers.REGISTRY,
        "first",
        _stub_provider(error=ProviderError("first: boom"), calls=calls, name="first"),
    )
    monkeypatch.setitem(
        _providers.REGISTRY,
        "second",
        _stub_provider(
            result=Translation("你好", "en", "second"), calls=calls, name="second"
        ),
    )
    monkeypatch.setenv("GD_PROVIDERS", "first,second")

    output = translator.translate("hello")
    assert calls == ["first", "second"]
    assert "你好" in output


def test_translate_survives_unexpected_provider_exception(monkeypatch):
    monkeypatch.setitem(
        _providers.REGISTRY, "boom", _stub_provider(error=RuntimeError("kaboom"))
    )
    monkeypatch.setitem(
        _providers.REGISTRY, "ok", _stub_provider(result=Translation("你好", None, "ok"))
    )
    monkeypatch.setenv("GD_PROVIDERS", "boom,ok")
    assert "你好" in translator.translate("hello")


def test_translate_renders_error_when_all_providers_fail(monkeypatch):
    monkeypatch.setitem(
        _providers.REGISTRY, "bad", _stub_provider(error=ProviderError("bad: nope"))
    )
    monkeypatch.setenv("GD_PROVIDERS", "bad")

    output = translator.translate("hello")
    assert "Translation failed" in output
    assert "bad: nope" in output
    assert output.startswith("<div")


def test_error_detail_is_truncated_unless_debugging(monkeypatch):
    long_error = "x" * 2000
    monkeypatch.setitem(
        _providers.REGISTRY, "bad", _stub_provider(error=ProviderError(long_error))
    )
    monkeypatch.setenv("GD_PROVIDERS", "bad")

    output = translator.translate("hello")
    assert "GD_DEBUG=1" in output
    assert len(output) < 700

    monkeypatch.setenv("GD_DEBUG", "1")
    assert long_error in translator.translate("hello")


def test_translate_uses_cache_on_second_call(monkeypatch):
    calls = []
    monkeypatch.setitem(
        _providers.REGISTRY,
        "once",
        _stub_provider(result=Translation("你好", "en", "once"), calls=calls),
    )
    monkeypatch.setenv("GD_PROVIDERS", "once")

    first = translator.translate("hello")
    second = translator.translate("hello")
    assert len(calls) == 1
    assert "你好" in second
    assert first == second


def test_translate_does_not_cache_failures(monkeypatch):
    calls = []
    monkeypatch.setitem(
        _providers.REGISTRY,
        "flaky",
        _stub_provider(error=ProviderError("flaky: 429"), calls=calls),
    )
    monkeypatch.setenv("GD_PROVIDERS", "flaky")

    translator.translate("hello")
    translator.translate("hello")
    assert len(calls) == 2


def test_translate_stops_at_deadline(monkeypatch):
    calls = []
    monkeypatch.setitem(
        _providers.REGISTRY,
        "slow",
        _stub_provider(error=ProviderError("slow: timeout"), calls=calls, name="slow"),
    )
    monkeypatch.setitem(
        _providers.REGISTRY,
        "never",
        _stub_provider(result=Translation("x", None, "never"), calls=calls, name="never"),
    )
    monkeypatch.setenv("GD_PROVIDERS", "slow,never")

    ticks = iter([0.0, 0.0, 0.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(translator.time, "monotonic", lambda: next(ticks))

    output = translator.translate("hello")
    assert calls == ["slow"]
    assert "deadline exceeded" in output


def test_translate_shrinks_timeout_to_fit_deadline(monkeypatch):
    seen = []

    def provider(text, source, target, session, timeout):
        seen.append(timeout)
        return Translation("ok", None, "x")

    monkeypatch.setitem(_providers.REGISTRY, "x", provider)
    monkeypatch.setenv("GD_PROVIDERS", "x")
    monkeypatch.setenv("GD_DEADLINE", "4")

    translator.translate("hello")
    connect, read = seen[0]
    assert read <= 2.0  # half of the remaining budget
    assert connect <= _net.CONNECT_TIMEOUT


def test_translate_escapes_html(monkeypatch):
    monkeypatch.setitem(
        _providers.REGISTRY,
        "x",
        _stub_provider(result=Translation("<b>bold</b>", None, "x")),
    )
    monkeypatch.setenv("GD_PROVIDERS", "x")

    output = translator.translate("<script>alert(1)</script>")
    assert "<script>" not in output
    assert "&lt;script&gt;" in output
    assert "&lt;b&gt;bold&lt;/b&gt;" in output


def test_translate_renders_newlines_as_breaks(monkeypatch):
    monkeypatch.setitem(
        _providers.REGISTRY, "x", _stub_provider(result=Translation("a\nb", None, "x"))
    )
    monkeypatch.setenv("GD_PROVIDERS", "x")
    assert "a<br>b" in translator.translate("hello")


@pytest.mark.parametrize("word", ["", "   ", "\n\t"])
def test_translate_ignores_blank_input(word):
    assert translator.translate(word) == ""


def test_translate_truncates_overlong_input(monkeypatch):
    seen = []

    def provider(text, source, target, session, timeout):
        seen.append(text)
        return Translation("ok", None, "x")

    monkeypatch.setitem(_providers.REGISTRY, "x", provider)
    monkeypatch.setenv("GD_PROVIDERS", "x")

    translator.translate("y" * 10_000)
    assert len(seen[0]) == translator.MAX_INPUT_CHARS


def test_unknown_provider_name_is_reported(monkeypatch):
    monkeypatch.setenv("GD_PROVIDERS", "does-not-exist")
    output = translator.translate("hello")
    assert "unknown provider" in output


def test_debug_note_shows_provider(monkeypatch):
    monkeypatch.setitem(
        _providers.REGISTRY, "x", _stub_provider(result=Translation("你好", None, "x"))
    )
    monkeypatch.setenv("GD_PROVIDERS", "x")
    monkeypatch.setenv("GD_DEBUG", "1")
    assert "x in" in translator.translate("hello")


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #
def test_main_writes_html(monkeypatch, capsys):
    monkeypatch.setitem(
        _providers.REGISTRY, "x", _stub_provider(result=Translation("你好", None, "x"))
    )
    monkeypatch.setenv("GD_PROVIDERS", "x")
    monkeypatch.setattr("sys.argv", ["translator", "hello"])

    translator.main()
    assert "你好" in capsys.readouterr().out


def test_main_joins_multiple_arguments(monkeypatch, capsys):
    seen = []

    def provider(text, source, target, session, timeout):
        seen.append(text)
        return Translation("ok", None, "x")

    monkeypatch.setitem(_providers.REGISTRY, "x", provider)
    monkeypatch.setenv("GD_PROVIDERS", "x")
    monkeypatch.setattr("sys.argv", ["translator", "good", "morning"])

    translator.main()
    capsys.readouterr()
    assert seen == ["good morning"]


def test_main_reads_stdin(monkeypatch, capsys):
    monkeypatch.setitem(
        _providers.REGISTRY, "x", _stub_provider(result=Translation("你好", None, "x"))
    )
    monkeypatch.setenv("GD_PROVIDERS", "x")
    monkeypatch.setattr("sys.argv", ["translator"])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("hello\n"))

    translator.main()
    assert "你好" in capsys.readouterr().out


def test_main_never_raises(monkeypatch, capsys):
    monkeypatch.setattr(
        translator, "translate", lambda word: (_ for _ in ()).throw(ValueError("bad"))
    )
    monkeypatch.setattr("sys.argv", ["translator", "hello"])

    translator.main()  # must not raise
    assert "Translator error" in capsys.readouterr().out


def test_main_tolerates_broken_pipe(monkeypatch):
    monkeypatch.setitem(
        _providers.REGISTRY, "x", _stub_provider(result=Translation("你好", None, "x"))
    )
    monkeypatch.setenv("GD_PROVIDERS", "x")
    monkeypatch.setattr("sys.argv", ["translator", "hello"])

    class BrokenStdout:
        encoding = "utf-8"

        def reconfigure(self, **kwargs):
            pass

        def write(self, data):
            raise BrokenPipeError("closed")

        def flush(self):
            pass

    monkeypatch.setattr("sys.stdout", BrokenStdout())
    translator.main()  # must not raise


def test_configure_streams_handles_missing_stream(monkeypatch):
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stdin", None)
    translator._configure_streams()  # must not raise


def test_normalise_strips_invisible_characters():
    assert translator._normalise("\ufeffhello\u200b") == "hello"
    assert translator._normalise("good\u00a0morning") == "good morning"


def test_translate_ignores_bom_only_input():
    assert translator.translate("\ufeff  ") == ""
