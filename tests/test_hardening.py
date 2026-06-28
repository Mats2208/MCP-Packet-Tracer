"""Tests de hardening de dispositivos."""

import pytest

from src.packet_tracer_mcp.domain.models.hardening import HardeningConfig, LocalUser, SSHConfig
from src.packet_tracer_mcp.domain.rules.hardening_rules import (
    validate_hardening, validate_hardening_against_topology,
)
from src.packet_tracer_mcp.infrastructure.generator.hardening_cli_generator import (
    generate_hardening_cli,
)
from src.packet_tracer_mcp.application.use_cases.apply_hardening import (
    build_hardening_config, apply_hardening_uc,
)


class TestHardeningValidation:
    def test_ssh_without_domain_rejected(self):
        cfg = HardeningConfig(device="R1", ssh=SSHConfig(domain="", enable=True),
                              users=[LocalUser(username="a", secret="b")])
        assert not validate_hardening(cfg).is_valid

    def test_weak_modulus_warns(self):
        cfg = HardeningConfig(device="R1", ssh=SSHConfig(modulus=512),
                              users=[LocalUser(username="a", secret="b")])
        result = validate_hardening(cfg)
        assert result.is_valid  # warning, no error
        assert result.warnings

    def test_device_not_found(self):
        cfg = HardeningConfig(device="NOPE")
        result = validate_hardening_against_topology(cfg, [{"name": "R1", "model": "2911"}])
        assert not result.is_valid


class TestHardeningGenerator:
    def test_ssh_cli_lines(self):
        cfg = build_hardening_config(
            device="R1", hostname="R1", enable_secret="cisco",
            users=[{"username": "admin", "secret": "pass", "privilege": 15}],
            ssh={"domain": "lab.local", "modulus": 1024},
        )
        lines = generate_hardening_cli(cfg)
        assert "hostname R1" in lines
        assert "enable secret cisco" in lines
        assert "username admin privilege 15 secret pass" in lines
        assert "ip domain-name lab.local" in lines
        assert "crypto key generate rsa modulus 1024" in lines
        assert "ip ssh version 2" in lines
        assert " transport input ssh" in lines

    def test_banner(self):
        cfg = build_hardening_config(device="R1", banner_motd="Acceso autorizado")
        lines = generate_hardening_cli(cfg)
        assert "banner motd #Acceso autorizado#" in lines


class TestApplyHardeningUseCase:
    def test_dry_run_payload(self):
        cfg = build_hardening_config(device="R1", hostname="R1")
        result = apply_hardening_uc(cfg, dry_run=True)
        assert result["valid"]
        assert not result["sent"]
        assert "\n" not in result["js_payload"]
        assert 'configureIosDevice("R1"' in result["js_payload"]

    def test_device_not_found_via_topology(self):
        cfg = build_hardening_config(device="GHOST", hostname="X")
        result = apply_hardening_uc(
            cfg, query_pt_topology=lambda: [{"name": "R1", "model": "2911"}], dry_run=True,
        )
        assert not result["valid"]
