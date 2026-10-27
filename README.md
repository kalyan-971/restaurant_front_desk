# Restaurant Front Desk

Text-first AI front desk for small restaurants. When a call is missed during service, the caller gets a text with a booking link in seconds. Diners can text questions (hours, menu, allergens, parking) and the AI answers only from the owner's fact sheet. Bookings are capacity-checked in code, confirmed by SMS, reminded the day before, and cancelled with a "C" reply.

## How it works

1. The restaurant sets **"divert on no answer"** with its phone provider to your Twilio number.
2. Twilio calls `POST /voice` → the app texts the caller a booking link and plays a short message.
3. Diner replies → `POST /sms` → the agent answers from the fact sheet, sends the booking link, or alerts the owner (big groups, unknown questions). In **shadow mode** replies wait for staff approval.
4. `GET /book/<restaurant_id>` → mobile booking page. Capacity, closed days and times are checked in code.
5. `python -m app.reminders` runs every 15 minutes and sends day-before reminders.
6. `GET /staff/<restaurant_id>` → staff screen: today's bookings, messages, drafts to approve, fact sheet.

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                                 # 13 tests, no keys needed
cp .env.example .env                                # then fill in your keys
uvicorn app.main:production_app --factory --reload
```

## Set up the services

1. **Supabase** — new project in the Sydney region → SQL editor → run `supabase/schema.sql`. Then insert your test restaurant (example at the bottom of the file). Copy the project URL and the service-role key into `.env`.
2. **Twilio** — buy an Australian mobile number. Under the number's settings set:
   - A call comes in → Webhook → `https://<your-app>.onrender.com/voice` (POST)
   - A message comes in → Webhook → `https://<your-app>.onrender.com/sms` (POST)
3. **Render** — New → Blueprint → pick this repo (uses `render.yaml`). Add the env vars from `.env.example`. Set `PUBLIC_BASE_URL` to your Render URL exactly, or Twilio signature checks will fail.
4. **Test** — call your Twilio number from your own phone. You should get a text with the booking link within seconds.

## Safety rules (in code, not just the prompt)

- The AI never confirms bookings; only the booking page can, after a capacity check.
- Answers come only from the fact sheet; anything else → "our team will reply" + owner alert.
- Any allergy-related message gets "Please confirm any allergies with our staff when you arrive."
- Groups above `max_online_party` (default 7) go to the owner.
- Every Twilio request must carry a valid signature; the staff screen needs `STAFF_PASSWORD`.
- If the AI API fails, the diner gets a safe holding reply and the owner is alerted.

## Project layout

```
app/
  main.py        routes (voice, sms, booking page, staff screen)
  agent.py       reply logic + guardrails
  booking.py     capacity rules, time slots, phone normalising
  db.py          Supabase repo + in-memory repo for tests
  sms.py         Twilio sender + fake sender
  reminders.py   day-before reminder job
  templates/     booking page and staff screen
supabase/schema.sql
tests/test_app.py
render.yaml
```
