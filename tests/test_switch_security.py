"""Tests de STP y port-security."""

import pytest

from src.packet_tracer_mcp.domain.models.switch_security import STPConfig, PortSecurityConfig
from src.packet_tracer_mcp.domain.rules.switch_security_rules import (
    validate_stp, validate_port_security,
    validate_stp_against_topology,
)
from src.packet_tracer_mcp.infrastructure.generator.switch_security_cli_generator import (
    generate_stp_cli, generate_port_security_cli,
)
from src.packet_tracer_mcp.application.use_cases.apply_switch_security import (
    apply_stp_uc, apply_port_security_uc,
)


class TestSTP:
    def test_invalid_priority_rejected(self):
        cfg = STPConfig(switch="SW1", priority={10: 5000})  # no múltiplo de 4096
        assert not validate_stp(cfg).is_valid

    def test_valid_priority_passes(self):
        cfg = STPConfig(switch="SW1", priority={10: 24576})
        assert validate_stp(cfg).is_valid

    def test_cli_generation(self):
        cfg = STPConfig(switch="SW1", mode="rapid-pvst",
                        root_primary_vlans=[10], portfast_ports=["FastEthernet0/1"])
        lines = generate_stp_cli(cfg)
        assert "spanning-tree mode rapid-pvst" in lines
        assert "spanning-tree vlan 10 root primary" in lines
        assert " spanning-tree portfast" in lines

    def test_switch_not_found(self):
        cfg = STPConfig(switch="NOPE")
        result = validate_stp_against_topology(cfg, [{"name": "SW1", "model": "2960-24TT"}])
        assert not result.is_valid

    def test_dry_run_payload(self):
        cfg = STPConfig(switch="SW1", root_primary_vlans=[10])
        result = apply_stp_uc(cfg, dry_run=True)
        assert result["valid"]
        assert not result["sent"]
        assert "\n" not in result["js_payload"]
        assert 'configureIosDevice("SW1"' in result["js_payload"]


class TestPortSecurity:
    def test_invalid_max_rejected(self):
        cfg = PortSecurityConfig(switch="SW1", port="Fa0/1", max_mac=0)
        assert not validate_port_security(cfg).is_valid

    def test_invalid_mac_rejected(self):
        cfg = PortSecurityConfig(switch="SW1", port="Fa0/1", static_macs=["xyz"])
        assert not validate_port_security(cfg).is_valid

    def test_cli_generation(self):
        cfg = PortSecurityConfig(switch="SW1", port="FastEthernet0/1",
                                 max_mac=2, violation="restrict", sticky=True)
        lines = generate_port_security_cli(cfg)
        assert " switchport port-security maximum 2" in lines
        assert " switchport port-security violation restrict" in lines
        assert " switchport port-security mac-address sticky" in lines

    def test_dry_run_payload(self):
        cfg = PortSecurityConfig(switch="SW1", port="FastEthernet0/1", max_mac=2)
        result = apply_port_security_uc(cfg, dry_run=True)
        assert result["valid"]
        assert "\\n" in result["js_payload"]
        assert "\n" not in result["js_payload"]
