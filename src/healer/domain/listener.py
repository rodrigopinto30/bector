"""What ``healer listen`` reports about itself, so the panel can show it."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HEARTBEAT_SECONDS = 30.0
"""How often a running listener confirms it is alive."""

STALE_AFTER = timedelta(seconds=3 * HEARTBEAT_SECONDS)
"""A listener silent for this long has stopped without saying so."""


RECENT_EVENTS = 20
"""How many of its latest events the listener keeps."""

EVENT_TEXT_CHARS = 300


class ListenerEvent(BaseModel):
    """Something that happened to the listener. ``text`` may come from the network, so it is
    shown as text, never as markup, and kept short."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: Literal["connection", "received", "ignored", "rejected"]
    text: str = Field(max_length=EVENT_TEXT_CHARS)


class ListenerStatus(BaseModel):
    """The last known state of the listener. Counts start at zero on every start."""

    model_config = ConfigDict(frozen=True)

    node: str
    topic: str
    connection: str = Field(description="The last connection line, as the listener prints it.")
    connected: bool
    started_at: datetime
    changed_at: datetime = Field(description="When the connection last changed.")
    updated_at: datetime = Field(description="The last sign of life.")
    received: int = Field(default=0, ge=0, description="New or newer suggestions kept.")
    ignored: int = Field(default=0, ge=0, description="Already known, older or sent by this node.")
    rejected: int = Field(default=0, ge=0, description="Forged, malformed or unsafe messages.")
    stopped: bool = False
    recent: list[ListenerEvent] = Field(
        default_factory=list, description=f"The last {RECENT_EVENTS} events, newest first."
    )
