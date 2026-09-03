"""Self-test for the desktop foundation modules. Run with the bundled python:
    backend\\python\\python.exe desktop\\_selftest.py
Prints a PASS/FAIL line per check. Qt is imported but no window is shown.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append(f"PASS  {name}  {detail or ''}".rstrip())
    except Exception as exc:  # noqa: BLE001 - report any failure
        results.append(f"FAIL  {name}  {exc!r}")
        results.append("      " + traceback.format_exc().replace("\n", "\n      "))


def _pyside():
    import PySide6
    import PySide6.QtWidgets  # noqa: F401 - import must succeed
    return "PySide6 " + PySide6.__version__


def _paths():
    from app import paths
    assert paths.PROCESSOR_SCRIPT.exists(), "processor.py missing"
    assert paths.AI_CLIENT_SCRIPT.exists(), "ai_client.py missing"
    assert paths.python_executable().exists(), "bundled python missing"
    return f"root={paths.ROOT.name}"


def _config():
    from app import config
    s = config.load_settings()
    assert isinstance(s, dict) and len(s) >= 25, f"unexpected settings: {len(s)} keys"
    return f"{len(s)} keys; engine={s.get('transcriptionEngine')}; provider={s.get('aiProvider')}"


def _transcribe_cmd():
    from app.backend import transcription as t
    cmd = t.build_command("video.mkv", "out", engine="whisperx", model="medium")
    assert "processor.py" in cmd[1], cmd
    assert "whisperx" in cmd, cmd
    try:
        t.build_command("v", "o", engine="bogus")
        raise AssertionError("bad engine did not raise")
    except ValueError:
        pass
    return "argv ok; bad-engine rejected"


def _parse_events():
    from app.backend import transcription as t
    p = t.parse_event('{"stage":"status.extracting","progress":10,"details":"x"}')
    r = t.parse_event('{"success":true,"output":"a.txt","trace":"b.json"}')
    assert isinstance(p, t.ProgressEvent) and p.progress == 10, p
    assert isinstance(r, t.ResultEvent) and r.success and r.output == "a.txt", r
    assert t.parse_event("not json") is None
    return "progress+result+garbage parsed correctly"


def _summary_cmd():
    from app.backend import summarization as s
    cmd = s.build_command("PROMPT", "t.txt", provider="local",
                          endpoint="http://localhost:1234/v1",
                          participants=["Ivan", "Olga"])
    assert "ai_client.py" in cmd[1], cmd
    assert "local" in cmd and "Ivan,Olga" in cmd, cmd
    return "argv ok; participants joined"


check("pyside_import", _pyside)
check("paths", _paths)
check("config_load", _config)
check("transcription_build_command", _transcribe_cmd)
check("transcription_parse_event", _parse_events)
check("summarization_build_command", _summary_cmd)

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if all(r.startswith("PASS") for r in results)
                     else "HAS_FAILURES"))
sys.exit(0 if results and not any(r.startswith("FAIL") for r in results) else 1)
