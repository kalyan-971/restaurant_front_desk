"""Restaurant Front Desk -- FastAPI app.

Routes
  POST /voice                      Twilio: a diverted (missed) call arrives -> text the caller back
  POST /sms                        Twilio: a diner texts in -> agent replies (or drafts, in shadow mode)
  GET  /book/{restaurant_id}       public booking page
  POST /book/{restaurant_id}       create a booking (capacity checked in code)
  GET  /staff/{restaurant_id}      staff screen (password protected)
  POST /staff/{restaurant_id}/...  approve drafts, cancel bookings, edit settings
  GET  /health                     for Render health checks
"""
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import agent
from .booking import check_availability, normalize_au_mobile, time_options
from .config import TZ, Settings

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ANONYMOUS = {"", "anonymous", "restricted", "unknown", "+266696687", "+86282452253", "+7378742833"}
CANCEL_WORDS = {"C", "CANCEL"}
security = HTTPBasic()


def twiml(say: str | None = None) -> Response:
    inner = f"<Say>{escape(say)}</Say><Hangup/>" if say else ""
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>',
                    media_type="application/xml")


def create_app(repo=None, sender=None, settings: Settings | None = None, now=None, ask=None) -> FastAPI:
    """Build the app. Tests pass fakes; production builds real clients from env vars."""
    settings = settings or Settings()
    if repo is None:
        from .db import SupabaseRepo
        repo = SupabaseRepo(settings.supabase_url, settings.supabase_service_key)
    if sender is None:
        from .sms import TwilioSender
        sender = TwilioSender(settings.twilio_sid, settings.twilio_token)
    clock = now or (lambda: datetime.now(timezone.utc))

    app = FastAPI(title="Restaurant Front Desk")

    async def verify_twilio(request: Request) -> dict:
        form = dict(await request.form())
        if settings.validate_twilio:
            from twilio.request_validator import RequestValidator
            url = settings.public_base_url + request.url.path
            sig = request.headers.get("X-Twilio-Signature", "")
            if not RequestValidator(settings.twilio_token).validate(url, form, sig):
                raise HTTPException(403, "Invalid Twilio signature")
        return form

    def staff_auth(creds: HTTPBasicCredentials = Depends(security)):
        ok = settings.staff_password and secrets.compare_digest(
            creds.password.encode(), settings.staff_password.encode())
        if not ok:
            raise HTTPException(401, "Wrong password", headers={"WWW-Authenticate": "Basic"})

    def send_or_draft(r: dict, to: str, body: str):
        if r.get("shadow_mode"):
            repo.log_message(r["id"], to, "draft", body)
        else:
            sender.send(r["twilio_number"], to, body)
            repo.log_message(r["id"], to, "out", body)

    def alert_owner(r: dict, text: str):
        sender.send(r["twilio_number"], r["owner_phone"], f"[Front desk] {text}"[:320])
        repo.log_message(r["id"], r["owner_phone"], "alert", text)

    @app.get("/health")
    def health():
        return {"ok": True}

    # ---------- Missed calls ----------
    @app.post("/voice")
    async def voice(form: dict = Depends(verify_twilio)):
        caller, to = form.get("From", ""), form.get("To", "")
        r = repo.get_restaurant_by_number(to)
        if not r:
            return twiml("Sorry, this number isn't set up yet.")
        if caller.lower() not in ANONYMOUS:
            body = (f"Sorry we missed your call at {r['name']}! "
                    f"Book a table here: {agent.booking_link(settings.public_base_url, r)} "
                    f"or reply to this text with your question.")
            # Missed-call texts always go out, even in shadow mode: they are a fixed template.
            sender.send(r["twilio_number"], caller, body)
            repo.log_message(r["id"], caller, "out", body)
            return twiml(f"Thanks for calling {r['name']}. We're busy right now, "
                         "so we've just sent you a text with a booking link.")
        return twiml(f"Thanks for calling {r['name']}. We're busy right now. "
                     "Please book online or call back a little later.")

    # ---------- Incoming texts ----------
    @app.post("/sms")
    async def sms(form: dict = Depends(verify_twilio)):
        phone, to, body = form.get("From", ""), form.get("To", ""), (form.get("Body") or "").strip()
        r = repo.get_restaurant_by_number(to)
        if not r or not body:
            return twiml()
        repo.log_message(r["id"], phone, "in", body)

        if body.upper() in CANCEL_WORDS:
            b = repo.next_booking_for_phone(r["id"], phone, clock())
            if b:
                repo.update_booking(b["id"], {"status": "cancelled"})
                when = _fmt(b["starts_at"])
                sender.send(r["twilio_number"], phone, f"Your booking at {r['name']} for {when} is cancelled. Thanks for letting us know!")
                repo.log_message(r["id"], phone, "out", f"cancelled booking {when}")
            else:
                sender.send(r["twilio_number"], phone, f"We couldn't find an upcoming booking for this number at {r['name']}.")
            return twiml()

        history = list(reversed(repo.recent_messages(r["id"], phone, limit=8)))
        result = agent.reply(r, body, history, settings.public_base_url,
                             settings.anthropic_api_key, settings.ai_model, ask=ask)
        send_or_draft(r, phone, result.text)
        if result.alert_owner:
            alert_owner(r, f"{phone} ({result.reason}): {body}")
        return twiml()

    # ---------- Booking page ----------
    @app.get("/book/{rid}", response_class=HTMLResponse)
    def book_page(request: Request, rid: str):
        r = repo.get_restaurant(rid) or _404()
        today = clock().astimezone(TZ).date()
        return TEMPLATES.TemplateResponse(request, "book.html", {
            "r": r, "times": time_options(r), "today": today.isoformat(),
            "max_date": (today + timedelta(days=60)).isoformat(), "error": None, "done": None, "form": {}})

    @app.post("/book/{rid}", response_class=HTMLResponse)
    def book_submit(request: Request, rid: str, day: str = Form(...), at: str = Form(...),
                    party_size: int = Form(...), name: str = Form(...), phone: str = Form(...)):
        r = repo.get_restaurant(rid) or _404()
        form = {"day": day, "at": at, "party_size": party_size, "name": name, "phone": phone}
        today = clock().astimezone(TZ).date()
        ctx = {"r": r, "times": time_options(r), "today": today.isoformat(),
               "max_date": (today + timedelta(days=60)).isoformat(), "form": form, "done": None}

        mobile = normalize_au_mobile(phone)
        try:
            d = date.fromisoformat(day)
            hh, mm = (int(x) for x in at.split(":"))
            starts = datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ)
        except ValueError:
            return TEMPLATES.TemplateResponse(request, "book.html", {**ctx, "error": "Please pick a valid date and time."}, status_code=400)
        if not mobile:
            return TEMPLATES.TemplateResponse(request, "book.html", {**ctx, "error": "Please enter an Australian mobile number (04...)."}, status_code=400)
        if not name.strip():
            return TEMPLATES.TemplateResponse(request, "book.html", {**ctx, "error": "Please enter your name."}, status_code=400)

        day_start = datetime(d.year, d.month, d.day, tzinfo=TZ)
        existing = repo.bookings_between(rid, day_start, day_start + timedelta(days=1))
        check = check_availability(r, starts, party_size, existing, clock())
        if not check.ok:
            return TEMPLATES.TemplateResponse(request, "book.html", {**ctx, "error": check.reason}, status_code=409)

        repo.create_booking({"restaurant_id": rid, "name": name.strip()[:80], "phone": mobile,
                             "party_size": party_size, "starts_at": starts})
        msg = (f"Booked! {name.strip()}, table for {party_size} at {r['name']} on {_fmt(starts)}. "
               f"We'll remind you the day before. Reply C to cancel.")
        sender.send(r["twilio_number"], mobile, msg)
        repo.log_message(rid, mobile, "out", msg)
        return TEMPLATES.TemplateResponse(request, "book.html", {**ctx, "error": None, "done": _fmt(starts)})

    # ---------- Staff screen ----------
    @app.get("/staff/{rid}", response_class=HTMLResponse, dependencies=[Depends(staff_auth)])
    def staff(request: Request, rid: str):
        r = repo.get_restaurant(rid) or _404()
        local_today = clock().astimezone(TZ).date()
        start = datetime(local_today.year, local_today.month, local_today.day, tzinfo=TZ)
        bookings = repo.bookings_between(rid, start, start + timedelta(days=2))
        msgs = repo.recent_messages(rid, limit=40)
        for b in bookings:
            b["when"] = _fmt(b["starts_at"])
        return TEMPLATES.TemplateResponse(request, "staff.html", {
            "r": r, "bookings": bookings, "messages": msgs,
            "drafts": [m for m in msgs if m["direction"] == "draft"],
            "covers_today": sum(b["party_size"] for b in bookings
                                if b["status"] == "confirmed" and _local(b["starts_at"]).date() == local_today)})

    @app.post("/staff/{rid}/drafts/{mid}", dependencies=[Depends(staff_auth)])
    def approve_draft(rid: str, mid: str, body: str = Form(...), action: str = Form("send")):
        r = repo.get_restaurant(rid) or _404()
        m = repo.get_message(mid)
        if not m or m["restaurant_id"] != rid or m["direction"] != "draft":
            _404()
        if action == "send":
            sender.send(r["twilio_number"], m["phone"], body[:320])
            repo.update_message(mid, {"direction": "out", "body": body[:320]})
        else:
            repo.update_message(mid, {"direction": "alert", "body": f"(discarded) {m['body']}"})
        return RedirectResponse(f"/staff/{rid}", status_code=303)

    @app.post("/staff/{rid}/bookings/{bid}/cancel", dependencies=[Depends(staff_auth)])
    def staff_cancel(rid: str, bid: str):
        repo.update_booking(bid, {"status": "cancelled"})
        return RedirectResponse(f"/staff/{rid}", status_code=303)

    @app.post("/staff/{rid}/settings", dependencies=[Depends(staff_auth)])
    def staff_settings(rid: str, fact_sheet: str = Form(""), max_covers_per_slot: int = Form(...),
                       shadow_mode: str = Form("off")):
        repo.update_restaurant(rid, {"fact_sheet": fact_sheet[:4000],
                                     "max_covers_per_slot": max(1, max_covers_per_slot),
                                     "shadow_mode": shadow_mode == "on"})
        return RedirectResponse(f"/staff/{rid}", status_code=303)

    return app


def _404():
    raise HTTPException(404, "Not found")


def _local(value) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.astimezone(TZ)


def _fmt(value) -> str:
    dt = _local(value)
    return dt.strftime("%a %d %b, ") + dt.strftime("%I:%M%p").lstrip("0").lower()


def production_app():
    """Entry point for uvicorn: `uvicorn app.main:production_app --factory`.
    Real clients are only built here, so tests can import this module without keys."""
    return create_app()
