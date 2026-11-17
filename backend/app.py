"""ShopSense local backend.

Run:  .venv\\Scripts\\python app.py      then open http://localhost:8000

Routes
  /                                     the prototype (frontend/index.html)
  /log                                  readable view of logs/ai_log.jsonl
  GET  /api/status                      current mode + model, and whether a key is set
  Accounts (the email address is the login)
  POST /api/auth/register               {email, password, name} -> creates the account and signs in
  POST /api/auth/login                  {email, password}
  POST /api/auth/logout
  GET  /api/me                          the signed-in user and their garage (401 if signed out)
  Garage (signed in; each car belongs to one user)
  POST  /api/vehicles                   add a car
  PATCH /api/vehicles/<id>              change a car (trim, mileage, driving, ...)
  GET   /api/vehicles/<id>/history      saved visits for that car
  POST  /api/vehicles/<id>/finalize     save this visit (Accepted = done, Rejected = declined)
  POST  /api/vehicles/<id>/history/reset
  POST  /api/maintenance-check          runs the AI step for one of your cars (live or simulated, per config.json),
                                        using the manual for that exact car (manuals/manuals.json)
  POST  /api/feedback                   records Approve / Decline / Accept / Reject clicks
"""
import html
import json
import re
import secrets
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).parent
load_dotenv(BASE / ".env", encoding="utf-8-sig")  # loads ANTHROPIC_API_KEY into the environment

import os  # noqa: E402

from ai import AIError, load_config, run_maintenance_check  # noqa: E402
from ai_log import log_event, read_events  # noqa: E402
import db  # noqa: E402
import manuals  # noqa: E402
import service_history  # noqa: E402

FRONTEND = BASE / "frontend"
MAX_MANUAL_BYTES = 32 * 1024 * 1024  # the AI API's request size limit
SECRET_FILE = BASE / "data" / "secret_key"
MIN_PASSWORD = 8


def secret_key() -> str:
    """Signs the login cookie. Made once and kept in data/secret_key (never committed).
    Deleting that file signs everyone out."""
    if os.environ.get("SHOPSENSE_SECRET_KEY"):
        return os.environ["SHOPSENSE_SECRET_KEY"]
    if not SECRET_FILE.exists():
        SECRET_FILE.parent.mkdir(exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
    return SECRET_FILE.read_text(encoding="utf-8").strip()


app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=1024 * 1024,     # requests are small JSON/forms; manuals are read from disk
    SECRET_KEY=secret_key(),
    SESSION_COOKIE_HTTPONLY=True,       # page scripts can't read the login cookie
    SESSION_COOKIE_SAMESITE="Lax",      # other websites can't send requests as you
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
db.init()


# ---------- the prototype ----------
@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND, filename)


# ---------- accounts ----------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Checked against when the email has no account, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = db.get_user(session["uid"]) if session.get("uid") else None
        if not user:
            session.clear()
            return jsonify(ok=False, error="Please sign in first.", signed_out=True), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def start_session(user) -> None:
    session.clear()
    session["uid"] = user["id"]
    session.permanent = True


def with_manual(vehicle: dict | None) -> dict | None:
    """Adds which owner's manual this car will be checked against ("manual"), or why there's none ("manual_note")."""
    return {**vehicle, **manuals.summary(vehicle)} if vehicle else None


def garage(user_id: int) -> list[dict]:
    return [with_manual(v) for v in db.list_vehicles(user_id)]


def me_payload(user) -> dict:
    return {"ok": True, "user": db.public_user(user), "vehicles": garage(user["id"])}


@app.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    name = str(data.get("name", "")).strip()[:60]
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify(ok=False, error="Enter a valid email address."), 400
    if len(password) < MIN_PASSWORD:
        return jsonify(ok=False, error=f"Use at least {MIN_PASSWORD} characters for your password."), 400
    if len(password) > 200:
        return jsonify(ok=False, error="That password is too long."), 400
    user = db.create_user(email, generate_password_hash(password), name)
    if not user:
        return jsonify(ok=False, error="An account with that email already exists. Sign in instead."), 409
    start_session(user)
    log_event("account_created", None, user_id=user["id"])
    return jsonify(me_payload(user))


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    user = db.get_user_by_email(email) if email else None
    ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
    if not (user and ok):
        return jsonify(ok=False, error="That email and password don't match an account."), 401
    start_session(user)
    return jsonify(me_payload(user))


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required
def me():
    return jsonify(me_payload(g.user))


