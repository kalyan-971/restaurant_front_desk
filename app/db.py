"""Data access. SupabaseRepo talks to the real database; MemoryRepo is for tests and local demos."""
import uuid
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt) -> str:
    return dt.isoformat() if isinstance(dt, datetime) else str(dt)


class SupabaseRepo:
    def __init__(self, url: str, key: str):
        from supabase import create_client  # imported here so tests don't need it
        self.db = create_client(url, key)

    def _one(self, query):
        rows = query.limit(1).execute().data
        return rows[0] if rows else None

    def get_restaurant(self, restaurant_id: str):
        return self._one(self.db.table("restaurants").select("*").eq("id", restaurant_id))

    def get_restaurant_by_number(self, number: str):
        return self._one(self.db.table("restaurants").select("*").eq("twilio_number", number))

    def update_restaurant(self, restaurant_id: str, fields: dict):
        self.db.table("restaurants").update(fields).eq("id", restaurant_id).execute()

    def bookings_between(self, restaurant_id: str, start, end):
        return (self.db.table("bookings").select("*")
                .eq("restaurant_id", restaurant_id)
                .gte("starts_at", _iso(start)).lt("starts_at", _iso(end))
                .order("starts_at").execute().data)

    def create_booking(self, data: dict):
        data = {**data, "starts_at": _iso(data["starts_at"])}
        return self.db.table("bookings").insert(data).execute().data[0]

    def update_booking(self, booking_id: str, fields: dict):
        self.db.table("bookings").update(fields).eq("id", booking_id).execute()

    def next_booking_for_phone(self, restaurant_id: str, phone: str, after):
        return self._one(self.db.table("bookings").select("*")
                         .eq("restaurant_id", restaurant_id).eq("phone", phone)
                         .eq("status", "confirmed").gte("starts_at", _iso(after))
                         .order("starts_at"))

    def unreminded_between(self, start, end):
        return (self.db.table("bookings").select("*, restaurants(name, twilio_number)")
                .eq("status", "confirmed").eq("reminded", False)
                .gte("starts_at", _iso(start)).lt("starts_at", _iso(end))
                .execute().data)

    def log_message(self, restaurant_id: str, phone: str, direction: str, body: str):
        return self.db.table("messages").insert(
            {"restaurant_id": restaurant_id, "phone": phone, "direction": direction, "body": body}
        ).execute().data[0]

    def get_message(self, message_id: str):
        return self._one(self.db.table("messages").select("*").eq("id", message_id))

    def update_message(self, message_id: str, fields: dict):
        self.db.table("messages").update(fields).eq("id", message_id).execute()

    def recent_messages(self, restaurant_id: str, phone: str | None = None, limit: int = 20):
        q = self.db.table("messages").select("*").eq("restaurant_id", restaurant_id)
        if phone:
            q = q.eq("phone", phone)
        return q.order("created_at", desc=True).limit(limit).execute().data


class MemoryRepo:
    """Same interface as SupabaseRepo, kept in memory."""

    def __init__(self):
        self.restaurants: dict[str, dict] = {}
        self.bookings: dict[str, dict] = {}
        self.messages: list[dict] = []

    def add_restaurant(self, **fields) -> dict:
        r = {
            "id": str(uuid.uuid4()), "fact_sheet": "", "max_covers_per_slot": 40,
            "slot_minutes": 30, "open_time": "17:00", "last_booking_time": "21:00",
            "closed_days": [], "max_online_party": 7, "shadow_mode": False, **fields,
        }
        self.restaurants[r["id"]] = r
        return r

    def get_restaurant(self, restaurant_id):
        return self.restaurants.get(restaurant_id)

    def get_restaurant_by_number(self, number):
        return next((r for r in self.restaurants.values() if r["twilio_number"] == number), None)

    def update_restaurant(self, restaurant_id, fields):
        self.restaurants[restaurant_id].update(fields)

    def bookings_between(self, restaurant_id, start, end):
        rows = [b for b in self.bookings.values()
                if b["restaurant_id"] == restaurant_id and start <= b["starts_at"] < end]
        return sorted(rows, key=lambda b: b["starts_at"])

    def create_booking(self, data):
        b = {"id": str(uuid.uuid4()), "status": "confirmed", "reminded": False,
             "created_at": _now_iso(), **data}
        self.bookings[b["id"]] = b
        return b

    def update_booking(self, booking_id, fields):
        self.bookings[booking_id].update(fields)

    def next_booking_for_phone(self, restaurant_id, phone, after):
        rows = [b for b in self.bookings.values()
                if b["restaurant_id"] == restaurant_id and b["phone"] == phone
                and b["status"] == "confirmed" and b["starts_at"] >= after]
        return min(rows, key=lambda b: b["starts_at"]) if rows else None

    def unreminded_between(self, start, end):
        out = []
        for b in self.bookings.values():
            if b["status"] == "confirmed" and not b["reminded"] and start <= b["starts_at"] < end:
                r = self.restaurants[b["restaurant_id"]]
                out.append({**b, "restaurants": {"name": r["name"], "twilio_number": r["twilio_number"]}})
        return out

    def log_message(self, restaurant_id, phone, direction, body):
        m = {"id": str(uuid.uuid4()), "restaurant_id": restaurant_id, "phone": phone,
             "direction": direction, "body": body, "created_at": _now_iso()}
        self.messages.append(m)
        return m

    def get_message(self, message_id):
        return next((m for m in self.messages if m["id"] == message_id), None)

    def update_message(self, message_id, fields):
        self.get_message(message_id).update(fields)

    def recent_messages(self, restaurant_id, phone=None, limit=20):
        rows = [m for m in self.messages if m["restaurant_id"] == restaurant_id
                and (phone is None or m["phone"] == phone)]
        return list(reversed(rows))[:limit]
