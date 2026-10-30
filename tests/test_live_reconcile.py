"""Tests de reconcile (F16) y DHCP fallback del último host (F17)."""

from tests._registry_src import registry_source
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
        # The verify + reconcile loop moved to live_checks.verify_and_reconcile
        # (one canvas read instead of one trip per element); its behaviour is
        # tested in test_deploy_verify_trips.py.
        src = registry_source()
        body = src[src.index("def pt_live_deploy("):]
        assert "Reconcile (fix F16)" in body
        assert "verify_and_reconcile(" in body
        assert "reconciled_devices" in body

    def test_lwadddevice_has_global_fallback(self):
        """F16 root cause: lw.addDevice falla para Laptop-PT (type 17) → devuelve "" y no
        crea nada. lwAddDevice cae al addDevice global (resuelve por nombre de modelo),
        confiable. Verificado en vivo. Este guard evita perder el fallback. El helper vive
        ahora en la extensión (installMcpHelpers), no en un patch inyectado."""
        js = Path("EXTENSION/script-engine/main.js").read_text(encoding="utf-8")
        assert "GLOBAL.lwAddDevice = function" in js
        assert "if (!ipc.network().getDevice(name))" in js
        assert "addDevice(name, model, x, y)" in js


# On the 116-device deploy (2026-10-26) the first device's check timed out while PT was
# still running 347 queued commands; the timeout counted as MISSING and the reconcile
# re-sent its lwAddDevice, stacking an unconfigured duplicate MLS-CORE-SCZ on the canvas.

def test_reconcile_never_creates_a_device_that_already_exists():
    from src.packet_tracer_mcp.adapters.mcp.tools.live import reconcile_commands
    commands = [
        'lwAddDevice("CORE", 16, "3560-24PS", 1500, 720);',
        'lwAddDevice("PC1", 8, "PC-PT", 100, 100);',
        'configureIosDevice("CORE", "hostname CORE");',
    ]
    retry = reconcile_commands(commands, {"CORE"})
    add = next(c for c in retry if "lwAddDevice" in c)
    assert add.startswith('if(!ipc.network().getDevice("CORE")){')
    assert not any('"PC1"' in c for c in retry)
    assert any("configureIosDevice" in c for c in retry)


def test_a_timed_out_check_is_not_a_missing_device():
    from src.packet_tracer_mcp.adapters.mcp.tools.live import device_check_status
    assert device_check_status("OK") == "ok"
    assert device_check_status("MISSING") == "missing"
    assert device_check_status(None) == "unknown"
