"""ShopSense AI step: "what maintenance is due at this mileage, and which nearby shop should do it?"

Everything that talks to the AI model lives in ONE function: run_maintenance_check().
Which provider / model / effort to use comes from config.json, so you can switch models
without touching this file. Set "mode": "simulated" in config.json to skip the AI entirely.
"""
import base64
import hashlib
import json
import os
import time
import uuid
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from ai_log import log_event
from service_history import history_for_prompt
from simulated import simulated_result

BASE = Path(__file__).parent
CONFIG_FILE = BASE / "config.json"
SHOPS_FILE = BASE / "data" / "shops.json"


class AIError(Exception):
    """An error we can show to the user in plain language."""


def load_config() -> dict:
    # Read fresh on every request, so edits to config.json apply without a restart.
    # utf-8-sig tolerates the invisible marker Windows Notepad sometimes adds.
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise AIError(f"config.json has a typo (line {e.lineno}): check quotes and commas.") from e


# ---------------------------------------------------------------------------
# The instructions ("system prompt") the model gets on every request.
# Kept identical between requests so the provider can cache it.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are ShopSense, an independent car-maintenance advisor. You have no incentive to upsell.

You receive: a vehicle (year, make, model, optional trim/engine), its current odometer mileage, how it is driven, today's date, any service history ShopSense recorded at earlier visits, the vehicle's owner's manual (or an excerpt of its maintenance schedule), and a list of nearby repair shops.

