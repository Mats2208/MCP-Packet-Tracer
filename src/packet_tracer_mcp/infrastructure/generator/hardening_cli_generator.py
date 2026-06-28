"""Generador CLI para hardening de dispositivos."""

from __future__ import annotations

from ...domain.models.hardening import HardeningConfig


def generate_hardening_cli(cfg: HardeningConfig) -> list[str]:
    lines: list[str] = []

    if cfg.hostname:
        lines.append(f"hostname {cfg.hostname}")

    if cfg.service_password_encryption:
        lines.append("service password-encryption")

    if cfg.enable_secret:
        lines.append(f"enable secret {cfg.enable_secret}")

    for u in cfg.users:
        lines.append(f"username {u.username} privilege {u.privilege} secret {u.secret}")

    # SSH: requiere hostname + domain-name + claves RSA
    if cfg.ssh and cfg.ssh.enable:
        host = cfg.hostname or cfg.device
        # hostname ya emitido arriba si se dio; aseguramos domain-name + claves
        lines.append(f"ip domain-name {cfg.ssh.domain}")
        lines.append(f"crypto key generate rsa modulus {cfg.ssh.modulus}")
        lines.append(f"ip ssh version {cfg.ssh.version}")

    if cfg.banner_motd:
        # delimitador # (el texto no debe contener #)
        lines.append(f"banner motd #{cfg.banner_motd}#")

    if cfg.console_login_local and cfg.users:
        lines.append("line console 0")
        lines.append(" login local")
        lines.append(" exit")

    if cfg.vty_ssh_only and cfg.ssh and cfg.ssh.enable:
        lines.append("line vty 0 4")
        lines.append(" transport input ssh")
        lines.append(" login local")
        lines.append(" exit")

    return lines


def build_hardening_payload(cfg: HardeningConfig) -> str:
    return "\n".join(
        ["enable", "configure terminal", *generate_hardening_cli(cfg), "end", "write memory"]
    )


def build_hardening_js_call(device: str, ios_payload: str) -> str:
    safe_dev = device.replace("\\", "\\\\").replace('"', '\\"')
    safe_payload = (
        ios_payload.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )
    return f'configureIosDevice("{safe_dev}", "{safe_payload}");'
