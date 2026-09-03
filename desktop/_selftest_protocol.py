"""The formal protocol's metadata must be a FACT, not a guess.

Measured on the owner's own 14 analyses before this: the model wrote "24.10.2023"
in ten of them, "Текущая дата (на основании транскрипции)" in one, and a
differently-shaped protocol number every single time — while the real date and
start time sat in the file name (`2026-08-17 15-33-43.mkv`). Covered here:

* the file-name parser (every shape the recorders produce, and rejection of digit
  groups that are not a timestamp);
* the derived facts: date in the output language, start–end from the duration, a
  protocol number that invents nothing;
* the prompt carries the facts (so the protocol TEXT states them) and the parsed
  fields are overwritten regardless of what the model answered;
* a file NOT named by date leaves the model's own answer alone;
* end to end through the real pipeline, with the fake AI CLI.

Run:
    backend\\python\\python.exe desktop\\_selftest_protocol.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QCoreApplication, QTimer     # noqa: E402

from app import paths                                    # noqa: E402
from app.backend import analysis as A                    # noqa: E402
from app.backend import gsheets, obsidian                # noqa: E402
from app.backend.media import (                          # noqa: E402
    meeting_datetime_from_name, parse_duration_label, shift_clock)
from app.core.history import HistoryStore                # noqa: E402
from app.core.pipeline import JobRunner, PipelineQueue   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS  {name}  {detail}".rstrip())
    else:
        FAIL.append(name)
        print(f"FAIL  {name}  {detail}".rstrip())


# ── 1. file-name parsing ──────────────────────────────────────────────────────
CASES = {
    # the owner's own recordings (verified against transcripts/ on disk)
    "2026-08-17 15-33-43.mkv": ("2026-08-17", "15:33"),
    "2026-07-28 14-31-45.mkv": ("2026-07-28", "14:31"),
    # other shapes tools produce
    "Планёрка 17.08.2026 15-33.mp4": ("2026-08-17", "15:33"),
    "meeting_20260817_153343.wav": ("2026-08-17", "15:33"),
    "call 2026-08-17T09:05:00.m4a": ("2026-08-17", "09:05"),
    "Zoom 2026.08.17 09.05.mp4": ("2026-08-17", "09:05"),
    # date but no time
    "2026-08-17.mkv": ("2026-08-17", ""),
    # nothing to read
    "Синк с банком.mkv": ("", ""),
    # NOT a timestamp: a version string, and out-of-range numbers
    "v1.2.2026 release notes.mkv": ("", ""),
    "запись 2026-13-45 99-99-99.mkv": ("", ""),
}
for name, expected in CASES.items():
    got = meeting_datetime_from_name(name)
    check(f"name_parse: {name}", got == expected, f"{got} != {expected}" if got != expected else "")

check("duration_ru_label", parse_duration_label("12м 47с") == 767)
check("duration_en_label", parse_duration_label("12m 47s") == 767)
check("duration_with_hours", parse_duration_label("1h 5m 3s") == 3903)
check("duration_unparsable_is_zero", parse_duration_label("N/A") == 0
      and parse_duration_label("") == 0)
check("end_time_is_start_plus_duration", shift_clock("15:33", 767) == "15:46")
check("end_time_wraps_past_midnight", shift_clock("23:50", 1200) == "00:10",
      shift_clock("23:50", 1200))
check("no_duration_no_end_time", shift_clock("15:33", 0) == "")

# ── 2. derived facts ──────────────────────────────────────────────────────────
ru = A.protocol_facts("2026-08-17 15-33-43.mkv", "12м 47с", "ru")
en = A.protocol_facts("2026-08-17 15-33-43.mkv", "12м 47с", "en")
check("facts_ru_date_is_russian_style", ru.get("date") == "17.08.2026", str(ru))
check("facts_en_date_is_iso", en.get("date") == "2026-08-17", str(en))
check("facts_number_is_date_and_start_time",
      ru.get("protocolNumber") == "2026-08-17-1533", str(ru))
check("facts_time_is_an_interval", ru.get("time") == "15:33 – 15:46", str(ru))
check("facts_number_without_time_is_the_date",
      A.protocol_facts("2026-08-17.mkv", "12м 47с").get("protocolNumber") == "2026-08-17")
check("facts_no_time_field_when_the_name_has_none",
      "time" not in A.protocol_facts("2026-08-17.mkv", "12м 47с"))
check("facts_time_is_a_single_moment_without_a_duration",
      A.protocol_facts("2026-08-17 15-33-43.mkv", "").get("time") == "15:33")
check("no_date_in_the_name_means_no_facts_at_all",
      A.protocol_facts("Синк с банком.mkv", "12м 47с") == {}, "the model keeps deciding")

# ── 3. the prompt states them, and only for the protocol ─────────────────────
block = A.protocol_facts_block(ru, "ru")
check("facts_block_lists_all_three",
      all(v in block for v in ("2026-08-17-1533", "17.08.2026", "15:33 – 15:46")), block)
prompt = A.feature_prompt("formalProtocol", "ru", facts_block=block)
check("facts_land_before_the_transcript_label",
      prompt.endswith("Транскрипция:") and "2026-08-17-1533" in prompt,
      prompt[-80:].replace("\n", " "))
check("facts_are_the_last_thing_before_the_transcript",
      prompt.index("2026-08-17-1533") > prompt.index("protocolText"))
check("prompt_without_facts_is_untouched",
      A.feature_prompt("formalProtocol", "ru")
      == A.feature_prompt("formalProtocol", "ru", facts_block=""))
check("the_fake_ai_still_recognises_the_protocol_prompt",
      prompt.lower().startswith("сгенерируй формальный протокол"),
      "the facts block must not displace the opening instruction")
settings = {"transcriptionLanguage": "ru", "outputLanguage": "ru"}



def sent_prompt(command) -> str:
    """The prompt as the CLI will really receive it: via the environment, never
    argv (a prompt on the command line is visible to every process on the box)."""
    return (command.process_environment() or {}).get("MEETING_SUMMARIZER_PROMPT", "")


other = A.build_feature_command("actionItems", "t.txt", settings, facts=ru,
                                python_exe="py", ai_client_script="ai.py")
check("other_features_get_no_protocol_facts",
      "2026-08-17-1533" not in sent_prompt(other))
proto_cmd = A.build_feature_command("formalProtocol", "t.txt", settings, facts=ru,
                                    python_exe="py", ai_client_script="ai.py")
check("the_protocol_feature_gets_them",
      "2026-08-17-1533" in sent_prompt(proto_cmd) and "17.08.2026" in sent_prompt(proto_cmd),
      sent_prompt(proto_cmd)[-90:].replace("\n", " "))
check("the_facts_never_travel_on_the_command_line",
      not any("2026-08-17-1533" in str(part) for part in proto_cmd))

# ── 4. the parsed fields are overwritten whatever the model answered ─────────
invented = {"protocolNumber": "01-2023-PR", "date": "Текущая дата (на основании транскрипции)",
            "time": "14:00 - 14:15", "location": "Онлайн", "participants": ["Спикер 1"]}
fixed = A.apply_protocol_facts(dict(invented), ru)
check("invented_number_replaced", fixed["protocolNumber"] == "2026-08-17-1533")
check("invented_date_replaced", fixed["date"] == "17.08.2026")
check("invented_time_replaced", fixed["time"] == "15:33 – 15:46")
check("everything_else_survives",
      fixed["location"] == "Онлайн" and fixed["participants"] == ["Спикер 1"])
check("no_facts_changes_nothing", A.apply_protocol_facts(dict(invented), {}) == invented)

# ── 5. the other name-based dates use the same parser now ───────────────────
check("obsidian_reads_a_dotted_date",
      obsidian._date_from_stem("Планёрка 17.08.2026 15-33") == "2026-08-17",
      obsidian._date_from_stem("Планёрка 17.08.2026 15-33"))
check("gsheets_reads_a_dotted_date",
      gsheets._extract_date("Планёрка 17.08.2026 15-33.mp4") == "2026-08-17",
      gsheets._extract_date("Планёрка 17.08.2026 15-33.mp4"))
check("both_still_fall_back_to_today_without_a_date",
      len(obsidian._date_from_stem("Синк с банком")) == 10
      and len(gsheets._extract_date("Синк с банком.mkv")) == 10)

# ── 6. end to end through the real pipeline (fake AI CLI) ───────────────────
app = QCoreApplication.instance() or QCoreApplication(sys.argv)
PY = str(paths.python_executable())
FAKE_PROC = str(HERE / "_fake_processor_cli.py")
FAKE_AI = str(HERE / "_fake_ai_cli.py")


def run_pipeline(video_name: str) -> dict:
    """Process one file end to end; return its analysis JSON."""
    root = Path(tempfile.mkdtemp())
    video = root / video_name
    video.write_bytes(b"not a real video")
    store = HistoryStore(path=root / "history.json",
                         transcripts_root=root / "transcripts")
    conf = {
        "transcriptionEngine": "faster-whisper", "whisperModel": "medium",
        "transcriptionLanguage": "ru", "whisperDevice": "auto",
        "analysisSource": "transcript", "aiProvider": "local",
        "localEndpoint": "http://localhost:1234/v1",
        "prompt": "Сделай саммари.", "generateFormalProtocol": True,
    }
    queue = PipelineQueue(
        1, lambda i, v: JobRunner(i, v, conf, store, python_exe=PY,
                                  processor_script=FAKE_PROC, ai_client_script=FAKE_AI))
    queue.all_done.connect(lambda: QTimer.singleShot(0, app.quit))
    entry_id = store.add(str(video), "12м 47с", "18.2 MB")
    queue.enqueue(entry_id, str(video))
    QTimer.singleShot(120000, app.quit)
    app.exec()
    entry = store.get(entry_id)
    if not entry or not entry.analysis_versions:
        return {}
    return json.loads(Path(entry.analysis_versions[-1].path).read_text(encoding="utf-8"))


named = run_pipeline("2026-08-17 15-33-43.mkv")
protocol = (named.get("formalProtocol") or {})
check("e2e_protocol_produced", bool(protocol), str(named)[:120])
check("e2e_number_from_the_file_name",
      protocol.get("protocolNumber") == "2026-08-17-1533", str(protocol.get("protocolNumber")))
check("e2e_date_from_the_file_name",
      protocol.get("date") == "17.08.2026", str(protocol.get("date")))
check("e2e_time_is_start_plus_duration",
      protocol.get("time") == "15:33 – 15:46", str(protocol.get("time")))

unnamed = run_pipeline("Синк с банком.mkv")
unnamed_protocol = (unnamed.get("formalProtocol") or {})
check("e2e_unnamed_file_produced_a_protocol_too", bool(unnamed_protocol),
      str(unnamed)[:120])
check("e2e_nothing_is_imposed_without_a_date_in_the_name",
      "protocolNumber" not in unnamed_protocol and "date" not in unnamed_protocol,
      str(unnamed_protocol))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
