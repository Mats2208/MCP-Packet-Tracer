"""EtherChannel (port-channel) model for a switch."""

from __future__ import annotations
from pydantic import BaseModel, Field

# channel-group modes: LACP (active/passive), PAgP (desirable/auto) and static (on).
LACP_MODES = ("active", "passive")
PAGP_MODES = ("desirable", "auto")
ETHERCHANNEL_MODES = (*LACP_MODES, *PAGP_MODES, "on")


class EtherChannelConfig(BaseModel):
    """Bundle `ports` of `switch` into Port-channel<group>."""
    switch: str
    ports: list[str] = Field(default_factory=list)
    group: int = 1
    mode: str = "active"
    trunk: bool = False
    allowed_vlans: list[int] | None = None

    def protocol(self) -> str:
        if self.mode in LACP_MODES:
            return "LACP"
        if self.mode in PAGP_MODES:
            return "PAgP"
        return "static"
