"""Simple local log: one JSON object per line in logs/ai_log.jsonl.

Every event shares a `request_id`, so you can follow one AI request from
"what we sent" -> "what came back" -> "what the user clicked".
Open the file in any text editor, or visit http://localhost:8000/log.
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "ai_log.jsonl"
_lock = threading.Lock()


def log_event(event: str, request_id: str | None = None, **fields) -> dict:
    """Append one event. `event` is e.g. ai_request, ai_response, ai_error, feedback."""
    entry = {
        "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "event": event,
        "request_id": request_id,
        **fields,
    }
    LOG_DIR.mkdir(exist_ok=True)
    with _lock, LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


def read_events(limit: int = 200) -> list[dict]:
    """Newest first, for the /log page."""
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"event": "unreadable_line", "raw": line})
    return out
