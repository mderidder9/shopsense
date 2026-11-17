"""Pick the owner's manual for a car, using manuals/manuals.json.

A manual is used only when the car's year, make, model, trim and engine (engine includes the
drivetrain, e.g. "3.8L V6 GDi · FWD") ALL match an entry, ignoring capitals and extra spaces.
Every one of those fields is required in the entry; an incomplete entry never matches.
No match -> None, and the AI check doesn't run, so a manual for a different car is never sent.
"""
import json
from pathlib import Path

MANUALS = Path(__file__).parent / "manuals"
INDEX_FILE = MANUALS / "manuals.json"
REQUIRED = ("years", "make", "model", "trims", "engines", "file")


class ManualIndexError(Exception):
    pass


def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def _entries() -> list[dict]:
    # Read fresh each time, so edits to manuals.json apply without a restart.
    try:
        entries = json.loads(INDEX_FILE.read_text(encoding="utf-8-sig")).get("manuals", [])
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        raise ManualIndexError(f"manuals/manuals.json has a typo (line {e.lineno}): check quotes and commas.") from e
    # Fail closed: skip entries missing any required field, or whose file isn't there.
    return [m for m in entries
            if all(m.get(k) for k in REQUIRED) and (MANUALS / Path(m["file"]).name).is_file()]


def _year_ok(m: dict, car: dict) -> bool:
    return _norm(car.get("year")) in {_norm(y) for y in m["years"]}


def _trim_ok(m: dict, car: dict) -> bool:
    return _norm(car.get("trim")) in {_norm(t) for t in m["trims"]}


def _engine_ok(m: dict, car: dict) -> bool:
    return _norm(car.get("engine")) in {_norm(e) for e in m["engines"]}


def find_manual(car: dict) -> dict | None:
    """Returns {"file", "label", "path"} for this exact car's manual, or None."""
    for m in _entries():
        if (_norm(m["make"]) == _norm(car.get("make")) and _norm(m["model"]) == _norm(car.get("model"))
                and _year_ok(m, car) and _trim_ok(m, car) and _engine_ok(m, car)):
            name = Path(m["file"]).name  # .name blocks paths like ../../
            return {"file": name, "label": m.get("label") or name, "path": MANUALS / name}
    return None


def missing_reason(car: dict) -> str:
    """Plain-language reason a car has no manual, naming what didn't match."""
    same_model = [m for m in _entries()
                  if _norm(m["make"]) == _norm(car.get("make")) and _norm(m["model"]) == _norm(car.get("model"))]
    if not same_model:
        return f"No owner's manual on file for the {car.get('make')} {car.get('model')} yet."
    m = same_model[0]
    if not any(_year_ok(x, car) for x in same_model):
        covers = ", ".join(str(y) for y in m["years"])
        return f"The {m['make']} {m['model']} manual on file covers model year {covers}, not {car.get('year')}."
    if not any(_trim_ok(x, car) for x in same_model if _year_ok(x, car)):
        return f"The {m['make']} {m['model']} manual on file doesn't list the {car.get('trim') or '(no trim)'} trim."
    return (f"The {m['make']} {m['model']} manual on file covers {', '.join(m['engines'])}, "
            f"not {car.get('engine') or '(no engine given)'}.")


def summary(car: dict) -> dict:
    """What the app shows: which manual a car will be checked against, or why there isn't one (no file path)."""
    try:
        m = find_manual(car)
    except ManualIndexError as e:
        return {"manual": None, "manual_note": str(e)}
    if m:
        return {"manual": {"file": m["file"], "label": m["label"]}, "manual_note": None}
    return {"manual": None, "manual_note": missing_reason(car) + " ShopSense only checks a car against its own manual."}
