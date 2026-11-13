"""What can be changed from the panel, and the record of what was changed (ADR 0025).

Every action works on this node's own data: the outbox and the team's suggestions. None
of them can reach the workspace: applying a fix stays in the terminal.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

ACTIONS_KEPT = 100
"""How many past actions are kept for the panel's recent activity."""


class ActionKind(StrEnum):
    SEND = "send"
    DISCARD = "discard"
    FORGET = "forget"


class PanelAction(BaseModel):
    """One change made from the panel, as the recent activity shows it."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: ActionKind
    title: str = Field(max_length=200)
    detail: str = Field(max_length=500)


class ActionOutcome(BaseModel):
    """What the panel says after an action: whether it did what was asked, and why not."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    message: str
