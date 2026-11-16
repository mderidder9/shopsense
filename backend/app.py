"""ShopSense local backend.

Run:  .venv\\Scripts\\python app.py      then open http://localhost:8000

Routes
  /                         the prototype (frontend/index.html)
  /log                      readable view of logs/ai_log.jsonl
  POST /api/maintenance-check   runs the AI step (live or simulated, per config.json)
  POST /api/feedback            records Approve / Decline / Accept / Reject / Correct clicks
  GET  /api/status              current mode + model, and whether a key is set
"""
import html
import json
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).parent
load_dotenv(BASE / ".env", encoding="utf-8-sig")  # loads ANTHROPIC_API_KEY into the environment

import os  # noqa: E402

from ai import AIError, load_config, run_maintenance_check  # noqa: E402
from ai_log import log_event, read_events  # noqa: E402
import service_history  # noqa: E402

FRONTEND = BASE / "frontend"
MANUALS = BASE / "manuals"
MAX_MANUAL_BYTES = 32 * 1024 * 1024  # the API's request size limit

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MANUAL_BYTES + 1024 * 1024


# ---------- the prototype ----------
@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND, filename)


# ---------- API ----------
@app.get("/api/status")
def status():
    cfg = load_config()
    load_dotenv(BASE / ".env", override=True, encoding="utf-8-sig")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return jsonify(mode=cfg["mode"], provider=cfg["provider"], model=cfg["model"],
                   has_key=bool(key) and key != "paste-your-key-here",
                   manuals=sorted(p.name for p in MANUALS.iterdir() if p.suffix.lower() in (".pdf", ".txt")))


def manual_from_request() -> dict:
    """Use the uploaded file if there is one, otherwise a file from the manuals/ folder."""
    up = request.files.get("manual")
    if up and up.filename:
        data, filename = up.read(), up.filename
    else:
        name = Path(request.form.get("manual_name", "")).name  # .name blocks paths like ../../
        path = MANUALS / name
        if not name or not path.is_file():
            raise AIError("Choose an owner's manual first (upload a PDF, or use the sample).")
        data, filename = path.read_bytes(), name
    if len(data) > MAX_MANUAL_BYTES:
        raise AIError("That manual is over 32 MB. Upload just the maintenance-schedule pages.")
    kind = "pdf" if filename.lower().endswith(".pdf") or data[:4] == b"%PDF" else "text"
    return {"filename": filename, "kind": kind, "bytes": data}


@app.post("/api/maintenance-check")
def maintenance_check():
    f = request.form
    try:
        vehicle = {
            "year": f["year"], "make": f["make"], "model": f["model"],
            "trim": f.get("trim", ""), "engine": f.get("engine", ""),
            "mileage": int(str(f["mileage"]).replace(",", "")),
            "driving": f.get("driving", ""),
        }
        if vehicle["mileage"] <= 0:
            raise AIError("Enter the current mileage first.")
        radius = float(f.get("radius", 10))
        manual = manual_from_request()
        history = service_history.get_visits(vehicle)
        return jsonify(ok=True, **run_maintenance_check(vehicle, manual, radius, history=history))
    except AIError as e:
        return jsonify(ok=False, error=str(e)), 400
    except (KeyError, ValueError) as e:
        return jsonify(ok=False, error=f"Missing or invalid input: {e}"), 400


CAR_FIELDS = ("year", "make", "model", "trim", "engine")


@app.get("/api/history")
def get_history():
    car = {k: request.args.get(k, "") for k in CAR_FIELDS}
    return jsonify(ok=True, vehicle=service_history.vehicle_key(car), visits=service_history.get_visits(car))


@app.post("/api/finalize")
def finalize():
    """Save the visit: Accepted items = done at this mileage, Rejected = declined (not done)."""
    data = request.get_json(silent=True) or {}
    car = {k: str(data.get("car", {}).get(k, "")) for k in CAR_FIELDS}
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
    visits = service_history.add_visit(car, visit)
    log_event("finalize", data.get("request_id"), screen="04b AI maintenance check",
              vehicle=service_history.vehicle_key(car), visit=visit,
              decisions=[{k: i.get(k) for k in ("service", "status", "confidence", "decision")} for i in items])
    return jsonify(ok=True, visit=visit, visits=visits)


@app.post("/api/history/reset")
def reset_history():
    car = {k: str((request.get_json(silent=True) or {}).get("car", {}).get(k, "")) for k in CAR_FIELDS}
    service_history.clear(car)
    log_event("history_reset", None, vehicle=service_history.vehicle_key(car))
    return jsonify(ok=True, visits=[])


@app.post("/api/feedback")
def feedback():
    data = request.get_json(silent=True) or {}
    log_event("feedback", data.pop("request_id", None), **data)
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
    if ev == "ai_request":
        v = e.get("input_values", {})
        return (f"[{e.get('mode')}] {e.get('model')} · {v.get('year')} {v.get('make')} {v.get('model')} "
                f"@ {v.get('mileage')} mi · manual: {e.get('manual', {}).get('filename')}")
    if ev == "ai_response":
        return (f"[{e.get('mode')}] confidence {e.get('overall_confidence')} · "
                f"{e.get('latency_ms')} ms · ${e.get('cost_usd')}")
    if ev == "ai_error":
        return f"{e.get('error_type')}: {e.get('error')}"
    if ev == "feedback":
        return f"{e.get('action')} · {e.get('item')} · {e.get('screen')}"
    if ev == "finalize":
        v = e.get("visit", {})
        return (f"{e.get('vehicle')} @ {v.get('mileage')} mi · done {len(v.get('done', []))} · "
                f"declined {len(v.get('declined', []))} · undecided {len(v.get('undecided', []))}")
    if ev == "history_reset":
        return f"cleared service history for {e.get('vehicle')}"
    return ""


if __name__ == "__main__":
    port = load_config().get("port", 8000)
    print(f"\n  ShopSense is running ->  http://localhost:{port}\n  AI log viewer       ->  http://localhost:{port}/log\n")
    app.run(host="127.0.0.1", port=port, debug=False)
