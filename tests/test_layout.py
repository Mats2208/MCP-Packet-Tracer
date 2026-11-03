"""Disposicion de los dispositivos en el canvas logico.

Regresiones observadas desplegando 6 routers x 4 PCs contra PT 9.0.1:

- PC1 salia en x=-60, es decir FUERA del canvas por la izquierda.
- Los clusters de LAN vecinos se pisaban (PC4 en x=180 y PC5 en x=190) porque
  el ancho del cluster (n_pcs * 80) superaba el espaciado entre LANs (250).
- Los servidores se colocaban en el extremo derecho pero se cablean al PRIMER
  switch, dibujando diagonales que cruzaban el diagrama entero.
"""

import pytest

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.domain.services.layout import ICON_H, ICON_W, LABEL_H


def _big_plan(**kw):
    params = dict(routers=6, pcs_per_lan=4, laptops_per_lan=2,
                  servers=3, has_wan=True)
    params.update(kw)
    plan, _ = plan_from_request(TopologyRequest(**params))
    return plan


class TestNoOffCanvasDevices:
    def test_no_negative_coordinates(self):
        plan = _big_plan()
        off = [(d.name, d.x, d.y) for d in plan.devices if d.x < 0 or d.y < 0]
        assert not off, f"dispositivos fuera del canvas: {off}"

    def test_no_negative_coordinates_with_crowded_lans(self):
        plan = _big_plan(routers=2, pcs_per_lan=8, laptops_per_lan=6)
        off = [(d.name, d.x, d.y) for d in plan.devices if d.x < 0 or d.y < 0]
        assert not off, f"dispositivos fuera del canvas: {off}"


class TestNoOverlap:
    def test_no_two_devices_share_a_position(self):
        plan = _big_plan()
        seen: dict[tuple[int, int], str] = {}
        clashes = []
        for d in plan.devices:
            key = (d.x, d.y)
            if key in seen:
                clashes.append((seen[key], d.name, key))
            seen[key] = d.name
        assert not clashes, f"dispositivos superpuestos: {clashes}"

    def test_no_two_icons_overlap(self):
        """Icon boxes (with their labels) never intersect, whatever side a LAN takes.

        This replaces a row-based gap check: LANs are no longer on one row, they hang
        off their router on different sides.
        """
        plan = _big_plan(routers=4, pcs_per_lan=4, laptops_per_lan=0, servers=0)
        boxes = [(d.name, d.x, d.y, d.x + ICON_W, d.y + ICON_H + LABEL_H) for d in plan.devices]
        clashes = [(a[0], b[0]) for i, a in enumerate(boxes) for b in boxes[i + 1:]
                   if a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]]
        assert not clashes, f"overlapping icons: {clashes}"


class TestServersNearTheirSwitch:
    def test_servers_sit_close_to_the_switch_they_cable_to(self):
        plan = _big_plan()
        servers = [d for d in plan.devices if d.category == "server"]
        switches = [d for d in plan.devices if d.category == "switch"]
        assert servers and switches

        # Se cablean al primer switch: deben quedar en su columna, no en la otra
        # punta del diagrama.
        sw0 = switches[0]
        far = [(s.name, s.x, sw0.x) for s in servers
               if abs(s.x - sw0.x) > 300]
        assert not far, f"servidores lejos de {sw0.name}: {far}"

    def test_servers_are_actually_cabled_to_that_switch(self):
        """Guarda del test anterior: si cambia a que switch se cablean, se entera."""
        plan = _big_plan()
        switches = [d for d in plan.devices if d.category == "switch"]
        server_names = {d.name for d in plan.devices if d.category == "server"}
        for link in plan.links:
            if link.device_b in server_names:
                assert link.device_a == switches[0].name
            elif link.device_a in server_names:
                assert link.device_b == switches[0].name