# ---------- garage ----------
def vehicle_fields(data: dict, partial: bool) -> dict:
    """Validate car fields from the client. Raises ValueError with a message for the screen."""
    out = {}
    for k in ("year", "make", "model", "trim", "engine"):
        if k in data:
            out[k] = str(data[k]).strip()[:60]
    if not partial and not all(out.get(k) for k in ("year", "make", "model")):
        raise ValueError("Pick the car's year, make and model.")
    if "year" in out and not re.fullmatch(r"(19|20)\d\d", out["year"]):
        raise ValueError("Pick the car's model year.")
    if "driving" in data:
        if data["driving"] not in db.DRIVING:
            raise ValueError("Pick how you drive.")
        out["driving"] = data["driving"]
    if "mileage" in data:
        m = data["mileage"]
        try:
            out["mileage"] = None if m in (None, "", 0) else int(str(m).replace(",", ""))
        except ValueError:
            raise ValueError("Check the mileage.") from None
        if out["mileage"] is not None and not 0 < out["mileage"] < 2_000_000:
            raise ValueError("Check the mileage.")
    return out


def owned_vehicle(vehicle_id):
    """The signed-in user's car, or None (also None for someone else's car)."""
    try:
        return db.get_vehicle(g.user["id"], int(vehicle_id))
    except (TypeError, ValueError):
        return None


NOT_YOURS = ({"ok": False, "error": "That car isn't in your garage."}, 404)


@app.post("/api/vehicles")
@login_required
def add_vehicle():
    try:
        fields = vehicle_fields(request.get_json(silent=True) or {}, partial=False)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    vehicle = db.create_vehicle(g.user["id"], {"driving": "mixed", **fields})
    return jsonify(ok=True, vehicle=with_manual(vehicle), vehicles=garage(g.user["id"]))


@app.patch("/api/vehicles/<vehicle_id>")
@login_required
def edit_vehicle(vehicle_id):
    if not owned_vehicle(vehicle_id):
        return NOT_YOURS
    try:
        fields = vehicle_fields(request.get_json(silent=True) or {}, partial=True)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    vehicle = db.update_vehicle(g.user["id"], int(vehicle_id), fields)
    return jsonify(ok=True, vehicle=with_manual(vehicle), vehicles=garage(g.user["id"]))


@app.get("/api/vehicles/<vehicle_id>/history")
@login_required
def get_history(vehicle_id):
    car = owned_vehicle(vehicle_id)
    if not car:
        return NOT_YOURS
    return jsonify(ok=True, vehicle=service_history.vehicle_key(car), visits=db.get_visits(car["id"]))


@app.post("/api/vehicles/<vehicle_id>/finalize")
@login_required
def finalize(vehicle_id):
    """Save the visit: Accepted items = done at this mileage, Rejected = declined (not done)."""
    car = owned_vehicle(vehicle_id)
    if not car:
        return NOT_YOURS
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    try:
        mileage = int(data["mileage"])
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="Missing mileage for this visit."), 400
    if not any(i.get("decision") in ("accept", "reject") for i in items):
        return jsonify(ok=False, error="Accept or reject at least one item first."), 400
    visit = {
        "date": date.today().isoformat(),
        "mileage": mileage,
        "request_id": data.get("request_id"),
        "done": [i["service"] for i in items if i.get("decision") == "accept"],
        "declined": [i["service"] for i in items if i.get("decision") == "reject"],
        "undecided": [i["service"] for i in items if i.get("decision") not in ("accept", "reject")],
        # What the AI said was due for each decided item, so the next check knows exactly what was done.
        "details": [{"service": i["service"], "decision": "done" if i["decision"] == "accept" else "declined",
                     "ai_status": i.get("status"), "what_was_due": i.get("explanation"), "interval": i.get("interval")}
                    for i in items if i.get("decision") in ("accept", "reject")],
    }
    visits = db.add_visit(car["id"], visit)
    log_event("finalize", data.get("request_id"), screen="04b AI maintenance check",
              user_id=g.user["id"], vehicle_id=car["id"], vehicle=service_history.vehicle_key(car), visit=visit,
              decisions=[{k: i.get(k) for k in ("service", "status", "confidence", "decision")} for i in items])
    return jsonify(ok=True, visit=visit, visits=visits, vehicle=with_manual(db.get_vehicle(g.user["id"], car["id"])))


@app.post("/api/vehicles/<vehicle_id>/history/reset")
@login_required
def reset_history(vehicle_id):
    car = owned_vehicle(vehicle_id)
    if not car:
        return NOT_YOURS
    db.clear_visits(car["id"])
    log_event("history_reset", None, user_id=g.user["id"], vehicle_id=car["id"],
              vehicle=service_history.vehicle_key(car))
    return jsonify(ok=True, visits=[])


# ---------- API ----------
@app.get("/api/status")
def status():
    cfg = load_config()
    load_dotenv(BASE / ".env", override=True, encoding="utf-8-sig")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return jsonify(mode=cfg["mode"], provider=cfg["provider"], model=cfg["model"],
                   has_key=bool(key) and key != "paste-your-key-here")


