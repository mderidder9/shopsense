# ShopSense

**An independent AI second opinion at the auto-shop counter.** ShopSense checks what maintenance a car actually needs at its mileage, using the manufacturer's schedule, and recommends trustworthy nearby shops.

This is a class prototype for *43120 – Building an AI Company*.

## What's here

| Path | What it is |
|---|---|
| [`backend/`](backend/) | **The runnable prototype.** It's a click-through phone app served by a small local Python server. One AI step is live, using Claude Opus 5.5: what maintenance is due, plus shop recommendations. The other steps are simulated. **Setup instructions: [`backend/README.md`](backend/README.md).** |
| `ShopSense UI mockups/` | The original design mockups (open `ShopSense Mockups.dc.html` in a browser) |
| `ShopSense_Mockups.pptx` | Mockup slides |
| `#1`–`#3 F26 BAIC Assignment*.docx` | Course assignments: opportunity and product strategy, competitive advantage, technical solution flow and feasibility |

## Quick start

1. Install Python 3.12 and get your own Anthropic API key. Step-by-step instructions are in [`backend/README.md`](backend/README.md).
2. In `backend/`, double-click `setup.bat` and paste your key when Notepad opens.
3. Double-click `start.bat`. The app opens at http://localhost:8000.

Your API key goes in `backend/.env`, which is excluded from this repo. **Never commit it.**

## Notes

- The shop list is **sample data** from the mockups, not real businesses.
- The demo vehicle is a 2025 Kia Telluride. Its maintenance schedule (`backend/manuals/`) was transcribed from Kia's public online owner's manual.
- AI confidence scores are the model's own estimates, not measured probabilities.
