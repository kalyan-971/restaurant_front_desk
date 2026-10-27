"""The reply agent.

Safety rules live in code, not only in the prompt:
- Groups bigger than the online limit always go to the owner.
- The model may only use the owner's fact sheet; if it can't answer it must say UNKNOWN,
  and then the owner is alerted instead.
- Any allergy-related message gets a "please confirm with staff" line.
- The agent never confirms a booking. Bookings only happen through the booking page,
  where code checks capacity.
"""
import re
from dataclasses import dataclass

ALLERGY_WORDS = re.compile(
    r"\b(allerg\w*|gluten|coeliac|celiac|nut|nuts|peanut\w*|dairy|lactose|vegan|"
    r"shellfish|seafood|egg|eggs|soy|sesame|halal|intoleran\w*)\b", re.I)
BOOKING_WORDS = re.compile(r"\b(book|booking|reserve|reservation|table|tonight|seat)\b", re.I)
GROUP_PATTERNS = [
    re.compile(r"\b(?:table|booking|reservation)\s+for\s+(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:people|ppl|pax|guests|persons|adults|of us)\b", re.I),
    re.compile(r"\bgroup of\s+(\d{1,3})\b", re.I),
]
ALLERGY_NOTE = "Please confirm any allergies with our staff when you arrive."
MAX_SMS_CHARS = 320


@dataclass
class AgentReply:
    text: str
    alert_owner: bool = False
    reason: str = ""


def party_size_in(text: str) -> int | None:
    for p in GROUP_PATTERNS:
        m = p.search(text)
        if m:
            return int(m.group(1))
    return None


def booking_link(base_url: str, restaurant: dict) -> str:
    return f"{base_url}/book/{restaurant['id']}"


SYSTEM_PROMPT = """You reply to text messages for {name}, a restaurant in Sydney.
Rules:
- Use ONLY the facts below. If the facts don't answer the question, reply with exactly: UNKNOWN
- Never confirm, change or promise a booking or a time. For bookings, tell them to use the booking link.
- Never say a dish is safe for an allergy unless the facts say so.
- Friendly Australian English, under 300 characters, no emojis, no markdown.

Facts from the owner:
{facts}"""


def _ask_model(api_key: str, model: str, restaurant: dict, history: list[dict], incoming: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    turns = []
    for m in history[-6:]:
        role = "user" if m["direction"] == "in" else "assistant"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + m["body"]
        else:
            turns.append({"role": role, "content": m["body"]})
    if not turns or turns[-1]["role"] != "user":
        turns.append({"role": "user", "content": incoming})
    if turns[0]["role"] != "user":
        turns = turns[1:]
    resp = client.messages.create(
        model=model, max_tokens=300,
        system=SYSTEM_PROMPT.format(name=restaurant["name"], facts=restaurant.get("fact_sheet") or "(none)"),
        messages=turns,
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def reply(restaurant: dict, incoming: str, history: list[dict], base_url: str,
          api_key: str = "", model: str = "", ask=None) -> AgentReply:
    """Decide what to text back. `ask` can be injected in tests instead of the real model."""
    link = booking_link(base_url, restaurant)
    size = party_size_in(incoming)
    limit = int(restaurant.get("max_online_party", 7))

    if size is not None and size > limit:
        return AgentReply(
            f"Thanks! For a group of {size} our team will text you back shortly to sort it out.",
            alert_owner=True, reason=f"group of {size}")

    ask = ask or (lambda: _ask_model(api_key, model, restaurant, history, incoming) if api_key else "")
    try:
        answer = ask()
    except Exception:  # the model is down or misconfigured: fail safe, hand to the owner
        answer = ""

    if not answer or answer.strip().upper().startswith("UNKNOWN"):
        text = "Thanks for your message! Our team will reply shortly."
        if BOOKING_WORDS.search(incoming):
            text += f" You can book a table here: {link}"
        result = AgentReply(text, alert_owner=True, reason="not in fact sheet")
    else:
        text = answer
        if BOOKING_WORDS.search(incoming) and link not in text:
            text += f" Book here: {link}"
        result = AgentReply(text)

    needs_note = ALLERGY_WORDS.search(incoming) or ALLERGY_WORDS.search(result.text)
    if needs_note and ALLERGY_NOTE not in result.text:
        # Trim the answer first so the safety note is never cut off.
        room = MAX_SMS_CHARS - len(ALLERGY_NOTE) - 1
        result.text = f"{result.text[:room].rstrip()} {ALLERGY_NOTE}"
    else:
        result.text = result.text[:MAX_SMS_CHARS]
    return result
