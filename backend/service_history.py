"""Service history helpers: what was done at each finalized visit.

Visits are stored in the database (see db.py), one list per car in a user's garage.
The next AI check for that car reads them so it knows when each service was last done.
"""


def vehicle_key(car: dict) -> str:
    """A readable name for a car, e.g. "2025 Kia Telluride EX · 3.8L V6 GDi · FWD" (used in the log)."""
    name = " ".join(str(car.get(k, "")).strip() for k in ("year", "make", "model", "trim") if car.get(k))
    return f"{name} · {car['engine']}" if car.get("engine") else name


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
