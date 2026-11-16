"""Service history: what was done at each finalized visit, saved in data/service_history.json.

One plain JSON file (no database). Visits are grouped by vehicle, e.g.
"2025 Kia Telluride EX · 3.8L V6 GDi · FWD". The next AI check reads this so it
knows when each service was last done.
"""
import json
import threading
from pathlib import Path

HISTORY_FILE = Path(__file__).parent / "data" / "service_history.json"
_lock = threading.Lock()


def vehicle_key(car: dict) -> str:
    name = " ".join(str(car.get(k, "")).strip() for k in ("year", "make", "model", "trim") if car.get(k))
    return f"{name} · {car['engine']}" if car.get("engine") else name


def _load() -> dict:
    if not HISTORY_FILE.exists():
        return {"vehicles": {}}
    return json.loads(HISTORY_FILE.read_text(encoding="utf-8-sig"))


def _save(data: dict) -> None:
    HISTORY_FILE.parent.mkdir(exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_visits(car: dict) -> list[dict]:
    with _lock:
        return _load()["vehicles"].get(vehicle_key(car), [])


def add_visit(car: dict, visit: dict) -> list[dict]:
    """Append a visit (ignores a second finalize of the same AI request). Returns all visits."""
    with _lock:
        data = _load()
        visits = data["vehicles"].setdefault(vehicle_key(car), [])
        if not any(v.get("request_id") == visit.get("request_id") for v in visits):
            visits.append(visit)
        _save(data)
        return visits


def clear(car: dict) -> None:
    with _lock:
        data = _load()
        data["vehicles"].pop(vehicle_key(car), None)
        _save(data)


def history_for_prompt(visits: list[dict]) -> str:
    """Plain-text history for the AI, oldest visit first."""
    if not visits:
        return "SERVICE HISTORY: none recorded. Use the no-history rule.\n"
    lines = ["SERVICE HISTORY (recorded by ShopSense at earlier visits; oldest first):"]
    for v in visits:
        lines.append(f"- Visit on {v['date']} at {v['mileage']:,} mi:")
        if v.get("details"):
            # Includes what was due for each item, e.g. an inspection vs. a replacement.
            for d in v["details"]:
                lines.append(f"    {d['decision'].upper()}: {d['service']}  (what was due then: {d.get('what_was_due') or '?'} "
                             f"| manual interval: {d.get('interval') or '?'})")
        else:
            lines.append(f"    DONE: {'; '.join(v['done']) or 'nothing'}")
            lines.append(f"    DECLINED (not done): {'; '.join(v['declined']) or 'nothing'}")
    return "\n".join(lines) + "\n"
