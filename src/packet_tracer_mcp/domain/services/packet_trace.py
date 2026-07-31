"""
Lectura del event list de simulación de PT: qué hizo cada paquete y por qué.

Lógica pura, sin bridge — testeable con dicts sintéticos, igual que topology_diff.

Lo que hace útil a esto no es la lista de paquetes sino el log de decisiones: PT
expone, por frame y por capa OSI, la misma explicación en prosa que muestra en el
panel "PDU Details" de su GUI. Verificado contra PT 9.0.0.0810 con un ping de
PC1 a su gateway:

    L3 :: The source IP address is not specified. The device sets it to the port's IP address.
    L3 :: The destination IP address is in the same subnet. The device sets the next-hop to destination.
    L2 :: The next-hop IP address is not in the ARP table. The ARP process ... buffers this packet.

Eso convierte "el ping no anda" en una causa concreta.
"""

from __future__ import annotations

# getUserTrafficType() devuelve un entero. 0 e ICMP y 5 y ARP están MEDIDOS (ping
# de PC1 a su gateway: el ICMP queda en buffer y sale primero el ARP broadcast).
# El resto no se observó, así que se devuelve el crudo en vez de inventar nombres.
TRAFFIC_TYPES = {0: "ICMP", 5: "ARP"}

# Orden de precedencia al derivar UN estado por frame. Lo que bloquea va primero:
# un frame descartado importa más que uno "enviado" en el mismo tick.
_STATUS_ORDER = (
    ("dropped", "dropped"),
    ("collided_on_link", "collided_on_link"),
    ("collided_at_device", "collided_at_device"),
    ("not_forwarded", "not_forwarded"),
    ("unexpected", "unexpected"),
    ("buffered", "buffered"),
    ("in_transit", "in_transit"),
    ("accepted", "accepted"),
    ("sent", "sent"),
)

# Estados que significan "este paquete no llegó a destino".
FAILURE_STATUSES = frozenset({
    "dropped", "collided_on_link", "collided_at_device",
    "not_forwarded", "unexpected",
})


def traffic_type_label(raw) -> str:
    """Etiqueta del tipo de tráfico; deja pasar el crudo si no fue observado."""
    return TRAFFIC_TYPES.get(raw, f"type{raw}")


def frame_status(frame: dict) -> str:
    """Un solo estado por frame, a partir de las banderas booleanas de PT."""
    for flag, status in _STATUS_ORDER:
        if frame.get(flag):
            return status
    return "pending"


def summarize_trace(frames: list[dict]) -> dict:
    """Agrupa el event list y separa lo que falló de lo que no."""
    by_status: dict[str, int] = {}
    by_device: dict[str, int] = {}
    failures: list[dict] = []

    for frame in frames:
        status = frame_status(frame)
        frame["status"] = status
        by_status[status] = by_status.get(status, 0) + 1

        device = frame.get("device") or "?"
        by_device[device] = by_device.get(device, 0) + 1

        if status in FAILURE_STATUSES:
            failures.append({
                "device": device,
                "status": status,
                "destination": frame.get("destination", ""),
                "traffic": frame.get("traffic_type", ""),
                # La última decisión es la que explica el desenlace.
                "reason": (frame.get("decisions") or [{}])[-1].get("description", ""),
            })

    return {
        "frames": len(frames),
        "by_status": dict(sorted(by_status.items())),
        "by_device": dict(sorted(by_device.items())),
        "failures": failures,
        "clean": not failures,
    }
