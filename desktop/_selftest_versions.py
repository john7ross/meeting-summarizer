"""Tests for version model + history: summary/analysis versioning and linkage.

Run:
    backend\\python\\python.exe desktop\\_selftest_versions.py
"""
import sys, tempfile, os, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.core.history import HistoryStore, versioned_filename
from desktop.app.core.models import HistoryEntry, AnalysisVersion, SummaryVersion

PASS, FAIL = [], []

def check(name, cond, detail=""):
    if cond:
        PASS.append(name); print(f"PASS  {name}")
    else:
        FAIL.append(name); print(f"FAIL  {name}" + (f"  ({detail})" if detail else ""))

tmp = tempfile.mkdtemp()
store = HistoryStore(
    path=os.path.join(tmp, "history.json"),
    transcripts_root=Path(tmp) / "transcripts")

# ── add entry ─────────────────────────────────────────────────────────────────
eid = store.add("C:/videos/meeting.mp4", duration="00:30:00", size="100 MB")
check("entry_created", isinstance(eid, int))

job = store.job_dir(eid)

# ── summary versioning ────────────────────────────────────────────────────────
s1 = job / versioned_filename("meeting", "summary", 1, ".txt")
s1.write_text("summary v1", encoding="utf-8")
v1 = store.add_summary_version(eid, s1, provider="local")
check("summary_v1_is_1", v1 == 1)

s2 = job / versioned_filename("meeting", "summary", 2, ".txt")
s2.write_text("summary v2", encoding="utf-8")
v2 = store.add_summary_version(eid, s2, provider="openai")
check("summary_v2_is_2", v2 == 2)

# filename versioning: v1 has no suffix, v2 has _v2
check("v1_filename_plain", versioned_filename("meeting","summary",1,".txt") == "meeting_summary.txt")
check("v2_filename_suffixed", versioned_filename("meeting","summary",2,".txt") == "meeting_summary_v2.txt")

entry = store.get(eid)
check("two_summary_versions", len(entry.summary_versions) == 2)
check("summary_path_mirrors_latest", entry.summary_path == str(s2))
check("summary_v1_provider", entry.summary_versions[0].provider == "local")
check("summary_v2_provider", entry.summary_versions[1].provider == "openai")

# ── analysis versioning with source linkage ──────────────────────────────────
a1 = job / versioned_filename("meeting", "analysis", 1, ".json")
a1.write_text(json.dumps({"x": 1}), encoding="utf-8")
# explicit link to summary v2
av1 = store.add_analysis_version(eid, a1, provider="local", source_summary_version=2)
check("analysis_v1_is_1", av1 == 1)

entry = store.get(eid)
check("analysis_linked_to_summary_v2",
      entry.analysis_versions[0].source_summary_version == 2,
      str(entry.analysis_versions[0].source_summary_version))

# default link = latest summary present at the time
a2 = job / versioned_filename("meeting", "analysis", 2, ".json")
a2.write_text(json.dumps({"x": 2}), encoding="utf-8")
av2 = store.add_analysis_version(eid, a2, provider="openai")  # no explicit link
entry = store.get(eid)
check("analysis_v2_default_link",
      entry.analysis_versions[1].source_summary_version == 2,
      str(entry.analysis_versions[1].source_summary_version))

# ── round-trip through JSON: linkage survives ─────────────────────────────────
reloaded = HistoryStore(
    path=os.path.join(tmp, "history.json"),
    transcripts_root=Path(tmp) / "transcripts").get(eid)
check("reload_two_analysis", len(reloaded.analysis_versions) == 2)
check("reload_link_preserved",
      reloaded.analysis_versions[0].source_summary_version == 2)
check("reload_analysis_is_AnalysisVersion",
      isinstance(reloaded.analysis_versions[0], AnalysisVersion))

# to_dict has the camelCase key
d = reloaded.analysis_versions[0].to_dict()
check("to_dict_has_sourceSummaryVersion", d.get("sourceSummaryVersion") == 2, str(d))

# ── legacy entry without sourceSummaryVersion -> defaults to 0 ────────────────
legacy = AnalysisVersion.from_dict({"version": 1, "path": "x.json"})
check("legacy_link_zero", legacy.source_summary_version == 0)

# ── regenerate scenario: summary v3 without paired analysis ───────────────────
# (simulates regenerating summary while analysis disabled in settings)
s3 = job / versioned_filename("meeting", "summary", 3, ".txt")
s3.write_text("summary v3", encoding="utf-8")
store.add_summary_version(eid, s3, provider="local")
entry = store.get(eid)
check("three_summaries_two_analyses",
      len(entry.summary_versions) == 3 and len(entry.analysis_versions) == 2)
# the gap is now explicit: latest analysis still points to summary v2,
# so the UI can show "analysis is from summary v2", not v3
check("latest_analysis_still_v2_link",
      entry.analysis_versions[-1].source_summary_version == 2)

# ── summary ───────────────────────────────────────────────────────────────────
print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
else:
    print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
    sys.exit(0)
