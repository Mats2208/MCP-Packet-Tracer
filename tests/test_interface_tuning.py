"""Tests de ajuste fino de interfaz."""

import pytest

from src.packet_tracer_mcp.domain.models.interface_tuning import InterfaceTuning
from src.packet_tracer_mcp.domain.rules.interface_tuning_rules import (
    validate_interface_tuning, validate_interface_tuning_against_topology,
)
from src.packet_tracer_mcp.infrastructure.generator.interface_tuning_cli_generator import (
    generate_interface_tuning_cli,
)
from src.packet_tracer_mcp.application.use_cases.apply_interface_tuning import (
    apply_interface_tuning_uc,
)


class TestInterfaceTuningValidation:
    def test_clock_rate_on_non_serial_rejected(self):
        cfg = InterfaceTuning(router="R1", interface="GigabitEthernet0/0", clock_rate=64000)
        assert not validate_interface_tuning(cfg).is_valid

    def test_clock_rate_on_serial_passes(self):
        cfg = InterfaceTuning(router="R1", interface="Serial0/0/0", clock_rate=64000)
        assert validate_interface_tuning(cfg).is_valid

    def test_nonstandard_clock_rate_warns(self):
        cfg = InterfaceTuning(router="R1", interface="Serial0/0/0", clock_rate=12345)
        result = validate_interface_tuning(cfg)
        assert result.is_valid
        assert result.warnings

    def test_interface_not_found(self):
        cfg = InterfaceTuning(router="R1", interface="Serial9/9/9", clock_rate=64000)
        live = [{"name": "R1", "model": "2911",
                 "ports": [{"name": "GigabitEthernet0/0"}, {"name": "GigabitEthernet0/1"}]}]
        result = validate_interface_tuning_against_topology(cfg, live)
        assert not result.is_valid


class TestInterfaceTuningGenerator:
    def test_cli_lines(self):
        cfg = InterfaceTuning(router="R1", interface="Serial0/0/0",
                              clock_rate=64000, bandwidth=64, ospf_cost=10)
        lines = generate_interface_tuning_cli(cfg)
        assert "interface Serial0/0/0" in lines
        assert " clock rate 64000" in lines
        assert " bandwidth 64" in lines
        assert " ip ospf cost 10" in lines


class TestApplyInterfaceTuningUseCase:
    def test_dry_run_payload(self):
        cfg = InterfaceTuning(router="R1", interface="Serial0/0/0", clock_rate=64000)
        result = apply_interface_tuning_uc(cfg, dry_run=True)
        assert result["valid"]
        assert not result["sent"]
        assert "\n" not in result["js_payload"]
        assert 'configureIosDevice("R1"' in result["js_payload"]
