# Contributing

**English** · [Русский](CONTRIBUTING.ru.md)

## Getting the project to run

Windows only — the app bundles Windows binaries and its launchers are `.bat`
files (see [README](README.md#requirements)).

```bat
backend\python\python.exe desktop\run.py          :: desktop client
backend\python\python.exe server\run_server.py    :: web cabinet on :8000
```

`backend/python` is the embedded runtime that everything heavy runs in. The web
cabinet has a second, deliberately torch-free venv at `server/.venv`; it invokes
the embedded runtime as a subprocess. **Anything that probes for or installs a
transcription engine must target `backend/python`, never the server's own
interpreter** — getting that backwards made the admin panel report all seven
engines as "not installed" while transcription worked fine.

## Tests

There is no pytest suite. Each `_selftest_*.py` is a standalone runner that
prints `PASS`/`FAIL` lines and a `SUMMARY`, and exits non-zero on failure:

```bat
backend\python\python.exe desktop\_selftest_ui.py
backend\python\python.exe server\_selftest_core.py
```

Run all of them (46 runners, 1395 checks, a few minutes):

```bat
for %f in (desktop\_selftest*.py) do @backend\python\python.exe %f
for %f in (server\_selftest*.py) do @backend\python\python.exe %f
```

Set `QT_QPA_PLATFORM=offscreen` for the UI runners on a headless machine — but
note that the offscreen platform exposes **zero font families**, so it is fine
for asserting geometry and behaviour and useless for judging how text looks.

`_livetest_*.py` files need real models and are not part of the normal run.

### What a good test looks like here

- **Assert behaviour, not source text.** `'foo' in source` cannot see a file that
  no longer parses. Every shipped script is parsed by the suite (`node --check`,
  `py_compile`, a JSON load); keep it that way.
- **Check the exit code, not just the output.** A runner that prints `FAIL` and
  exits 0 is worse than no test.
- **Never weaken an assertion to make it pass.** If a test fails, first decide
  whether the bug is in the product or in the test, and say which.
- **A flake is a bug.** Find the nondeterminism and pin it. The export-parity
  runner carries six specific timestamps in `STAMPS` for exactly this reason —
  do not "tidy" them away.

## Style

Match the file you are editing. Comments explain *why*, especially when the code
looks odd — most of the odd-looking code here is a fix for something that
actually broke, and the comment is the only record of it.

## Before opening a PR

1. The full suite is green, twice in a row.
2. A defect is fixed on **every** front-end it affects — desktop *and* web
   cabinet. A defect is a class, not a location.
3. User-visible strings exist in both RU and EN (`server/web/js/i18n.js` for the
   cabinet, the `_L` dictionaries for the desktop).
4. Docs that mention what you changed are updated in **both** languages, and each
   language's links point at that language's files.
5. If you changed anything that ships, say so — the release archives and their
   checksums have to be rebuilt after the last edit, not before it.

## Reporting bugs

Open an issue with what you did, what happened and what you expected. Include the
engine, the AI provider and whether you were on the desktop or the web cabinet.

**For anything security-related, do not open an issue** — see
[SECURITY.md](SECURITY.md), which also lists what must never be attached.
