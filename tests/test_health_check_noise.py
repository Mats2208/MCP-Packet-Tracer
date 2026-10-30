"""`cabled_without_ip` solo tiene sentido donde SE ESPERA una IP.

Regresion observada sobre una topologia de 47 dispositivos: el barrido listaba
los puertos de acceso de cada 2960, los dos puertos del AP y el Ethernet6 de la
nube como "cableado sin IP". Ninguno de esos lleva IP jamas -- un puerto de
switch en capa 2 no tiene direccion por definicion -- asi que el ruido tapaba
los casos reales, que son los hosts a los que el DHCP no les llego.
"""

import pytest

from src.packet_tracer_mcp.domain.services.topology_diff import health_check


def _port(name, ip="", linked=True, up=True):
    return {"name": name, "ip": ip, "linked": linked, "up": up}


class TestLayer2PortsAreNotFlagged:
    def test_switch_access_ports_are_not_reported(self):
        live = [{
            "name": "SW1", "model": "2960-24TT",
            "ports": [_port("FastEthernet0/1"), _port("FastEthernet0/2"),
                      _port("GigabitEthernet0/1")],
        }]
        result = health_check(live)
        assert result["cabled_without_ip"] == []

    def test_access_point_ports_are_not_reported(self):
        live = [{"name": "WAP1", "model": "AccessPoint-PT",
                 "ports": [_port("Port 0"), _port("Port 1")]}]
        assert health_check(live)["cabled_without_ip"] == []

    def test_cloud_ports_are_not_reported(self):
        live = [{"name": "WAN", "model": "Cloud-PT",
                 "ports": [_port("Ethernet6")]}]
        assert health_check(live)["cabled_without_ip"] == []


class TestRealCasesStillReported:
    def test_host_without_ip_is_still_reported(self):
        """Un PC cableado sin IP es DHCP que no llego: eso si importa."""
        live = [{"name": "PC1", "model": "PC-PT",
                 "ports": [_port("FastEthernet0")]}]
        flagged = health_check(live)["cabled_without_ip"]
        assert flagged == [{"device": "PC1", "port": "FastEthernet0"}]

    def test_router_interface_without_ip_is_still_reported(self):
        live = [{"name": "R1", "model": "2911",
                 "ports": [_port("GigabitEthernet0/0")]}]
        flagged = health_check(live)["cabled_without_ip"]
        assert flagged == [{"device": "R1", "port": "GigabitEthernet0/0"}]

    def test_unknown_model_is_still_reported(self):
        """Si no se puede resolver el modelo, mejor avisar de mas que de menos."""
        live = [{"name": "X1", "model": "modelo-que-no-existe",
                 "ports": [_port("FastEthernet0")]}]
        assert health_check(live)["cabled_without_ip"] != []


class TestOtherChecksUnaffected:
    def test_down_link_on_a_switch_is_still_reported(self):
        live = [{"name": "SW1", "model": "2960-24TT",
                 "ports": [_port("FastEthernet0/1", linked=True, up=False)]}]
        result = health_check(live)
        assert result["down_links"] == [{"device": "SW1", "port": "FastEthernet0/1"}]
        assert not result["healthy"]

    def test_duplicate_ips_still_detected(self):
        live = [
            {"name": "PC1", "model": "PC-PT", "ports": [_port("FastEthernet0", ip="192.168.0.5")]},
            {"name": "PC2", "model": "PC-PT", "ports": [_port("FastEthernet0", ip="192.168.0.5")]},
        ]
        assert "192.168.0.5" in health_check(live)["duplicate_ips"]


class TestIpPhones:
    """A 7960's IP lives on its Vlan1 interface; the Switch and PC jacks are layer 2.
    Measured live on PT 9.0.1: registered phones were flagged "cabled without IP"."""

    def _phone(self, vlan_ip):
        return [{"name": "TEL1", "model": "7960",
                 "ports": [_port("Vlan1", ip=vlan_ip, linked=False),
                           _port("Switch"), _port("PC", linked=False)]}]

    def test_phone_with_an_address_is_not_flagged(self):
        assert health_check(self._phone("10.10.10.2"))["cabled_without_ip"] == []

    def test_phone_without_an_address_is_flagged_on_vlan1(self):
        flagged = health_check(self._phone(""))["cabled_without_ip"]
        assert flagged == [{"device": "TEL1", "port": "Vlan1"}]
