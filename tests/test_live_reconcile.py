"""Tests de reconcile (F16) y DHCP fallback del último host (F17)."""

import re
from pathlib import Path

import pytest

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_executable_script,
)


class TestDHCPFallback:
    def test_last_host_per_lan_is_static(self):
        # 1 router, 3 PCs, DHCP on → 2 DHCP + 1 estático (el de mayor IP)
        req = TopologyRequest(routers=1, pcs_per_lan=3, dhcp=True)
        plan, _ = plan_from_request(req)
        script = generate_executable_script(plan)

        dhcp_calls = re.findall(r"configurePcIp\([^,]+, true\)", script)
        static_calls = re.findall(r"configurePcIp\([^,]+, false,", script)
        assert len(dhcp_calls) == 2
        assert len(static_calls) == 1
        # el estático es el de mayor IP (.4)
        assert "192.168.0.4" in script

    def test_two_lans_one_static_each(self):
        req = TopologyRequest(routers=2, pcs_per_lan=2, dhcp=True, routing="static")
        plan, _ = plan_from_request(req)
        script = generate_executable_script(plan)
        static_calls = re.findall(r"configurePcIp\([^,]+, false,", script)
        # un host estático por LAN (2 LANs)
        assert len(static_calls) == 2

    def test_no_dhcp_all_static(self):
        req = TopologyRequest(routers=1, pcs_per_lan=3, dhcp=False)
        plan, _ = plan_from_request(req)
        script = generate_executable_script(plan)
        assert "configurePcIp" in script
        assert ", true)" not in script  # ningún DHCP


class TestReconcileWiring:
    def test_pt_live_deploy_has_reconcile(self):
        src = Path("src/packet_tracer_mcp/adapters/mcp/tool_registry.py").read_text(encoding="utf-8")
        assert "Reconcile (fix F16)" in src
        assert "reconciled" in src
        assert "link_fail_objs" in src

    def test_lwadddevice_has_global_fallback(self):
        """F16 root cause: lw.addDevice falla para Laptop-PT (type 17) → devuelve "" y no
        crea nada. lwAddDevice ahora cae al addDevice global (resuelve por nombre de modelo),
        confiable. Verificado en vivo. Este guard evita perder el fallback."""
        src = Path("src/packet_tracer_mcp/adapters/mcp/tool_registry.py").read_text(encoding="utf-8")
        assert "if (!ipc.network().getDevice(name)) { try { addDevice(name, model, x, y); }" in src
