"""Tests de diff + health-check (lógica pura sobre dicts sintéticos)."""

import pytest

from src.packet_tracer_mcp.domain.models.plans import TopologyPlan, DevicePlan
from src.packet_tracer_mcp.domain.services.topology_diff import diff, health_check


def _plan():
    return TopologyPlan(devices=[
        DevicePlan(name="R1", model="2911", category="router",
                   interfaces={"GigabitEthernet0/0": "192.168.0.1/24"}),
        DevicePlan(name="PC1", model="PC-PT", category="pc",
                   interfaces={"FastEthernet0": "192.168.0.2/24"}),
    ])


class TestDiff:
    def test_in_sync(self):
        live = [
            {"name": "R1", "model": "2911",
             "ports": [{"name": "GigabitEthernet0/0", "ip": "192.168.0.1", "up": True, "linked": True}]},
            {"name": "PC1", "model": "PC-PT",
             "ports": [{"name": "FastEthernet0", "ip": "192.168.0.2", "up": True, "linked": True}]},
        ]
        result = diff(_plan(), live)
        assert result["in_sync"]

    def test_missing_device(self):
        live = [{"name": "R1", "model": "2911", "ports": []}]
        result = diff(_plan(), live)
        assert "PC1" in result["missing_devices"]
        assert not result["in_sync"]

    def test_extra_device(self):
        live = [
            {"name": "R1", "model": "2911", "ports": []},
            {"name": "PC1", "model": "PC-PT", "ports": []},
            {"name": "ROGUE", "model": "PC-PT", "ports": []},
        ]
        result = diff(_plan(), live)
        assert "ROGUE" in result["extra_devices"]

    def test_ip_mismatch(self):
        live = [
            {"name": "R1", "model": "2911",
             "ports": [{"name": "GigabitEthernet0/0", "ip": "10.9.9.9"}]},
            {"name": "PC1", "model": "PC-PT", "ports": []},
        ]
        result = diff(_plan(), live)
        assert any(m["device"] == "R1" for m in result["ip_mismatches"])


class TestHealthCheck:
    def test_down_link(self):
        live = [{"name": "R1", "model": "2911",
                 "ports": [{"name": "Gig0/0", "ip": "1.1.1.1", "up": False, "linked": True}]}]
        result = health_check(live)
        assert result["down_links"]
        assert not result["healthy"]

    def test_duplicate_ips(self):
        live = [
            {"name": "PC1", "model": "PC-PT", "ports": [{"name": "Fa0", "ip": "192.168.0.5", "linked": True, "up": True}]},
            {"name": "PC2", "model": "PC-PT", "ports": [{"name": "Fa0", "ip": "192.168.0.5", "linked": True, "up": True}]},
        ]
        result = health_check(live)
        assert "192.168.0.5" in result["duplicate_ips"]

    def test_cabled_without_ip(self):
        live = [{"name": "PC3", "model": "PC-PT",
                 "ports": [{"name": "Fa0", "ip": "0.0.0.0", "linked": True, "up": True}]}]
        result = health_check(live)
        assert result["cabled_without_ip"]

    def test_healthy(self):
        live = [{"name": "R1", "model": "2911",
                 "ports": [{"name": "Gig0/0", "ip": "1.1.1.1", "up": True, "linked": True}]}]
        result = health_check(live)
        assert result["healthy"]
