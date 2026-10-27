import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Australia/Sydney")


@dataclass
class Settings:
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_service_key: str = field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_KEY", ""))
    twilio_sid: str = field(default_factory=lambda: os.getenv("TWILIO_SID", ""))
    twilio_token: str = field(default_factory=lambda: os.getenv("TWILIO_TOKEN", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "claude-haiku-5-5"))
    staff_password: str = field(default_factory=lambda: os.getenv("STAFF_PASSWORD", ""))
    # Set to "false" only for local testing without Twilio.
    validate_twilio: bool = field(default_factory=lambda: os.getenv("VALIDATE_TWILIO", "true").lower() != "false")
