"""Fake transcription backend: emits the same stdout protocol as processor.py
(progress JSON lines, then a terminal result line) without whisper/ffmpeg/GPU.
Used only by the worker self-test to prove status routing deterministically.
"""
import argparse
import json
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--label", required=True)
parser.add_argument("--steps", type=int, default=4)
parser.add_argument("--delay", type=float, default=0.05)
parser.add_argument("--fail", action="store_true")
args = parser.parse_args()

stages = ["status.extracting", "status.transcribing",
          "status.transcribing", "status.complete"]

for i in range(args.steps):
    stage = stages[min(i, len(stages) - 1)]
    print(json.dumps({
        "stage": stage,
        "progress": int((i + 1) / args.steps * 80),
        "details": args.label,  # carries our label so cross-talk is detectable
    }), flush=True)
    time.sleep(args.delay)

if args.fail:
    print(json.dumps({"success": False, "error": "boom " + args.label}), flush=True)
    sys.exit(1)

print(json.dumps({"success": True,
                  "output": args.label + "_raw.txt",
                  "trace": args.label + "_trace.json"}), flush=True)
