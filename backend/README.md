# ShopSense: local AI backend

This folder runs the ShopSense prototype on your own laptop and connects **one** AI step to a real model:

> **What maintenance is due at this mileage, and which nearby shop should do it?**
> Input: year, make, model, mileage (plus trim, engine, driving style) and the car's **owner's manual**.
> Output: each service marked *needed now / due soon / inspect / not due*, with the manual page it came from, a confidence score, and up to 3 recommended shops with reasons.

Everything else in the prototype (photo reading, quote check, prices, savings, peer stats) is still **simulated**.

| What | Real or simulated |
|---|---|
| Screen **04b**: maintenance due + recommended shops | **Real AI** (or simulated, if you flip the switch) |
| "Due now / Next up" on screen 04, "AI PICK" tags on screen 08 | Filled from the 04b answer |
| Shop list (names, ratings, prices, upsell rates) | **Sample data** from the mockup, in `data/shops.json`. These aren't real businesses. |
| Everything else | Simulated, unchanged from the mockup |

---

## Setup (one time, about 10 minutes)

These steps are for Windows. Mac notes are in brackets.

### 1. Install Python 3.12
Open **PowerShell** (Start menu → type "PowerShell") and paste:
```
winget install --id Python.Python.3.12 -e --scope user
```
Alternatively, download it from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** during install.
Close PowerShell afterwards so it picks up the new install. [Mac: `brew install python@3.12`, or use the python.org installer.]

### 2. Get your own API key
1. Go to https://console.anthropic.com and sign up or log in.
2. **Billing**: add a small amount of credit (about $5 is plenty for class testing).
3. **API Keys** → **Create Key** → copy it. It starts with `sk-ant-`.

Use your own key. Don't borrow a teammate's, and never paste a key into chat, slides, email or a shared doc.

### 3. Get the project
Copy the whole `ShopSense` folder to your own laptop, for example from the shared drive or a zip.
**Don't copy anyone's `.env` file.** That file holds their key. If you received one, delete it.

### 4. Run setup
Open the `backend` folder and **double-click `setup.bat`**. It:
- creates a private Python environment (`.venv`),
- installs the 3 libraries the project needs,
- creates your `.env` file and opens it in Notepad.

In Notepad, replace `paste-your-key-here` with your key so the line reads `ANTHROPIC_API_KEY=sk-ant-...`. Then **save** and close.

