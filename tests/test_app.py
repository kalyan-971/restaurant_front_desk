from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import agent, reminders
from app.booking import check_availability, normalize_au_mobile
from app.config import TZ, Settings
from app.db import MemoryRepo
from app.main import create_app
from app.sms import FakeSender

TWILIO_NO = "+61400000000"
OWNER = "+61411111111"
DINER = "+61422222222"
# A fixed "now": Wednesday 28 Oct 2026, 10am Sydney time.
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=TZ)


@pytest.fixture
def world():
    repo, sender = MemoryRepo(), FakeSender()
    r = repo.add_restaurant(name="Test Kitchen", twilio_number=TWILIO_NO, owner_phone=OWNER,
                            fact_sheet="Open Tue-Sun 5pm-10pm. BYO wine, $5 corkage.",
                            max_covers_per_slot=10, closed_days=[0])
    settings = Settings(public_base_url="https://fd.test", validate_twilio=False, staff_password="pw")
    answers = {"text": "We're open 5pm-10pm Tuesday to Sunday."}
    app = create_app(repo, sender, settings, now=lambda: NOW, ask=lambda: answers["text"])
    return {"repo": repo, "sender": sender, "r": r, "client": TestClient(app), "answers": answers}


# ---------- booking rules ----------

def test_capacity_is_enforced(world):
    r = world["r"]
    at = datetime(2026, 10, 29, 19, 0, tzinfo=TZ)
    existing = [{"party_size": 6, "starts_at": at, "status": "confirmed"},
                {"party_size": 4, "starts_at": at, "status": "cancelled"}]
    assert check_availability(r, at, 4, existing, NOW).ok           # 6 + 4 = 10
    assert not check_availability(r, at, 5, existing, NOW).ok       # 11 > 10


def test_closed_day_past_and_big_groups_refused(world):
    r = world["r"]
    monday = datetime(2026, 11, 2, 19, 0, tzinfo=TZ)
    assert "closed" in check_availability(r, monday, 2, [], NOW).reason
    assert not check_availability(r, NOW - timedelta(hours=1), 2, [], NOW).ok
    assert not check_availability(r, datetime(2026, 10, 29, 19, 0, tzinfo=TZ), 9, [], NOW).ok
    assert not check_availability(r, datetime(2026, 10, 29, 19, 10, tzinfo=TZ), 2, [], NOW).ok  # off-slot time


def test_phone_normalising():
    assert normalize_au_mobile("0412 345 678") == "+61412345678"
    assert normalize_au_mobile("+61 412-345-678") == "+61412345678"
    assert normalize_au_mobile("02 9999 1234") is None


# ---------- agent guardrails ----------

def test_big_group_goes_to_owner(world):
    res = agent.reply(world["r"], "Can we get a table for 12 on Saturday?", [], "https://fd.test", ask=lambda: "sure!")
    assert res.alert_owner and "team will text you back" in res.text


def test_unknown_answer_alerts_owner_and_sends_link(world):
    res = agent.reply(world["r"], "Can I book for 2 tonight with a dog?", [], "https://fd.test", ask=lambda: "UNKNOWN")
    assert res.alert_owner and "/book/" in res.text


def test_allergy_note_always_added(world):
    long = "Our curries are mild. " * 30
    res = agent.reply(world["r"], "Is the korma nut free?", [], "https://fd.test", ask=lambda: long)
    assert res.text.endswith(agent.ALLERGY_NOTE) and len(res.text) <= agent.MAX_SMS_CHARS


def test_model_failure_fails_safe(world):
    def boom():
        raise RuntimeError("api down")
    res = agent.reply(world["r"], "what time do you close?", [], "https://fd.test", ask=boom)
    assert res.alert_owner


# ---------- Twilio webhooks ----------

def test_missed_call_texts_caller(world):
    resp = world["client"].post("/voice", data={"From": DINER, "To": TWILIO_NO})
    assert resp.status_code == 200 and "<Say>" in resp.text
    sent = world["sender"].sent[-1]
    assert sent["to"] == DINER and f"/book/{world['r']['id']}" in sent["body"]


def test_withheld_number_gets_no_text(world):
    world["client"].post("/voice", data={"From": "anonymous", "To": TWILIO_NO})
    assert world["sender"].sent == []


def test_sms_reply_and_shadow_mode(world):
    c, s, repo, r = world["client"], world["sender"], world["repo"], world["r"]
    c.post("/sms", data={"From": DINER, "To": TWILIO_NO, "Body": "What are your hours?"})
    assert s.sent[-1]["to"] == DINER and "5pm" in s.sent[-1]["body"]

    repo.update_restaurant(r["id"], {"shadow_mode": True})
    before = len(s.sent)
    c.post("/sms", data={"From": DINER, "To": TWILIO_NO, "Body": "Do you have parking?"})
    assert len(s.sent) == before                              # nothing sent yet
    draft = next(m for m in repo.messages if m["direction"] == "draft")
    resp = c.post(f"/staff/{r['id']}/drafts/{draft['id']}", data={"body": "Street parking on Smith St.", "action": "send"},
                  auth=("staff", "pw"), follow_redirects=False)
    assert resp.status_code == 303 and s.sent[-1]["body"] == "Street parking on Smith St."


# ---------- booking page + cancel + reminders ----------

def test_full_booking_flow(world):
    c, s, repo, r = world["client"], world["sender"], world["repo"], world["r"]
    assert c.get(f"/book/{r['id']}").status_code == 200

    form = {"day": "2026-10-29", "at": "19:00", "party_size": "4", "name": "Priya", "phone": "0422 222 222"}
    resp = c.post(f"/book/{r['id']}", data=form)
    assert resp.status_code == 200 and "booked" in resp.text.lower()
    assert s.sent[-1]["to"] == DINER and "Reply C to cancel" in s.sent[-1]["body"]

    # Fill the slot: 4 + 6 = 10, then one more is refused.
    c.post(f"/book/{r['id']}", data={**form, "party_size": "6", "phone": "0433 333 333"})
    full = c.post(f"/book/{r['id']}", data={**form, "party_size": "1", "phone": "0444 444 444"})
    assert full.status_code == 409 and "fully booked" in full.text

    # Reminder the day before (Thursday 7pm booking, run on Wednesday 7pm).
    sent = reminders.run(repo, s, now=datetime(2026, 10, 28, 19, 0, tzinfo=TZ))
    assert sent == 2 and reminders.run(repo, s, now=datetime(2026, 10, 28, 19, 15, tzinfo=TZ)) == 0

    # Diner replies C -> booking cancelled.
    c.post("/sms", data={"From": DINER, "To": TWILIO_NO, "Body": "c"})
    mine = next(b for b in repo.bookings.values() if b["phone"] == DINER)
    assert mine["status"] == "cancelled" and "cancelled" in s.sent[-1]["body"]


def test_staff_screen_needs_password(world):
    rid = world["r"]["id"]
    assert world["client"].get(f"/staff/{rid}").status_code == 401
    assert world["client"].get(f"/staff/{rid}", auth=("staff", "wrong")).status_code == 401
    assert world["client"].get(f"/staff/{rid}", auth=("staff", "pw")).status_code == 200


def test_twilio_signature_required_in_production():
    repo, sender = MemoryRepo(), FakeSender()
    repo.add_restaurant(name="X", twilio_number=TWILIO_NO, owner_phone=OWNER)
    app = create_app(repo, sender, Settings(public_base_url="https://fd.test", twilio_token="secret", validate_twilio=True))
    resp = TestClient(app).post("/voice", data={"From": DINER, "To": TWILIO_NO})
    assert resp.status_code == 403 and sender.sent == []
