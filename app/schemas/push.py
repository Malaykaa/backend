from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    """Miroir exact de l'objet `PushSubscription` du navigateur (`toJSON()`)."""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(max_length=2048)
    keys: PushSubscriptionKeys


class PushUnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(max_length=2048)
