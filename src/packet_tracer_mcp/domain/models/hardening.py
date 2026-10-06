"""Device hardening model."""

from __future__ import annotations
from pydantic import BaseModel, Field


class LocalUser(BaseModel):
    username: str
    secret: str
    privilege: int = 1


class SSHConfig(BaseModel):
    domain: str = "lab.local"
    modulus: int = 1024
    version: int = 2
    enable: bool = True


class HardeningConfig(BaseModel):
    """Hardening config for a router/switch (all optional)."""
    device: str
    hostname: str = ""
    banner_motd: str = ""
    enable_secret: str = ""
    users: list[LocalUser] = Field(default_factory=list)
    ssh: SSHConfig | None = None
    service_password_encryption: bool = True
    console_login_local: bool = True
    vty_ssh_only: bool = True
