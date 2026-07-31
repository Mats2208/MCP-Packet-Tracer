"""
Resumen de la inspección de puertos vivos de PT.

Lógica pura, sin bridge — testeable con dicts sintéticos, igual que topology_diff.

El detalle por puerto lo devuelve el lector del bridge; acá solo se agrega y se
marcan las anomalías que un humano miraría primero. pt_health_check ya cubre el
barrido de toda la topología (links caídos, IPs duplicadas): esto es la vista de
detalle de UN dispositivo, así que no repite esos chequeos globales.
"""

from __future__ import annotations

# Verificado contra PT 9.0.0.0810: getNatMode() devuelve 0 en un puerto limpio y
# 1 tras `ip nat inside`. El 2 es el único valor restante en IOS (`ip nat
# outside`) — inferido, no observado.
NAT_MODES = {0: "none", 1: "inside", 2: "outside"}


def nat_mode_label(raw) -> str:
    """Etiqueta legible del modo NAT; deja pasar el crudo si aparece un valor nuevo."""
    return NAT_MODES.get(raw, f"unknown({raw})")


def summarize_ports(devices: list[dict]) -> dict:
    """Agrega el detalle por puerto y marca anomalías.

    `devices` es [{name, model, ports: [{name, up, linked, ip, ...}]}].
    """
    total = 0
    up = 0
    linked = 0
    anomalies: list[dict] = []

    for dev in devices:
        dname = dev.get("name", "?")
        for port in dev.get("ports", []):
            total += 1
            p_up = bool(port.get("up"))
            p_linked = bool(port.get("linked"))
            if p_up:
                up += 1
            if p_linked:
                linked += 1

            pname = port.get("name", "?")
            # Cable puesto y el puerto no levanta: es el síntoma clásico de un
            # `shutdown` olvidado o de un tipo de cable equivocado.
            if p_linked and not p_up:
                anomalies.append({
                    "device": dname, "port": pname, "issue": "linked_but_down",
                    "detail": "Tiene cable pero el puerto está down (¿shutdown o cable incorrecto?).",
                })
            # Capa 1 arriba pero protocolo abajo: encapsulación o keepalive.
            elif p_up and not port.get("protocol_up", True):
                anomalies.append({
                    "device": dname, "port": pname, "issue": "protocol_down",
                    "detail": "Línea up pero protocolo down (encapsulación o keepalive).",
                })

    return {
        "devices_inspected": len(devices),
        "ports_total": total,
        "ports_up": up,
        "ports_linked": linked,
        "anomalies": anomalies,
    }
