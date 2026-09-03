"""Fake processor.py: real argv shape, real stdout protocol, writes a real
raw transcript file — but no whisper/ffmpeg/GPU. Pipeline self-test only.
"""
import argparse
import json
import os
import time

p = argparse.ArgumentParser()
p.add_argument("--video", required=True)
p.add_argument("--language", default="ru")
p.add_argument("--model", default="medium")
p.add_argument("--engine", default="faster-whisper")
p.add_argument("--device", default="auto")
p.add_argument("--output-dir", dest="output_dir", required=True)
a = p.parse_args()

os.makedirs(a.output_dir, exist_ok=True)
stem = os.path.splitext(os.path.basename(a.video))[0]

print(json.dumps({"stage": "status.extracting", "progress": 5,
                  "details": "WAV_PATH:" + os.path.join(a.output_dir, stem + "_temp.wav")}),
      flush=True)
time.sleep(0.03)
print(json.dumps({"stage": "status.transcribing", "progress": 29,
                  "details": f"Auto-selected device: {a.device}"}), flush=True)
time.sleep(0.03)
for i in range(2):
    print(json.dumps({"stage": "status.transcribing", "progress": 35 + i * 20,
                      "details": f"Transcribing chunk {i + 1}/2..."}), flush=True)
    time.sleep(0.03)

out = os.path.join(a.output_dir, stem + "_raw.txt")
with open(out, "w", encoding="utf-8") as f:
    f.write("[00:00:00] Привет, это тестовая транскрипция.\n"
            "[00:00:05] Обсудили задачи и сроки.")

print(json.dumps({"stage": "status.complete", "progress": 80,
                  "details": "Processing complete: " + out}), flush=True)
print(json.dumps({"success": True, "output": out,
                  "trace": os.path.join(a.output_dir, stem + "_trace.json")}), flush=True)
