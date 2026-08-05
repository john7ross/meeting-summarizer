"""One-line JSON progress events on stdout.

The only channel between a transcription subprocess and whichever front-end
launched it: the desktop JobRunner and the server worker both parse these.
"""
import json


def log_progress(stage, progress, details=""):
    """Send JSON progress updates to the desktop/server stdout readers."""
    print(json.dumps({
        "stage": stage,
        "progress": progress,
        "details": details
    }), flush=True)
