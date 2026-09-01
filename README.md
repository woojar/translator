# translator

A small command-line translator designed to be used as an external **program
dictionary** in [GoldenDict](http://goldendict.org/).

When you look up a word in GoldenDict, it runs this script with the word and
renders the returned HTML inline alongside your other dictionaries.

## Behaviour

- **Chinese input** (contains CJK characters) → translated to **English**.
- **Any other input** (e.g. English) → translated to **Chinese (Simplified)**.
- Output is minimal, readable HTML showing the source/target languages, the
  original word, and the translation.

Requires network access (uses free, unauthenticated translation endpoints).

## Reliability

The free endpoints are rate-limited per IP address, which is why a naive
lookup fails intermittently — especially behind a corporate proxy where many
users share one egress address. This tool works around that:

- **Disk cache** — a successful lookup is stored in SQLite, so repeat lookups
  cost no network traffic at all. This alone removes most failures.
- **Provider fallback** — several back ends are tried in order until one
  answers: `google-m` (Google's lightweight mobile page), `google-json`
  (`translate.googleapis.com`), then `mymemory` (an independent service). A
  `deep-translator` back end is also available but not enabled by default.
- **Bounded time** — every request has a connect/read timeout, and the whole
  lookup has a deadline, so a lookup can never hang GoldenDict.
- **HTTP 429 fails over immediately** instead of backing off against an
  endpoint that is already refusing us. Only genuinely transient errors
  (connection resets, 5xx) are retried.
- **Never crashes** — any failure, including a broken pipe or a changed page
  layout upstream, is rendered as an HTML message instead of a traceback.

## Installation

This project uses [uv](https://docs.astral.sh/uv/).

```bash
# from the project root
uv sync
```

This creates a virtual environment in `.venv/` and installs the `translator`
console script (`.venv/bin/translator`, or `.venv\Scripts\translator.exe` on
Windows).

## Command-line usage

```bash
# English -> Chinese
.venv/bin/translator "good morning"

# Chinese -> English
.venv/bin/translator "你好世界"

# also works via stdin
echo "computer science" | .venv/bin/translator

# equivalent module form
.venv/bin/python -m translator "hello"
```

The output is HTML. To see plain text on the terminal you can strip the tags:

```bash
.venv/bin/translator "apple" | sed -E 's/<[^>]+>//g'
```

## Configuration

All settings are environment variables, so they can be set inline in the
GoldenDict command line.

| Variable | Default | Meaning |
| --- | --- | --- |
| `GD_SRC` | `auto` | Source language code. |
| `GD_TGT` | `en` / `zh-CN` | Target language code (see *Behaviour*). |
| `GD_PROVIDERS` | `google-m,google-json,mymemory` | Comma-separated back ends to try, in order. Also available: `deep-translator`. |
| `GD_TIMEOUT` | `5` | Read timeout per request, in seconds. |
| `GD_DEADLINE` | `12` | Budget for the whole lookup, in seconds. |
| `GD_PROXY` | – | Proxy for both http and https; overrides `HTTP_PROXY` / `HTTPS_PROXY`. |
| `GD_NO_CACHE` | – | Set to `1` to disable the cache. |
| `GD_CACHE_TTL` | `2592000` | Cache entry lifetime in seconds; `0` never expires. |
| `GD_CACHE_DIR` | platform cache dir | Where to keep `translations.sqlite3`. |
| `GD_DEBUG` | – | Set to `1` to show which provider answered, how long it took, and why others failed. |

Language codes are the ones Google Translate accepts; use `auto` for automatic
source detection.

```bash
# force English -> Chinese
GD_SRC=en GD_TGT=zh-CN .venv/bin/translator "hello"

# auto-detect source, translate to Japanese
GD_TGT=ja .venv/bin/translator "hello"

# diagnose a flaky lookup
GD_DEBUG=1 GD_NO_CACHE=1 .venv/bin/translator "hello"
```

## Using a proxy

If the translation endpoint is only reachable through a proxy, set one of the
following environment variables. `GD_PROXY` takes precedence and is applied to
both HTTP and HTTPS; otherwise the standard `HTTP_PROXY` / `HTTPS_PROXY`
variables are used.

```bash
# single proxy for both protocols
GD_PROXY=http://127.0.0.1:7890 .venv/bin/translator "hello"

# standard proxy variables
HTTPS_PROXY=http://127.0.0.1:7890 .venv/bin/translator "hello"
```

In a GoldenDict command line, prefix with `env` like the language example:

```
env GD_PROXY=http://127.0.0.1:7890 /absolute/path/to/translator/.venv/bin/translator "%GDWORD%"
```

## Using it in GoldenDict

1. Open GoldenDict and go to **Edit → Dictionaries… → Programs** tab.
2. Add a new entry and configure it:
   - **Enabled**: checked
   - **Type**: `Html`
   - **Name**: `Translator` (anything you like)
   - **Command Line**:
     ```
     /absolute/path/to/translator/.venv/bin/translator "%GDWORD%"
     ```
     If the console script is not on your `PATH`, use the module form instead:
     ```
     /absolute/path/to/translator/.venv/bin/python -m translator "%GDWORD%"
     ```
     To force a language direction, prefix with the env vars:
     ```
     env GD_SRC=en GD_TGT=zh-CN /absolute/path/to/translator/.venv/bin/translator "%GDWORD%"
     ```
3. Click **Apply / OK**.
4. Look up any word — the translation appears as its own dictionary entry.

Notes:
- `%GDWORD%` is the placeholder GoldenDict replaces with the looked-up word.
- Replace `/absolute/path/to/translator` with the real path to this project.
- On Windows, point GoldenDict at `.venv\Scripts\translator.exe` (not
  `pythonw.exe`) so stdout exists.

## Tests

```bash
uv sync --all-groups
uv run pytest
```

The test suite stubs out all HTTP traffic, so it runs offline.
