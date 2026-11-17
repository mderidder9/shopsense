"""ShopSense database: user accounts, their cars (the garage), and saved service visits.

One SQLite file, data/shopsense.db, created automatically on first run. SQLite ships with
Python, so there is nothing extra to install. To start completely fresh, stop the server
and delete data/shopsense.db.

    users     one row per account; the email address is the login
    vehicles  each car belongs to one user (that user's garage)
    visits    each finalized visit belongs to one vehicle
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path(__file__).parent / "data" / "shopsense.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vehicles (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year       TEXT NOT NULL,
    make       TEXT NOT NULL,
    model      TEXT NOT NULL,
    trim       TEXT NOT NULL DEFAULT '',
    engine     TEXT NOT NULL DEFAULT '',
    driving    TEXT NOT NULL DEFAULT 'mixed',   -- highway | mixed | severe
    mileage    INTEGER,                         -- latest known odometer reading
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS vehicles_by_user ON vehicles(user_id);
CREATE TABLE IF NOT EXISTS visits (
    id         INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    date       TEXT NOT NULL,
    mileage    INTEGER NOT NULL,
    request_id TEXT,
    done       TEXT NOT NULL DEFAULT '[]',      -- JSON lists of service names
    declined   TEXT NOT NULL DEFAULT '[]',
    undecided  TEXT NOT NULL DEFAULT '[]',
    details    TEXT NOT NULL DEFAULT '[]',      -- JSON: what the AI said was due for each decided item
    created_at TEXT NOT NULL,
    UNIQUE (vehicle_id, request_id)             -- finalizing the same AI check twice saves it once
);
CREATE INDEX IF NOT EXISTS visits_by_vehicle ON visits(vehicle_id);
"""

VEHICLE_FIELDS = ("year", "make", "model", "trim", "engine", "driving", "mileage")
DRIVING = ("highway", "mixed", "severe")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    DB_FILE.parent.mkdir(exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------- users ----------
def public_user(row) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


def create_user(email: str, password_hash: str, name: str) -> dict | None:
    """Returns the new user, or None if that email already has an account."""
    try:
        with connect() as conn:
            cur = conn.execute("INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)",
                               (email, password_hash, name, _now()))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    return get_user(uid)  # after the commit, so the new row is visible


def get_user(user_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_email(email: str):
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


# ---------- vehicles ----------
def _vehicle(row) -> dict:
    return {k: row[k] for k in ("id", *VEHICLE_FIELDS)}


def list_vehicles(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM vehicles WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    return [_vehicle(r) for r in rows]


def get_vehicle(user_id: int, vehicle_id: int) -> dict | None:
    """Only returns the car if it belongs to this user."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM vehicles WHERE id = ? AND user_id = ?", (vehicle_id, user_id)).fetchone()
    return _vehicle(row) if row else None


def create_vehicle(user_id: int, fields: dict) -> dict:
    fields = {"trim": "", "engine": "", "driving": "mixed", **{k: v for k, v in fields.items() if v is not None or k == "mileage"}}
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO vehicles (user_id, year, make, model, trim, engine, driving, mileage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, *(fields.get(k) for k in VEHICLE_FIELDS), _now()))
        vid = cur.lastrowid
    return get_vehicle(user_id, vid)


def update_vehicle(user_id: int, vehicle_id: int, fields: dict) -> dict | None:
    fields = {k: v for k, v in fields.items() if k in VEHICLE_FIELDS}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)  # keys come from VEHICLE_FIELDS only
        with connect() as conn:
            conn.execute(f"UPDATE vehicles SET {sets} WHERE id = ? AND user_id = ?",
                         (*fields.values(), vehicle_id, user_id))
    return get_vehicle(user_id, vehicle_id)


# ---------- visits (service history) ----------
def _visit(row) -> dict:
    return {"date": row["date"], "mileage": row["mileage"], "request_id": row["request_id"],
            **{k: json.loads(row[k]) for k in ("done", "declined", "undecided", "details")}}


def get_visits(vehicle_id: int) -> list[dict]:
    """Oldest first."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM visits WHERE vehicle_id = ? ORDER BY mileage, id", (vehicle_id,)).fetchall()
    return [_visit(r) for r in rows]


def add_visit(vehicle_id: int, visit: dict) -> list[dict]:
    """Save a finalized visit (ignored if this AI check was already saved) and raise the car's
    mileage to the visit's if it's higher. Returns all visits for the car."""
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO visits (vehicle_id, date, mileage, request_id, done, declined, undecided, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vehicle_id, visit["date"], visit["mileage"], visit.get("request_id"),
             *(json.dumps(visit.get(k, []), ensure_ascii=False) for k in ("done", "declined", "undecided", "details")),
             _now()))
        conn.execute("UPDATE vehicles SET mileage = ? WHERE id = ? AND (mileage IS NULL OR mileage < ?)",
                     (visit["mileage"], vehicle_id, visit["mileage"]))
    return get_visits(vehicle_id)


def clear_visits(vehicle_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM visits WHERE vehicle_id = ?", (vehicle_id,))