TASK 1 - Scheduled maintenance at this mileage
- Use the maintenance schedule in the provided manual as your only source for intervals. Do not use outside knowledge to invent or change intervals. If the manual does not cover an item, leave it out.
- Choose the NORMAL or SEVERE schedule based on the driving style given, if the manual distinguishes them.
- SERVICE HISTORY comes first: for any item the history covers (match by meaning, not exact wording), count its next due point from the mileage and date it was last DONE, and say so in the explanation (e.g. "done at 25,000 mi on 2026-11-16 - next at 30,000"). An item DECLINED at an earlier visit and not done since is still outstanding: if it was due then, it is needed_now now, and say it was declined at that visit.
- No-history rule (only for items the history doesn't cover): for each interval-based item, find the most recent scheduled point at or below the current mileage (or date). If that point was passed within the last 3,000 miles (or 3 months), mark it needed_now and say "due unless it was done at X mi". If it was passed longer ago than that, assume it was done on schedule then and give the next point (not_due or due_soon).
  Examples at 68,420 mi: oil every 7,500 -> last point 67,500, 920 mi ago -> needed_now. Engine air filter every 30,000 -> last point 60,000, 8,420 mi ago -> assume done -> next 90,000 -> not_due.
- For time-based intervals (e.g. "every 3 years"), use the vehicle's model year and today's date, and say in the explanation that the date of last service is unknown.
- Status meanings:
  needed_now = due or overdue at this mileage/date.
  due_soon   = due within the next 3,000 miles or 3 months.
  not_due    = on the schedule but not due yet (give next_due_miles).
  inspect    = wear item (brakes, tires, battery, wipers) whose need depends on a physical measurement; tell the user what measurement to ask the shop for.
- List at most 12 services: every needed_now, due_soon and inspect item first, then the not_due items shops most often push early (e.g. transmission fluid, coolant, spark plugs, fuel system cleaning). Missing a needed safety-critical service is the costliest error, so when unsure between not_due and inspect for a safety-critical item, choose inspect.
- manual_reference: quote the page number and/or section heading where the interval appears.
- confidence (0 to 1): how sure you are that the status is correct given the manual. Lower it when the manual is ambiguous, the manual may not match the exact vehicle, or the answer depends on unknown history. This is your own estimate, not a measured probability.

TASK 2 - Recommend shops
- Recommend up to 3 shops, ONLY from the list provided (use their exact id and name). Never invent a shop.
- Weigh: trustworthiness (low upsell_rate_pct matters most), rating, price_index (below 1.0 is cheaper than the area median), how much data backs the numbers (owner_reports), and distance. For needed_now work on safety-critical items, a nearby trustworthy shop beats a slightly cheaper distant one.
- In each reason, name the trade-off in one sentence.

GENERAL
- If the manual does not appear to be for this vehicle, set manual_matches_vehicle to "no" or "unclear", explain why, lower all confidences, and add a warning.
- Keep it short - the user is standing at a service counter. Each explanation is ONE plain-language sentence (max ~25 words) for a non-mechanic. manual_reference and interval_from_manual are short phrases, not sentences. Group minor routine inspections into a single item. At most 3 warnings, one sentence each.
"""

# The exact JSON shape the model must return (enforced by the API's structured outputs).
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["vehicle_summary", "manual_matches_vehicle", "manual_match_note", "schedule_used",
                 "services", "shop_recommendations", "overall_confidence", "warnings"],
    "properties": {
        "vehicle_summary": {"type": "string"},
        "manual_matches_vehicle": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "manual_match_note": {"type": "string"},
        "schedule_used": {"type": "string", "enum": ["normal", "severe", "manual_has_one_schedule"]},
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["service", "status", "next_due_miles", "interval_from_manual",
                             "manual_reference", "explanation", "safety_critical", "confidence"],
                "properties": {
                    "service": {"type": "string"},
                    "status": {"type": "string", "enum": ["needed_now", "due_soon", "not_due", "inspect"]},
                    "next_due_miles": {"type": ["integer", "null"]},
                    "interval_from_manual": {"type": "string"},
                    "manual_reference": {"type": "string"},
                    "explanation": {"type": "string"},
                    "safety_critical": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "shop_recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rank", "shop_id", "shop_name", "reason", "confidence"],
                "properties": {
                    "rank": {"type": "integer"},
                    "shop_id": {"type": "string"},
                    "shop_name": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "overall_confidence": {"type": "number"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def load_shops(radius_miles: float) -> list[dict]:
    shops = json.loads(SHOPS_FILE.read_text(encoding="utf-8"))["shops"]
    return [s for s in shops if s["distance_mi"] <= radius_miles]


def build_user_message(vehicle: dict, shops: list[dict], radius_miles: float, history_text: str) -> str:
    """The per-request part of the prompt: the input values, written out plainly."""
    return (
        f"Today's date: {date.today().isoformat()}\n\n"
        "VEHICLE\n"
        f"- Year: {vehicle['year']}\n"
        f"- Make: {vehicle['make']}\n"
        f"- Model: {vehicle['model']}\n"
        f"- Trim: {vehicle.get('trim') or 'not given'}\n"
        f"- Engine: {vehicle.get('engine') or 'not given'}\n"
        f"- Current mileage: {int(vehicle['mileage']):,} mi\n"
        f"- Driving style: {vehicle.get('driving') or 'not given'}\n\n"
        f"{history_text}\n"
        f"NEARBY SHOPS (within {radius_miles:g} mi):\n"
        f"{json.dumps(shops, indent=2)}\n\n"
        "Using the owner's manual attached above, list the maintenance expected at this mileage "
        "and recommend shops, in the required JSON format."
    )


def manual_to_content_block(manual: dict) -> dict:
    """Turn the uploaded manual (PDF or plain text) into a document block the model can read."""
    if manual["kind"] == "pdf":
        source = {"type": "base64", "media_type": "application/pdf",
                  "data": base64.standard_b64encode(manual["bytes"]).decode("ascii")}
    else:
        source = {"type": "text", "media_type": "text/plain",
                  "data": manual["bytes"].decode("utf-8", errors="replace")}
    # cache_control: if the same manual is sent again within ~5 minutes, it's billed at ~10%.
    return {"type": "document", "source": source, "title": manual["filename"],
            "cache_control": {"type": "ephemeral"}}


FAST_MODE_MODELS = {"claude-opus-5-5", "claude-fable-5-2"}


def estimate_cost(usage, prices: dict) -> float:
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    input_cost = (usage.input_tokens + cache_write * 1.25 + cache_read * 0.1) * prices["input"]
    output_cost = usage.output_tokens * prices["output"]
    fast = 2 if getattr(usage, "speed", None) == "fast" else 1  # fast mode bills 2x
    return round(fast * (input_cost + output_cost) / 1_000_000, 4)


# ---------------------------------------------------------------------------
# THE AI CALL - the one function the rest of the app uses.
# ---------------------------------------------------------------------------
def run_maintenance_check(vehicle: dict, manual: dict, radius_miles: float = 10,
                          config_overrides: dict | None = None, history: list[dict] | None = None) -> dict:
    """
    vehicle: {"year", "make", "model", "mileage", optional "trim", "engine", "driving"}
    manual:  {"filename", "kind": "pdf"|"text", "bytes"}
    config_overrides: optional, e.g. {"model": "claude-sonnet-5-5"} - used by speed tests; the app uses config.json.
    history: earlier finalized visits for this vehicle (see service_history.py), oldest first.
    Returns: {"request_id", "mode", "model", "result", "latency_ms", "cost_usd"}
    """
    cfg = {**load_config(), **(config_overrides or {})}
    request_id = uuid.uuid4().hex[:12]
    shops = load_shops(radius_miles)
    history = history or []
    user_message = build_user_message(vehicle, shops, radius_miles, history_for_prompt(history))
    manual_info = {
        "filename": manual["filename"],
        "kind": manual["kind"],
        "size_bytes": len(manual["bytes"]),
        "sha256": hashlib.sha256(manual["bytes"]).hexdigest()[:16],
    }

    # 1) Record exactly what we are about to send.
    log_event("ai_request", request_id,
              mode=cfg["mode"], provider=cfg["provider"], model=cfg["model"], effort=cfg.get("effort"),
              input_values={**vehicle, "radius_miles": radius_miles, "service_history_visits": len(history)},
              service_history=history,
              manual=manual_info,
              instructions=SYSTEM_PROMPT,
              user_message=user_message)

    # 2a) Simulated mode: no AI call, return canned data in the same shape.
    if cfg["mode"] == "simulated":
        result = simulated_result(vehicle, shops)
        log_event("ai_response", request_id, mode="simulated", output=result,
                  overall_confidence=result["overall_confidence"], latency_ms=0, cost_usd=0)
        return {"request_id": request_id, "mode": "simulated", "model": "simulated",
                "result": result, "latency_ms": 0, "cost_usd": 0}

    # 2b) Live mode: call the real model.
    started = time.monotonic()
    try:
        if cfg["provider"] != "anthropic":
            raise AIError(f"Provider '{cfg['provider']}' isn't wired up yet. "
                          "Only 'anthropic' is supported in ai.py today.")

        load_dotenv(BASE / ".env", override=True, encoding="utf-8-sig")  # picks up a newly pasted key
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key or key == "paste-your-key-here":
            raise AIError("No API key yet. Paste your key into backend/.env, save, and click again.")

        client = anthropic.Anthropic(api_key=key, timeout=180.0)
        request = dict(
            model=cfg["model"],
            max_tokens=cfg.get("max_output_tokens", 16000),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                manual_to_content_block(manual),
                {"type": "text", "text": user_message},
            ]}],
            output_config={"effort": cfg.get("effort", "medium"),
                           "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        if cfg.get("fast_mode") and cfg["model"] in FAST_MODE_MODELS:
            # Up to ~2.5x faster output at 2x the per-token price. Ignored for other models.
            request["speed"] = "fast"
        # Haiku has no backup model, so the fallback option is skipped for it automatically.
        if cfg.get("refusal_fallback") and not cfg["model"].startswith("claude-haiku"):
            # If the model's safety filter declines, the API retries on a recommended backup model.
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
        else:
            response = client.messages.create(**request)

        latency_ms = int((time.monotonic() - started) * 1000)
        raw_text = next((b.text for b in response.content if b.type == "text"), "")

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise AIError(f"The model declined this request ({getattr(details, 'category', None)}).")
        if response.stop_reason == "max_tokens":
            raise AIError("The answer was cut off (hit max_output_tokens). Raise it in config.json.")

        result = json.loads(raw_text)
        cost = estimate_cost(response.usage, cfg["price_per_million_tokens"])

        # 3) Record exactly what came back, with the confidence scores pulled out for easy reading.
        log_event("ai_response", request_id,
                  mode="live", model_requested=cfg["model"], model_served=response.model,
                  provider_request_id=getattr(response, "_request_id", None),
                  stop_reason=response.stop_reason,
                  overall_confidence=result.get("overall_confidence"),
                  confidence_by_item={s["service"]: s["confidence"] for s in result.get("services", [])},
                  confidence_by_shop={s["shop_name"]: s["confidence"] for s in result.get("shop_recommendations", [])},
                  output=result,
                  raw_output_text=raw_text,
                  usage=response.usage.to_dict(),
                  cost_usd=cost, latency_ms=latency_ms)

        return {"request_id": request_id, "mode": "live", "model": response.model,
                "result": result, "latency_ms": latency_ms, "cost_usd": cost}

    except Exception as e:
        # 4) Record any error, then turn it into a plain-language message for the screen.
        latency_ms = int((time.monotonic() - started) * 1000)
        log_event("ai_error", request_id, error_type=type(e).__name__, error=str(e),
                  status_code=getattr(e, "status_code", None), latency_ms=latency_ms)
        raise AIError(friendly_error(e)) from e


def friendly_error(e: Exception) -> str:
    if isinstance(e, AIError):
        return str(e)
    if isinstance(e, anthropic.AuthenticationError):
        return "The API key was rejected. Check ANTHROPIC_API_KEY in backend/.env."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This API key isn't allowed to use that model."
    if isinstance(e, anthropic.NotFoundError):
        return "Model not found. Check the model name in config.json."
    if isinstance(e, anthropic.RateLimitError):
        return "Too many requests, or the account is out of credit. Wait a minute or add credit."
    if isinstance(e, anthropic.BadRequestError):
        return f"The request was rejected: {e.message}"
    if isinstance(e, anthropic.APIStatusError):
        return f"The AI service had a problem ({e.status_code}). Try again shortly."
    if isinstance(e, anthropic.APIConnectionError):
        return "Couldn't reach the AI service. Check your internet connection."
    if isinstance(e, json.JSONDecodeError):
        return "The model's answer wasn't valid JSON (see the log)."
    return f"Unexpected error: {e}"
