"""Per-stage timing spans written next to the artifacts as ``*_trace.json``.

Feeds the Diagnostics window's timings and Gantt-style profile, and is the
evidence for where a slow run actually spent its time.
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime


class PerformanceTracer:
    """Collect profiling data for the renderer flame graph."""

    def __init__(self):
        self.trace_id = str(uuid.uuid4())
        self.trace_name = "video_processing"
        self.start_time = time.time()
        self.spans = []
        self.current_span = None

    def start_span(self, name, metadata=None):
        """Start a new span."""
        span = {
            "id": str(uuid.uuid4()),
            "name": name,
            "start": time.time(),
            "metadata": metadata or {}
        }
        self.spans.append(span)
        self.current_span = span

        print(f"[TRACE DEBUG] Started span: {name}", file=sys.stderr, flush=True)

        return span

    def end_span(self):
        """Finish the current span."""
        if self.current_span:
            self.current_span["end"] = time.time()
            self.current_span["duration"] = (self.current_span["end"] - self.current_span["start"]) * 1000

            print(
                f"[TRACE DEBUG] Ended span: {self.current_span['name']}, "
                f"duration: {self.current_span['duration']:.2f}ms",
                file=sys.stderr,
                flush=True
            )

            self.current_span = None

    def save_trace(self, output_dir, base_name):
        """Save trace data as JSON for the flame graph."""
        trace_data = {
            "traceId": self.trace_id,
            "name": self.trace_name,
            "timestamp": datetime.now().isoformat(),
            "startTime": self.start_time,
            "endTime": time.time(),
            "duration": (time.time() - self.start_time) * 1000,
            "status": "completed",
            "spans": self.spans
        }

        trace_file = os.path.join(output_dir, f"{base_name}_trace.json")

        print(f"[TRACE DEBUG] Saving trace to: {trace_file}", file=sys.stderr, flush=True)
        print(f"[TRACE DEBUG] Total spans: {len(self.spans)}", file=sys.stderr, flush=True)
        print(f"[TRACE DEBUG] Total duration: {trace_data['duration']:.2f}ms", file=sys.stderr, flush=True)

        with open(trace_file, 'w', encoding='utf-8') as f:
            json.dump(trace_data, f, indent=2, ensure_ascii=False)

        print("[TRACE DEBUG] Trace file saved successfully", file=sys.stderr, flush=True)

        return trace_file