def manual_for(car: dict) -> dict:
    """The owner's manual for this exact car (year, make, model, trim and engine must all match manuals/manuals.json).
    The browser can't choose the file, so a manual for a different car is never sent."""
    try:
        found = manuals.find_manual(car)
    except manuals.ManualIndexError as e:
        raise AIError(str(e)) from e
    if not found:
        raise AIError(manuals.missing_reason(car) + " So ShopSense can't check this car yet.")
    data = found["path"].read_bytes()
    if len(data) > MAX_MANUAL_BYTES:
        raise AIError("That manual is over 32 MB. Save just the maintenance-schedule pages.")
    kind = "pdf" if found["file"].lower().endswith(".pdf") or data[:4] == b"%PDF" else "text"
    return {"filename": found["file"], "kind": kind, "bytes": data}


@app.post("/api/maintenance-check")
@login_required
def maintenance_check():
    f = request.form
    car = owned_vehicle(f.get("vehicle_id"))
    if not car:
        return jsonify(ok=False, error="Finish adding this car to your garage first (screens 02-03)."), 400
    try:
        # The car itself comes from your garage; mileage and driving come from this check.
        vehicle = {
            **{k: car[k] for k in ("year", "make", "model", "trim", "engine")},
            "mileage": int(str(f["mileage"]).replace(",", "")),
            "driving": f.get("driving", ""),
        }
        if vehicle["mileage"] <= 0:
            raise AIError("Enter the current mileage first.")
        radius = float(f.get("radius", 10))
        manual = manual_for(car)
        history = db.get_visits(car["id"])
        return jsonify(ok=True, **run_maintenance_check(
            vehicle, manual, radius, history=history,
            log_context={"user_id": g.user["id"], "vehicle_id": car["id"]}))
    except AIError as e:
        return jsonify(ok=False, error=str(e)), 400
    except (KeyError, ValueError) as e:
        return jsonify(ok=False, error=f"Missing or invalid input: {e}"), 400


@app.post("/api/feedback")
def feedback():
    data = request.get_json(silent=True) or {}
    data.pop("user_id", None)  # only the server says who you are
    log_event("feedback", data.pop("request_id", None), user_id=session.get("uid"), **data)
    return jsonify(ok=True)


# ---------- log viewer ----------
@app.get("/log")
def log_page():
    rows = []
    for e in read_events():
        colour = {"ai_request": "#2b6cb0", "ai_response": "#2f855a", "ai_error": "#c53030",
                  "feedback": "#b7791f", "finalize": "#6b46c1"}.get(e.get("event"), "#555")
        summary = summarize(e)
        rows.append(
            f"<details><summary><b style='color:{colour}'>{html.escape(str(e.get('event')))}</b> "
            f"<code>{html.escape(str(e.get('time', '')))}</code> "
            f"<code>id={html.escape(str(e.get('request_id')))}</code> {html.escape(summary)}</summary>"
            f"<pre>{html.escape(json.dumps(e, indent=2, ensure_ascii=False))}</pre></details>")
    body = "".join(rows) or "<p>No events yet. Run a check in the prototype.</p>"
    return (
        "<!doctype html><meta charset='utf-8'><title>ShopSense AI log</title>"
        "<style>body{font:14px system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px}"
        "details{border-bottom:1px solid #ddd;padding:6px 0}pre{white-space:pre-wrap;background:#f6f6f6;"
        "padding:10px;font-size:12px}code{color:#666}</style>"
        "<h1>ShopSense AI log</h1><p>Newest first · click a row to expand · raw file: "
        "<code>backend/logs/ai_log.jsonl</code> · <a href='/log'>refresh</a></p>" + body)


def summarize(e: dict) -> str:
    ev = e.get("event")
    who = f"user {e['user_id']} · " if e.get("user_id") else ""
    if ev == "ai_request":
        v = e.get("input_values", {})
        return (f"{who}[{e.get('mode')}] {e.get('model')} · {v.get('year')} {v.get('make')} {v.get('model')} "
                f"@ {v.get('mileage')} mi · manual: {e.get('manual', {}).get('filename')}")
    if ev == "ai_response":
        return (f"[{e.get('mode')}] confidence {e.get('overall_confidence')} · "
                f"{e.get('latency_ms')} ms · ${e.get('cost_usd')}")
    if ev == "ai_error":
        return f"{e.get('error_type')}: {e.get('error')}"
    if ev == "feedback":
        return f"{who}{e.get('action')} · {e.get('item')} · {e.get('screen')}"
    if ev == "finalize":
        v = e.get("visit", {})
        return (f"{who}{e.get('vehicle')} @ {v.get('mileage')} mi · done {len(v.get('done', []))} · "
                f"declined {len(v.get('declined', []))} · undecided {len(v.get('undecided', []))}")
    if ev == "history_reset":
        return f"{who}cleared service history for {e.get('vehicle')}"
    if ev == "account_created":
        return f"{who}new account"
    return ""


if __name__ == "__main__":
    port = load_config().get("port", 8000)
    print(f"\n  ShopSense is running ->  http://localhost:{port}\n  AI log viewer       ->  http://localhost:{port}/log\n")
    app.run(host="127.0.0.1", port=port, debug=False)
