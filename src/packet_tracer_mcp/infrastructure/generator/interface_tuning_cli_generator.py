"""Generador CLI para ajuste fino por interfaz."""

from __future__ import annotations

from ...domain.models.interface_tuning import InterfaceTuning


def generate_interface_tuning_cli(cfg: InterfaceTuning) -> list[str]:
    lines: list[str] = [f"interface {cfg.interface}"]
    if cfg.bandwidth is not None:
        lines.append(f" bandwidth {cfg.bandwidth}")
    if cfg.clock_rate is not None and cfg.is_serial():
        lines.append(f" clock rate {cfg.clock_rate}")
    if cfg.delay is not None:
        lines.append(f" delay {cfg.delay}")
    if cfg.ospf_cost is not None:
        lines.append(f" ip ospf cost {cfg.ospf_cost}")
    if cfg.ospf_priority is not None:
        lines.append(f" ip ospf priority {cfg.ospf_priority}")
    if cfg.ospf_hello_interval is not None:
        lines.append(f" ip ospf hello-interval {cfg.ospf_hello_interval}")
    if cfg.ospf_dead_interval is not None:
        lines.append(f" ip ospf dead-interval {cfg.ospf_dead_interval}")
    # La clave va ANTES de habilitar la autenticación: al revés, la interfaz
    # queda exigiendo auth sin tener con qué responder y el vecino se cae.
    if cfg.ospf_md5_key is not None:
        lines.append(
            f" ip ospf message-digest-key {cfg.ospf_md5_key_id} md5 {cfg.ospf_md5_key}"
        )
        lines.append(" ip ospf authentication message-digest")
    elif cfg.ospf_auth_key is not None:
        lines.append(f" ip ospf authentication-key {cfg.ospf_auth_key}")
        lines.append(" ip ospf authentication")
    lines.append(" exit")
    return lines


def build_interface_tuning_payload(cfg: InterfaceTuning) -> str:
    return "\n".join(
        ["enable", "configure terminal", *generate_interface_tuning_cli(cfg),
         "end", "write memory"]
    )


def build_interface_tuning_js_call(device: str, ios_payload: str) -> str:
    safe_dev = device.replace("\\", "\\\\").replace('"', '\\"')
    safe_payload = (
        ios_payload.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )
    return f'configureIosDevice("{safe_dev}", "{safe_payload}");'
