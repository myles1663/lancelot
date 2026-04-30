"""
Client Models — Pydantic models and enums for BAL client management.

Defines the core domain models for clients, their preferences, billing,
content history, and API input/output shapes.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ClientStatus(str, Enum):
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    PAUSED = "paused"
    CHURNED = "churned"


class PlanTier(str, Enum):
    STARTER = "starter"
    GROWTH = "growth"
    SCALE = "scale"


class PaymentStatus(str, Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class TonePreference(str, Enum):
    CASUAL = "casual"
    PROFESSIONAL = "professional"
    TECHNICAL = "technical"
    WITTY = "witty"


class HashtagPolicy(str, Enum):
    ALWAYS = "always"
    NEVER = "never"
    CONTEXTUAL = "contextual"


class EmojiPolicy(str, Enum):
    LIBERAL = "liberal"
    CONSERVATIVE = "conservative"
    NONE = "none"


# ---------------------------------------------------------------------------
# Nested Models
# ---------------------------------------------------------------------------

class ClientBilling(BaseModel):
    stripe_customer_id: Optional[str] = None
    subscription_id: Optional[str] = None
    current_period_end: Optional[datetime] = None
    payment_status: PaymentStatus = PaymentStatus.ACTIVE


class ClientPreferences(BaseModel):
    tone: TonePreference = TonePreference.PROFESSIONAL
    platforms: List[str] = Field(default_factory=lambda: ["twitter", "linkedin"])
    hashtag_policy: HashtagPolicy = HashtagPolicy.CONTEXTUAL
    emoji_policy: EmojiPolicy = EmojiPolicy.CONSERVATIVE
    brand_voice_notes: str = ""
    excluded_topics: List[str] = Field(default_factory=list)
    posting_schedule: Dict = Field(default_factory=dict)


class ContentHistory(BaseModel):
    total_pieces_delivered: int = 0
    last_delivery_at: Optional[datetime] = None
    average_satisfaction: float = 0.0


# ---------------------------------------------------------------------------
# Core Domain Model
# ---------------------------------------------------------------------------

class Client(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    email: str
    status: ClientStatus = ClientStatus.ONBOARDING
    plan_tier: PlanTier = PlanTier.STARTER
    billing: ClientBilling = Field(default_factory=ClientBilling)
    preferences: ClientPreferences = Field(default_factory=ClientPreferences)
    content_history: ContentHistory = Field(default_factory=ContentHistory)
    memory_block_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError(f"Invalid email format: {v}")
        return v.lower()


# ---------------------------------------------------------------------------
# API Input Models
# ---------------------------------------------------------------------------

class ClientCreate(BaseModel):
    name: str
    email: str
    plan_tier: PlanTier = PlanTier.STARTER
    preferences: Optional[ClientPreferences] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError(f"Invalid email format: {v}")
        return v.lower()


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    preferences: Optional[ClientPreferences] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError(f"Invalid email format: {v}")
        return v.lower()
