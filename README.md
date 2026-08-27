# translator

A small command-line translator, built on
[deep-translator](https://pypi.org/project/deep-translator/), designed to be
used as an external **program dictionary** in
[GoldenDict](http://goldendict.org/).

When you look up a word in GoldenDict, it runs this script with the word and
renders the returned HTML inline alongside your other dictionaries.

## Behaviour

- **Chinese input** (contains CJK characters) → translated to **English**.
- **Any other input** (e.g. English) → translated to **Chinese (Simplified)**.
- Output is minimal, readable HTML showing the source/target languages, the
  original word, and the translation.

Requires network access (uses deep-translator's free Google endpoint).

## Installation

This project uses [uv](https://docs.astral.sh/uv/).

```bash
# from the project root
uv sync
```

This creates a virtual environment in `.venv/` and installs the `translator`
console script into `.venv/bin/translator`.

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

## Choosing languages

By default the direction is picked automatically (see *Behaviour*). To force a
specific source/target, set the `GD_SRC` and `GD_TGT` environment variables.
Use language codes accepted by Google Translate, or `auto` for automatic source
detection.

```bash
# force English -> Chinese
GD_SRC=en GD_TGT=zh-CN .venv/bin/translator "hello"

# auto-detect source, translate to Japanese
GD_SRC=auto GD_TGT=ja .venv/bin/translator "hello"
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
- Because it depends on an online service, an occasional transient server
  error can occur; simply look the word up again.
