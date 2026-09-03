"""Self-test for the live (streaming) path: segmenter, live summary, worker CLI.

Covers the two new backend modules and the contract the front ends read:

* ``processing/live_vad.py`` — utterance segmentation from synthetic PCM, so no
  microphone and no model are needed;
* ``backend/live_summary.py`` — state schema, tolerant parsing, and the rule
  that a bad answer must NEVER replace a good summary;
* ``backend/live_stt.py`` — the JSON-line contract and the on-disk transcript;
* ``backend/live_summary.py`` end to end through the real ``AIClient`` with a
  fake agent CLI (``_fake_live_ai.py``), including the state file.

Run:
    backend\\python\\python.exe desktop\\_selftest_live.py
"""
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import live_summary                                     # noqa: E402
import live_stt                                         # noqa: E402
from processing import live_engines                     # noqa: E402
from processing.live_vad import Segmenter, Utterance     # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


RATE = 16000


def tone(seconds: float, amp: int = 9000, freq: int = 220) -> bytes:
    n = int(seconds * RATE)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / RATE)))
                    for i in range(n))


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * RATE)


def stereo(left: bytes, right: bytes) -> bytes:
    """Interleave two mono streams into mic-left / system-right, zero-padded."""
    size = max(len(left), len(right))
    left = left + b"\x00\x00" * ((size - len(left)) // 2)
    right = right + b"\x00\x00" * ((size - len(right)) // 2)
    out = bytearray()
    for i in range(0, size, 2):
        out += left[i:i + 2] + right[i:i + 2]
    return bytes(out)


def run(segmenter: Segmenter, pcm: bytes, block: int = 8192):
    out = []
    for i in range(0, len(pcm), block):
        out += segmenter.feed(pcm[i:i + block])
    return out + segmenter.flush()


# ── segmenter: silence produces nothing ──────────────────────────────────────
seg = Segmenter(sample_rate=RATE)
check("silence_yields_no_utterance", run(seg, silence(3.0)) == [])

# ── one phrase between two silences → exactly one utterance ──────────────────
seg = Segmenter(sample_rate=RATE)
got = run(seg, silence(1.0) + tone(2.0) + silence(1.5))
check("one_phrase_one_utterance", len(got) == 1, f"got {len(got)}")
if got:
    u = got[0]
    check("utterance_starts_near_speech", 0.7 <= u.start <= 1.1, f"start={u.start:.2f}")
    # Speech + the trailing silence the segmenter needs to declare the end.
    check("utterance_covers_the_phrase", 2.0 <= u.duration <= 3.2,
          f"duration={u.duration:.2f}")
    check("utterance_pcm_is_mono_int16", len(u.pcm) % 2 == 0 and len(u.pcm) > RATE,
          f"{len(u.pcm)} bytes")

# ── pre-roll: the attack of the phrase must survive ──────────────────────────
# Without pre-roll the utterance can only start AFTER the level crossed the
# threshold, i.e. with the first syllable already gone.
seg = Segmenter(sample_rate=RATE, preroll_ms=240)
got = run(seg, silence(1.0) + tone(1.5) + silence(1.5))
check("preroll_included", bool(got) and got[0].start <= 1.0,
      f"start={got[0].start:.2f}" if got else "no utterance")

# ── two phrases separated by a real pause → two utterances ───────────────────
seg = Segmenter(sample_rate=RATE)
got = run(seg, silence(0.5) + tone(1.5) + silence(1.5) + tone(1.5) + silence(1.5))
check("pause_splits_utterances", len(got) == 2, f"got {len(got)}")

# ── a breath must NOT split a sentence ───────────────────────────────────────
seg = Segmenter(sample_rate=RATE, silence_ms=700)
got = run(seg, silence(0.5) + tone(1.2) + silence(0.3) + tone(1.2) + silence(1.5))
check("short_gap_keeps_one_utterance", len(got) == 1, f"got {len(got)}")

# ── a monologue is cut by the length cap and marked as continuing ────────────
seg = Segmenter(sample_rate=RATE, max_utterance_ms=3000)
got = run(seg, silence(0.5) + tone(9.0) + silence(1.5))
check("long_speech_is_cut", len(got) >= 3, f"got {len(got)}")
check("cut_pieces_marked_forced", any(u.forced for u in got))

# ── a click too short to be a word is dropped ────────────────────────────────
seg = Segmenter(sample_rate=RATE, min_speech_ms=400)
got = run(seg, silence(1.0) + tone(0.08) + silence(1.5))
check("blip_is_not_an_utterance", got == [], f"got {len(got)}")

# ── stereo: who was louder decides the source label ──────────────────────────
seg = Segmenter(sample_rate=RATE, channels=2)
got = run(seg, stereo(silence(0.5) + tone(2.0) + silence(1.5), silence(4.0)))
check("mic_channel_labelled_mic", bool(got) and got[0].source == "mic",
      got[0].source if got else "none")

seg = Segmenter(sample_rate=RATE, channels=2)
got = run(seg, stereo(silence(4.0), silence(0.5) + tone(2.0) + silence(1.5)))
check("system_channel_labelled_system", bool(got) and got[0].source == "system",
      got[0].source if got else "none")

seg = Segmenter(sample_rate=RATE, channels=2)
both = silence(0.5) + tone(2.0) + silence(1.5)
got = run(seg, stereo(both, both))
check("both_channels_labelled_mix", bool(got) and got[0].source == "mix",
      got[0].source if got else "none")

check("mono_never_claims_a_source",
      Utterance(pcm=b"", start=0.0, duration=1.0).source == "mix")

# ── an adaptive floor keeps a noisy room from triggering constantly ──────────
seg = Segmenter(sample_rate=RATE)
noise = tone(6.0, amp=300, freq=90)           # steady hum, no speech
check("steady_noise_is_not_speech", run(seg, noise) == [],
      "hum must not be recognised as speech")

# ── the stream clock advances with the audio, not with wall time ─────────────
seg = Segmenter(sample_rate=RATE)
run(seg, silence(2.0))
check("stream_clock_tracks_audio", abs(seg.position - 2.0) < 0.05,
      f"position={seg.position:.2f}")

# ── live_stt helpers ─────────────────────────────────────────────────────────
check("timestamp_format", live_stt.format_timestamp(3725.4) == "[01:02:05]",
      live_stt.format_timestamp(3725.4))
check("timestamp_never_negative", live_stt.format_timestamp(-5) == "[00:00:00]")

tmp = Path(tempfile.mkdtemp())
tf = live_stt.TranscriptFile(str(tmp / "sub" / "live_transcript.txt"))
tf.append("[00:00:03]", "mic", "первая реплика")
tf.append("[00:00:09]", "system", "ответ собеседника")
tf.close()
written = (tmp / "sub" / "live_transcript.txt").read_text(encoding="utf-8")
check("transcript_file_created_with_parents", written.count("\n") == 2)
check("transcript_lines_carry_source",
      "[00:00:03] [MIC]: первая реплика" in written
      and "[00:00:09] [SYSTEM]: ответ собеседника" in written, repr(written[:60]))
check("transcript_file_without_path_is_a_noop",
      live_stt.TranscriptFile("").append("[00:00:00]", "mic", "x") is None)

# The live transcript is not a live-mode format: it is fed into the SAME
# summary/analysis pipeline as a batch transcript, so it must be the shape the
# rest of the project already parses. A mono recording has nobody to attribute
# to and gets no label, exactly like a non-diarised batch transcript.
check("two_source_line_is_the_diarised_shape",
      live_stt.transcript_line("[00:01:00]", "mic", "текст")
      == "[00:01:00] [MIC]: текст")
check("single_source_line_is_the_plain_shape",
      live_stt.transcript_line("[00:01:00]", "mix", "текст") == "[00:01:00] текст",
      "a label nobody can act on is noise")

sys.path.insert(0, str(ROOT))
from desktop.app.backend import speakers as SPK              # noqa: E402
_parsed = SPK.parse_utterances(written)
check("live_transcript_parses_as_a_diarised_transcript", len(_parsed) == 2,
      f"{len(_parsed)} utterance(s)")
check("live_transcript_speakers_are_extractable",
      SPK.extract_speakers(written) == ["MIC", "SYSTEM"],
      str(SPK.extract_speakers(written)))
check("live_transcript_speakers_are_renameable",
      "[Иван]: первая реплика" in SPK.rename_in_transcript(written, {"MIC": "Иван"}),
      "the speakers dialog must work on a live transcript too")

# ── engine registry: unsupported engines say so instead of substituting ──────
check("supports_faster_whisper", live_engines.supports("faster-whisper"))
check("supports_whisperx_via_faster", live_engines.supports("whisperx"))
check("funasr_not_supported_live", not live_engines.supports("funasr"))
try:
    live_engines.load("funasr", "", "ru")
    check("unsupported_engine_raises", False, "no exception")
except RuntimeError as exc:
    check("unsupported_engine_raises", "funasr" in str(exc) or "not suitable" in str(exc))
    check("unsupported_engine_lists_alternatives", "faster-whisper" in str(exc))
try:
    live_engines.load("nope", "", "ru")
    check("unknown_engine_raises", False, "no exception")
except RuntimeError:
    check("unknown_engine_raises", True)

# ── live summary: state schema ───────────────────────────────────────────────
check("empty_state_has_all_fields",
      set(live_summary.empty_state()) == set(live_summary.STATE_FIELDS))
check("empty_state_is_empty", live_summary.state_is_empty(live_summary.empty_state()))

messy = {"short_summary": "  ## Тема\n- пункт  ", "decisions": "нет",
         "action_items": [{"task": "x"}], "entities": {"people": "Иван"}}
norm = live_summary.normalize_state(messy)
check("normalize_keeps_good_fields", norm["action_items"] == [{"task": "x"}])
check("normalize_repairs_wrong_types", norm["decisions"] == []
      and norm["entities"]["people"] == [])
check("normalize_trims_summary", norm["short_summary"].startswith("## Тема"))
check("normalize_of_garbage_is_empty_state",
      live_summary.normalize_state("что-то не то") == live_summary.empty_state())
check("state_with_only_decisions_is_not_empty",
      not live_summary.state_is_empty(
          live_summary.normalize_state({"decisions": ["перенести релиз"]})))

# ── live summary: tolerant parsing ───────────────────────────────────────────
good = '{"updated_state": {"short_summary": "## Тема\\n- пункт"}, "live_delta": "новое"}'
check("parse_plain_json", live_summary.parse_response(good) is not None)
check("parse_json_fence",
      live_summary.parse_response("```json\n" + good + "\n```") is not None)
check("parse_bare_fence",
      live_summary.parse_response("```\n" + good + "\n```") is not None)
check("parse_with_prose_around",
      live_summary.parse_response("Вот результат:\n" + good + "\nГотово!") is not None)
check("parse_trailing_comma",
      live_summary.parse_response('{"updated_state": {"decisions": ["a",],},}') is not None)
check("parse_garbage_is_none", live_summary.parse_response("нет тут json") is None)
check("parse_empty_is_none", live_summary.parse_response("") is None)
check("parse_array_is_none", live_summary.parse_response('[1,2,3]') is None)

# ── live summary: what may and may not replace the previous state ────────────
previous = live_summary.normalize_state(
    {"short_summary": "## Бюджет\n- ждут цифры", "decisions": ["перенести релиз"]})

res = live_summary.extract_result(live_summary.parse_response(good), previous)
check("wrapped_answer_accepted", res is not None and res[1] == "новое")

bare = '{"short_summary": "## Тема\\n- пункт", "decisions": []}'
res_bare = live_summary.extract_result(live_summary.parse_response(bare), previous)
check("unwrapped_answer_accepted", res_bare is not None,
      "a formatting slip must not throw away a correct summary")

blank = '{"updated_state": {"short_summary": "", "decisions": []}, "live_delta": ""}'
check("blanking_a_good_summary_is_rejected",
      live_summary.extract_result(live_summary.parse_response(blank), previous) is None)
check("blank_answer_ok_when_nothing_yet",
      live_summary.extract_result(live_summary.parse_response(blank),
                                  live_summary.empty_state()) is not None)
check("non_dict_answer_rejected", live_summary.extract_result(["x"], previous) is None)
check("answer_without_state_rejected",
      live_summary.extract_result({"live_delta": "нечто"}, previous) is None)

# ── live summary: state file round-trip is atomic and keeps a counter ────────
state_path = tmp / "artifacts" / "live_summary.json"
live_summary.save_state(str(state_path), previous, "дельта", 1)
check("state_file_written", state_path.is_file())
check("no_temp_file_left", not list(state_path.parent.glob("*.tmp")))
check("state_round_trip", live_summary.load_state(str(state_path)) == previous)
check("updates_counter_read_back", live_summary.previous_updates(str(state_path)) == 1)
check("missing_state_file_is_empty_state",
      live_summary.load_state(str(tmp / "nope.json")) == live_summary.empty_state())
(tmp / "broken.json").write_text("{not json", encoding="utf-8")
check("corrupt_state_file_is_empty_state",
      live_summary.load_state(str(tmp / "broken.json")) == live_summary.empty_state())

# ── live summary: payload per mode, and the transcript tail rule ─────────────
payload = json.loads(live_summary.build_user_payload(
    "update", previous, "новый кусок", "недавнее", "весь транскрипт"))
check("update_payload_has_state_and_chunk",
      set(payload) == {"current_summary_state", "recent_transcript_buffer",
                       "new_transcript_chunk"})
payload = json.loads(live_summary.build_user_payload(
    "regen", previous, "chunk", "recent", "весь транскрипт"))
check("regen_payload_is_transcript_only", set(payload) == {"transcript"},
      "anti-drift: the previous summary must not be fed back in")
payload = json.loads(live_summary.build_user_payload(
    "consolidate", previous, "chunk", "recent", "транскрипт"))
check("consolidate_payload_is_state_only", set(payload) == {"current_summary_state"})

long_path = tmp / "long.txt"
long_path.write_text("A" * 100 + "ХВОСТ", encoding="utf-8")
tail = live_summary.read_text(str(long_path), limit=20)
check("long_transcript_keeps_the_tail", tail.endswith("ХВОСТ"))
check("truncation_is_announced", "обрезан" in tail)
check("short_transcript_untouched",
      live_summary.read_text(str(long_path), limit=10000).endswith("ХВОСТ")
      and "обрезан" not in live_summary.read_text(str(long_path), limit=10000))

check("prompts_exist_in_both_languages",
      all(live_summary.build_prompt(m, lang)
          for m in ("update", "regen", "consolidate") for lang in ("ru", "en")))
check("regen_prompt_forbids_previous_summary",
      "заново" in live_summary.build_prompt("regen", "ru").lower()
      and "scratch" in live_summary.build_prompt("regen", "en").lower())
check("unknown_language_falls_back_to_ru",
      live_summary.build_prompt("update", "de") == live_summary.build_prompt("update", "ru"))

# ── end to end through the real AIClient with a fake agent ───────────────────
PY = sys.executable
FAKE = str(Path(__file__).resolve().parent / "_fake_live_ai.py")


def run_live_summary(mode, state_file, chunk_file="", transcript_file="",
                     fake_mode="ok"):
    env = dict(os.environ, FAKE_LIVE_MODE=fake_mode)
    cmd = [PY, str(ROOT / "backend" / "live_summary.py"), "--mode", mode,
           "--provider", "agent",
           "--agent-command", f'"{PY}" "{FAKE}"',
           "--state-file", str(state_file)]
    if chunk_file:
        cmd += ["--chunk-file", str(chunk_file)]
    if transcript_file:
        cmd += ["--transcript-file", str(transcript_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=180)
    try:
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return proc.returncode, {"_stdout": proc.stdout, "_stderr": proc.stderr}


e2e = tmp / "e2e"
e2e.mkdir()
chunk = e2e / "chunk.txt"
chunk.write_text("[00:01:00] MIC: давайте перенесём релиз на пятницу", encoding="utf-8")
live_state = e2e / "live_summary.json"

code, out = run_live_summary("update", live_state, chunk_file=chunk)
check("e2e_update_succeeds", code == 0 and out.get("success") is True, str(out)[:200])
check("e2e_update_returns_state",
      "Запуск продукта" in (out.get("updated_state", {}).get("short_summary") or ""))
check("e2e_update_returns_delta", bool(out.get("live_delta")))
check("e2e_state_file_written", live_state.is_file())
check("e2e_update_counter_is_1", live_summary.previous_updates(str(live_state)) == 1)

code, out = run_live_summary("update", live_state, chunk_file=chunk, fake_mode="fenced")
check("e2e_fenced_answer_accepted", out.get("success") is True, str(out)[:160])
check("e2e_counter_increments", live_summary.previous_updates(str(live_state)) == 2)

before = live_summary.load_state(str(live_state))
code, out = run_live_summary("update", live_state, chunk_file=chunk, fake_mode="garbage")
check("e2e_garbage_answer_fails_loudly", code == 1 and out.get("success") is False)
check("e2e_garbage_keeps_last_good_state",
      live_summary.load_state(str(live_state)) == before,
      "an unparsable answer must never blank the live summary")
check("e2e_garbage_does_not_bump_counter",
      live_summary.previous_updates(str(live_state)) == 2)

code, out = run_live_summary("update", live_state, chunk_file=chunk, fake_mode="empty")
check("e2e_blank_answer_rejected", out.get("success") is False)
check("e2e_blank_keeps_last_good_state",
      live_summary.load_state(str(live_state)) == before)

code, out = run_live_summary("update", live_state, chunk_file=chunk, fake_mode="bare")
check("e2e_unwrapped_answer_accepted", out.get("success") is True, str(out)[:160])

empty_chunk = e2e / "empty.txt"
empty_chunk.write_text("   \n", encoding="utf-8")
code, out = run_live_summary("update", live_state, chunk_file=empty_chunk)
check("e2e_empty_chunk_is_skipped_not_failed",
      code == 0 and out.get("skipped") is True, str(out)[:160])

transcript = e2e / "live_transcript.txt"
transcript.write_text("[00:00:05] MIC: обсудили релиз\n[00:00:20] SYSTEM: согласны\n",
                      encoding="utf-8")
code, out = run_live_summary("regen", live_state, transcript_file=transcript)
check("e2e_regen_succeeds", code == 0 and out.get("success") is True, str(out)[:160])
check("e2e_regen_mode_reported", out.get("mode") == "regen")

code, out = run_live_summary("consolidate", live_state)
check("e2e_consolidate_succeeds", code == 0 and out.get("success") is True,
      str(out)[:160])

fresh_state = e2e / "fresh.json"
code, out = run_live_summary("consolidate", fresh_state)
check("e2e_consolidate_on_empty_state_is_skipped",
      code == 0 and out.get("skipped") is True, str(out)[:160])

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} ({len(results)} checks)")
sys.exit(1 if failed else 0)
