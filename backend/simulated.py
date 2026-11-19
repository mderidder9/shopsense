"""Canned answer used when config.json has "mode": "simulated".

Same shape as the real model's answer, so the screens work identically.
Numbers match the original mockup (2017 Accord at 68,420 mi).
"""


# Canned unscheduled problems, so the "Something came up" path can be demoed without the AI.
SIMULATED_ISSUES = {
    "tire": ("Flat or damaged tire.", "Tire plug or patch, or a new tire if the sidewall is damaged.", "fix_today",
             "Ask whether the puncture is in the tread (patchable) or the sidewall (needs a new tire).",
             ["Check the spare", "Check the other tires' tread and pressure"], True),
    "glass": ("Rock chip or crack in the windshield.", "Chip repair, or a new windshield if the crack is long or in your view.",
              "fix_soon", "Ask if it can be repaired instead of replaced, and whether your insurance covers glass.",
              ["Check the wipers aren't catching on the chip"], True),
}
DEFAULT_ISSUE = ("Unscheduled problem (see your note).", "Depends on what the shop finds.", "fix_soon",
                 "Ask the shop to show you the problem before approving any repair.", [], False)


def simulated_issue(visit: dict | None) -> dict:
    reason = (visit or {}).get("reason", "scheduled")
    if reason == "scheduled":
        return {"applies": False, "issue_summary": "", "likely_repair": "", "urgency": "none", "what_to_ask_shop": "",
                "related_checks": [], "while_youre_there": [], "safety_critical": False, "confidence": 0}
    summary, repair, urgency, ask, checks, safety = SIMULATED_ISSUES.get(reason, DEFAULT_ISSUE)
    return {"applies": True, "issue_summary": "SIMULATED · " + summary, "likely_repair": repair, "urgency": urgency,
            "what_to_ask_shop": ask, "related_checks": checks,
            "while_youre_there": ["Engine oil & filter", "Tire rotation"],  # the needed_now items below
            "safety_critical": safety, "confidence": 0.6}


def simulated_result(vehicle: dict, shops: list[dict], visit: dict | None = None) -> dict:
    name = f"{vehicle['year']} {vehicle['make']} {vehicle['model']}"
    ranked = sorted(
        (s for s in shops if s["id"] != "here"),
        key=lambda s: (s["upsell_rate_pct"], -s["rating"], s["price_index"]),
    )[:3]
    return {
        "vehicle_summary": f"SIMULATED · {name} at {int(vehicle['mileage']):,} mi",
        "manual_matches_vehicle": "yes",
        "manual_match_note": "Simulated response - the manual was not read.",
        "schedule_used": "normal",
        "unscheduled_issue": simulated_issue(visit),
        "services": [
            {"service": "Engine oil & filter", "status": "needed_now", "next_due_miles": 67500,
             "interval_from_manual": "Every 7,500 mi", "manual_reference": "Simulated",
             "explanation": "Due at 67,500 mi - you are 920 mi past.", "safety_critical": False, "confidence": 0.95},
            {"service": "Tire rotation", "status": "needed_now", "next_due_miles": 67500,
             "interval_from_manual": "Every 7,500 mi", "manual_reference": "Simulated",
             "explanation": "Rotate with the oil change.", "safety_critical": False, "confidence": 0.9},
            {"service": "Engine air filter", "status": "due_soon", "next_due_miles": 75000,
             "interval_from_manual": "Every 30,000 mi", "manual_reference": "Simulated",
             "explanation": "Due at 75,000 mi. Fine to do now if the price is fair.", "safety_critical": False, "confidence": 0.85},
            {"service": "Brake pads", "status": "inspect", "next_due_miles": None,
             "interval_from_manual": "By inspection", "manual_reference": "Simulated",
             "explanation": "Ask for the pad measurement. Replace under 3 mm.", "safety_critical": True, "confidence": 0.7},
            {"service": "Transmission fluid", "status": "not_due", "next_due_miles": 90000,
             "interval_from_manual": "Every 30,000 mi", "manual_reference": "Simulated",
             "explanation": "Not due until 90,000 mi. Drain & fill, not flush.", "safety_critical": False, "confidence": 0.85},
        ],
        "shop_recommendations": [
            {"rank": i + 1, "shop_id": s["id"], "shop_name": s["name"],
             "reason": f"Simulated: {s['upsell_rate_pct']}% upsell rate, rated {s['rating']}, {s['distance_mi']} mi away.",
             "confidence": 0.6}
            for i, s in enumerate(ranked)
        ],
        "overall_confidence": 0.8,
        "warnings": ["Simulated response - set \"mode\": \"live\" in config.json to call the real AI."],
    }
