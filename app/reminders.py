"""Day-before reminders. Run every 15 minutes as a Render cron job:

    python -m app.reminders

Texts every confirmed booking starting 20-28 hours from now that hasn't been reminded yet.
"""
from datetime import datetime, timedelta, timezone

from .config import TZ, Settings


def run(repo, sender, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    due = repo.unreminded_between(now + timedelta(hours=20), now + timedelta(hours=28))
    count = 0
    for b in due:
        r = b["restaurants"]
        start = b["starts_at"]
        start = start if isinstance(start, datetime) else datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        local = start.astimezone(TZ)
        when = local.strftime("%a ") + local.strftime("%I:%M%p").lstrip("0").lower()
        body = (f"Reminder: table for {b['party_size']} at {r['name']} tomorrow ({when}). "
                f"See you then! Reply C to cancel.")
        try:
            sender.send(r["twilio_number"], b["phone"], body)
        except Exception as exc:  # one bad number shouldn't stop the rest
            print(f"reminder failed for booking {b['id']}: {exc}")
            continue
        repo.update_booking(b["id"], {"reminded": True})
        repo.log_message(b["restaurant_id"], b["phone"], "out", body)
        count += 1
    return count


if __name__ == "__main__":
    from .db import SupabaseRepo
    from .sms import TwilioSender
    s = Settings()
    sent = run(SupabaseRepo(s.supabase_url, s.supabase_service_key), TwilioSender(s.twilio_sid, s.twilio_token))
    print(f"sent {sent} reminders")
