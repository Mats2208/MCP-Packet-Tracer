"""Tests de inspección de puertos (pt_inspect_ports) y del resto de Fase 1.

Los valores numéricos están verificados contra PT 9.0.0.0810: getNatMode()
devuelve 0 en un puerto limpio y 1 tras `ip nat inside`.
"""

from pathlib import Path

import pytest

from src.packet_tracer_mcp.domain.services.port_inspect import (
    NAT_MODES,
    nat_mode_label,
    summarize_ports,
)


def _port(**overrides) -> dict:
    base = {"name": "GigabitEthernet0/0", "up": True, "protocol_up": True, "linked": True}
    base.update(overrides)
    return base


def _dev(name: str = "R1", ports=None) -> dict:
    return {"name": name, "model": "2911", "ports": ports if ports is not None else [_port()]}


class TestNatModeLabel:
    @pytest.mark.parametrize("raw,label", [(0, "none"), (1, "inside"), (2, "outside")])
    def test_known_modes(self, raw, label):
        assert nat_mode_label(raw) == label

    def test_unknown_mode_keeps_the_raw_value(self):
        """Un valor nuevo de PT no debe perderse ni romper la tool."""
        assert nat_mode_label(7) == "unknown(7)"

    def test_none_is_not_confused_with_mode_zero(self):
        assert nat_mode_label(None) == "unknown(None)"
        assert NAT_MODES[0] == "none"


class TestSummarizePorts:
    def test_counts(self):
        result = summarize_ports([_dev(ports=[
            _port(name="Gi0/0", up=True, linked=True),
            _port(name="Gi0/1", up=False, linked=False),
            _port(name="Gi0/2", up=True, linked=False),
        ])])
        assert result["devices_inspected"] == 1
        assert result["ports_total"] == 3
        assert result["ports_up"] == 2
        assert result["ports_linked"] == 1

    def test_empty_input(self):
        result = summarize_ports([])
        assert result["ports_total"] == 0
        assert result["anomalies"] == []

    def test_healthy_ports_raise_nothing(self):
        assert summarize_ports([_dev()])["anomalies"] == []

    def test_linked_but_down_is_flagged(self):
        """Cable puesto y puerto down: shutdown olvidado o cable equivocado."""
        result = summarize_ports([_dev(ports=[_port(up=False, linked=True)])])
        assert len(result["anomalies"]) == 1
        anomaly = result["anomalies"][0]
        assert anomaly["issue"] == "linked_but_down"
        assert anomaly["device"] == "R1"
        assert anomaly["port"] == "GigabitEthernet0/0"

    def test_protocol_down_is_flagged(self):
        result = summarize_ports([_dev(ports=[_port(up=True, protocol_up=False)])])
        assert result["anomalies"][0]["issue"] == "protocol_down"

    def test_down_and_unlinked_is_not_an_anomaly(self):
        """Un puerto libre y apagado es lo normal, no un hallazgo."""
        assert summarize_ports([_dev(ports=[_port(up=False, linked=False)])])["anomalies"] == []

    def test_anomalies_are_not_double_counted(self):
        """linked_but_down y protocol_down son exclusivos: un puerto da un hallazgo."""
        result = summarize_ports([_dev(ports=[_port(up=False, protocol_up=False, linked=True)])])
        assert len(result["anomalies"]) == 1

    def test_missing_protocol_up_defaults_to_ok(self):
        """Modelos que no exponen isProtocolUp mandan null: no inventar una anomalía."""
        port = _port()
        del port["protocol_up"]
        assert summarize_ports([_dev(ports=[port])])["anomalies"] == []

    def test_multiple_devices(self):
        result = summarize_ports([
            _dev("R1", [_port(up=False, linked=True)]),
            _dev("R2", [_port()]),
        ])
        assert result["devices_inspected"] == 2
        assert [a["device"] for a in result["anomalies"]] == ["R1"]


class TestPhase1Readers:
    """Guards sobre los lectores JS. Son closures en register_tools, así que se
    verifican por texto, igual que TestReconcileWiring en test_live_reconcile.py."""

    def _src(self) -> str:
        return Path("src/packet_tracer_mcp/adapters/mcp/tool_registry.py").read_text(
            encoding="utf-8"
        )

    def test_port_getters_are_feature_detected(self):
        """La superficie de Port cambia por modelo; un método ausente lanza y abre
        un modal que congela el bridge."""
        src = self._src()
        for method in ("isProtocolUp", "getMacAddress", "getNatMode", "getAclInID", "getMtu"):
            assert f"typeof __p.{method} === 'function'" in src

    def test_power_control_is_feature_detected(self):
        """Guard defensivo: la superficie varía por build.

        Medido en PT 9.0.0.0810 TODOS los dispositivos exponen setPower/getPower
        —routers, switches, PC-PT y hasta el "Power Distribution Device"— así que
        hoy esta rama no se toma. Se mantiene porque un método ausente lanza y
        abre un modal que congela el bridge hasta que un humano lo cierre.
        """
        src = self._src()
        assert "typeof __d.setPower !== 'function'" in src
        assert "supported: false" in src

    def test_skipboot_is_guarded_separately_from_power(self):
        """skipBoot/isBooting SÍ faltan en los hosts: PC-PT no los tiene."""
        src = self._src()
        assert "typeof __d.skipBoot === 'function'" in src
        assert "typeof __d.isBooting === 'function'" in src

    def test_vlan_reader_guards_each_entry(self):
        src = self._src()
        assert "getProcess('VlanManager')" in src
        assert "catch (__ve) {}" in src

    def test_device_names_go_through_json_dumps(self):
        """Regla de AGENTS.md: nunca interpolar un nombre crudo en el JS."""
        src = self._src()
        assert "name = json.dumps(switch.strip())" in src
        assert "name = json.dumps(device.strip())" in src
        assert "want = json.dumps(device.strip())" in src