[Mac: open Terminal in the `backend` folder and run
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp .env.example .env && open -e .env`]

## Run it

1. Double-click **`start.bat`**. A black window opens; leave it open while you use the prototype.
   [Mac: `.venv/bin/python app.py`, then open http://localhost:8000]
2. Your browser opens **http://localhost:8000**. If it says "can't connect", wait 2 seconds and refresh.
3. The app opens as a **click-through phone app**, one screen at a time, starting at 01 Create account. Click through: Create account → Next: mileage → Finish setup → on the Garage screen, **"What's due? Ask ShopSense AI"** opens screen **04b**. The bar at the top has **← Back**, a link to the **AI log**, and **All screens** to switch to the side-by-side design view. "Click-through mode" switches back.
   The demo car is set up already: 2025 Kia Telluride EX, FWD, severe/city driving.
4. On screen **03**, type the car's **current mileage**. The field starts empty, and the AI check won't run without it.
5. On screen 04b, click **Check what's due + best shops**. Expect about 10–15 seconds. The demo always checks against the saved Kia manual, `manuals/2025-Kia-Telluride-scheduled-maintenance.txt`. To demo another car, change `DEMO_MANUAL` near the top of the script in `frontend/index.html` to another file in `manuals/`.
6. Optionally change the radius on screen 08 before running the check.
7. For each item, click **Accept** (this service is done at this visit) or **Reject** (not done). Each click goes in the log.
8. Click **Finalize maintenance**. This saves the visit (date, mileage, done, declined) to `data/service_history.json`. The next check for this car sends that history to the AI, so it counts from when each service was last done and still flags declined items. Items you didn't decide are saved as not done.
   After finalizing, the app goes home to the **Garage** (04). Its **"Back at the shop?"** card shows the last service and asks for the current mileage. At the next visit, type the mileage and click **Check what's due →**: the AI check runs on 04b using the saved history. A mileage that isn't higher than the last visit's is refused, to catch typos.
   Screen 04b shows the last saved visit. Click **Reset** next to it to clear the history between demos.
   Demo tip: both visits will be dated today, so a second visit at a higher mileage on the same day makes the AI add a warning asking you to confirm the dates.

To stop the server, close the black window (or press `Ctrl+C` in it).

> The page must be opened through `http://localhost:8000`. Double-clicking `index.html` won't reach the AI.

---

## Switching models, or going back to simulated (`config.json`)

Open `config.json` in Notepad. Changes apply on the **next click**, with no restart needed.

```json
{
  "mode": "live",                  // "live" = real AI,  "simulated" = canned answers, no key needed, free
  "provider": "anthropic",         // only "anthropic" is wired up in ai.py today
  "model": "claude-opus-5-5",      // the model to use
  "effort": "low",                 // low | medium | high | xhigh | max - how hard the model thinks (more = slower, costlier)
  "fast_mode": true,               // Opus 5.5 only: ~3-4x faster here, at 2x the price per token
  "max_output_tokens": 16000,      // the longest answer allowed
  "refusal_fallback": true,        // if the model's safety filter declines, retry automatically on a backup model
  "price_per_million_tokens": { "input": 4.00, "output": 20.00 },   // standard prices; doubled automatically in fast mode
  "port": 8000
}
```
(The real file has no `//` comments. JSON doesn't allow them, so don't add any.)

**Speed test** (2025 Kia Telluride, 25,000 mi, Kia's online schedule as the manual, Nov 2026). Every setting gave answers matching the manual:

| Setting | Time | Cost per check |
|---|---|---|
| Opus 5.5 · low · **fast mode** (default) | ~11 s | ~$0.19 |
| Opus 5.5 · medium · fast mode | ~17 s | ~$0.26 |
| Opus 5.5 · low | ~38 s | ~$0.10 |
| Sonnet 5.5 · low | ~43 s | ~$0.07 |
| Opus 5.5 · medium | ~56 s | ~$0.12 |

These are single runs, so expect some variation. A full PDF manual is slower and costs more than a one-page schedule.

**To try another Claude model in class**, change `"model"` and update the prices to match:

| Model | `"model"` value | Input $/M | Output $/M | Notes |
|---|---|---|---|---|
| Claude Opus 5.5 (default) | `claude-opus-5-5` | 4.00 | 20.00 | |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2.00 | 10.00 | Faster and cheaper |
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0.10 | 0.50 | Cheapest, but in our test it was not faster here (24 s) and missed items. |

`fast_mode` only applies to Opus 5.5. For other models it's skipped automatically, as is the backup-model fallback for Haiku.

**Switching to a different provider** (for example OpenAI) needs a few lines of new code in `ai.py`, in the clearly marked "THE AI CALL" section, plus that provider's key in `.env`.

---

## The log: what the AI was sent and what it said

Every AI request writes lines to **`logs/ai_log.jsonl`**, one event per line. All events from one request share the same `request_id`. You can read the file in Notepad, but the easier view is **http://localhost:8000/log**: newest first, click a row to expand it.

| `event` | What it records |
|---|---|
| `ai_request` | mode, provider, model, effort, the input values (car, mileage, driving, radius), the **service history** sent, the manual's file name, size and fingerprint, the full **instructions** (system prompt), and the exact **user message** sent |
| `ai_response` | the model's full answer (`output`, and `raw_output_text` exactly as returned), `overall_confidence`, `confidence_by_item`, `confidence_by_shop`, which model actually answered (`model_served`), the stop reason, token usage, **cost estimate**, **response time** |
| `ai_error` | what went wrong: error type, message, status code |
| `feedback` | each Accept / Reject click on 04b (with the AI's status and confidence for that item), and each Approve / Decline click on screen 07 (07 is still simulated, so it's marked `quote_check (simulated)`) |
| `finalize` | a saved visit: car, mileage, date, what was done, declined or left undecided, and every decision with the AI's status and confidence |
| `history_reset` | the Reset link was used to clear a car's service history |

Notes:
- **Confidence is the model's own estimate** (0–1) of how sure it is. It isn't a measured probability. Comparing it with your Accept/Reject clicks over time is how you'd test whether it's calibrated (Assignment #3's calibration target).
- The manual itself isn't copied into the log because it's too big. The file name, size and a fingerprint (`sha256`) show which file was used.
- This step makes **one** model call. It isn't an agent and uses no tools, so there are no intermediate steps to log.
- Accept/Reject clicks now mean "done / not done at this visit", which is what Finalize saves. They're still logged with the AI's status and confidence, so you can compare what the AI said was due with what the user actually had done.
- To start a fresh log, delete `logs/ai_log.jsonl`. A new one is created automatically.

---

## Manuals: tips

- PDFs up to **32 MB and 600 pages** work. Most full manuals fit, but bigger means slower and costlier, roughly $0.50–$1.50 per check for a 500-page manual on Opus. **The cheapest and fastest option is to upload only the maintenance-schedule pages.** Use "Print → Microsoft Print to PDF" with a page range.
- Checking the same manual again within about 5 minutes is cheaper because the provider caches it.
- Put manuals you'll reuse in the `manuals/` folder, with the make in the file name. Files can be PDF or `.txt`. `.txt` works well for web-page manuals: `2025-Kia-Telluride-scheduled-maintenance.txt` was built from Kia's online manual page.
- If the manual doesn't match the car, the AI is told to say so, lower its confidence, and show a warning.

## Troubleshooting

| You see | Fix |
|---|---|
| "No API key yet" | Put your key in `backend/.env`, save, and click the button again. No restart needed. |
| "The API key was rejected" | The key was mistyped or revoked. Make a new one in the Console. |
| "Too many requests, or the account is out of credit" | Add credit in the Console's Billing page, or wait a minute. |
| "Model not found" | Check the spelling of `"model"` in `config.json`. |
| "config.json has a typo" | A missing comma or quote. Compare with the example above. |
| "Server not running" on 04b | Start `start.bat`, and open the page via http://localhost:8000. |
| `setup.bat` says Python not found | Redo step 1, then **close and reopen** any windows. |
| Port 8000 already in use | Change `"port"` in `config.json` (e.g. 8001) and open that address. |

## Keeping keys safe

- `.env` holds **your** key. It's listed in `.gitignore`, so GitHub won't receive it if you use git. **Shared folders like OneDrive, Google Drive or Teams copy it anyway**, so don't keep your `.env` inside a folder you share with others.
- If a key is ever exposed, delete it in the Console (API Keys) and make a new one.
- The server only listens on your own laptop (`127.0.0.1`), so other people on the Wi-Fi can't use your key through it.

## What's in this folder

```
backend/
  app.py            the local web server (serves the prototype, /log page, API routes)
  ai.py             THE AI CALL: run_maintenance_check() - instructions, input, output format
  simulated.py      canned answer used when "mode" is "simulated"
  ai_log.py         writes logs/ai_log.jsonl
  config.json       provider, model, effort, live/simulated switch
  .env.example      template - copy to .env and add your key
  .env              YOUR key (never share)
  data/shops.json   sample nearby shops (from the mockup)
  data/service_history.json   saved visits (created by "Finalize maintenance"; delete it or use Reset to start over)
  service_history.py          reads/writes that file and formats it for the AI
  manuals/          owner's manuals; includes a made-up SAMPLE schedule for testing
  frontend/         copy of the ShopSense mockup, wired to the backend (original mockup untouched)
  logs/             created on first run
  setup.bat / start.bat   double-click helpers
```
