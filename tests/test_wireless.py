"""Tests de laptops por WiFi (J)."""

import pytest

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.domain.models.errors import ErrorCode
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_executable_script, generate_ptbuilder_script,
)


def _wifi_plan(laptops=2, pcs=1):
    req = TopologyRequest(routers=1, pcs_per_lan=pcs, laptops_per_lan=laptops,
                          wireless_laptops=True, dhcp=True)
    return plan_from_request(req)


class TestWirelessTopology:
    def test_laptops_marked_wireless(self):
        plan, _ = _wifi_plan()
        laptops = [d for d in plan.devices if d.category == "laptop"]
        assert laptops
        assert all(lt.wireless for lt in laptops)

    def test_access_point_created(self):
        plan, _ = _wifi_plan()
        aps = [d for d in plan.devices if d.category == "accesspoint"]
        assert len(aps) == 1
        assert aps[0].name == "WAP1"

    def test_no_ethernet_link_for_wireless_laptops(self):
        plan, _ = _wifi_plan()
        laptop_names = {d.name for d in plan.devices if d.category == "laptop"}
        for link in plan.links:
            assert link.device_a not in laptop_names
            assert link.device_b not in laptop_names

    def test_ap_linked_to_switch(self):
        plan, _ = _wifi_plan()
        sw = next(d for d in plan.devices if d.category == "switch")
        ap_links = [l for l in plan.links
                    if "WAP1" in (l.device_a, l.device_b)]
        assert len(ap_links) == 1
        assert sw.name in (ap_links[0].device_a, ap_links[0].device_b)


class TestWirelessGenerator:
    def test_nic_swap_emitted(self):
        plan, _ = _wifi_plan(laptops=2)
        script = generate_ptbuilder_script(plan)
        assert script.count("swapLaptopToWireless(") == 2

    def test_wireless_laptops_get_dhcp(self):
        plan, _ = _wifi_plan(laptops=1)
        script = generate_executable_script(plan)
        lt = next(d for d in plan.devices if d.category == "laptop")
        assert f'configurePcIp("{lt.name}", true)' in script


class TestWiredLaptopsStillWork:
    def test_wired_laptops_have_links(self):
        req = TopologyRequest(routers=1, pcs_per_lan=1, laptops_per_lan=2,
                              wireless_laptops=False, dhcp=True)
        plan, _ = plan_from_request(req)
        laptop_names = {d.name for d in plan.devices if d.category == "laptop"}
        linked = [l for l in plan.links
                  if l.device_a in laptop_names or l.device_b in laptop_names]
        assert len(linked) == 2  # cada laptop cableada
        # sin AP automático
        assert not [d for d in plan.devices if d.category == "accesspoint"]


class TestOneAccessPointPerLan:
    """Regresion: con N LANs se creaba UN solo AP, cableado al switch de la LAN 1.

    Verificado contra PT 9.0.1: la laptop LT9, planificada en la LAN 5, recibia
    192.168.0.5/24 — del pool DHCP de la LAN 1 — porque su unico AP colgaba de
    SW1. Todas las laptops inalambricas caian en la primera subred sin importar
    donde las hubiera puesto el plan.
    """

    def _multi_lan_wifi(self, routers=3, laptops=2):
        req = TopologyRequest(routers=routers, pcs_per_lan=1,
                              laptops_per_lan=laptops, wireless_laptops=True,
                              dhcp=True)
        plan, _ = plan_from_request(req)
        return plan

    def test_one_ap_per_lan(self):
        plan = self._multi_lan_wifi(routers=3)
        aps = [d for d in plan.devices if d.category == "accesspoint"]
        assert len(aps) == 3

    def test_each_ap_hangs_off_a_different_switch(self):
        plan = self._multi_lan_wifi(routers=3)
        ap_names = {d.name for d in plan.devices if d.category == "accesspoint"}
        switch_per_ap = {}
        for link in plan.links:
            if link.device_b in ap_names:
                switch_per_ap[link.device_b] = link.device_a
            elif link.device_a in ap_names:
                switch_per_ap[link.device_a] = link.device_b
        assert len(switch_per_ap) == 3, f"AP sin cablear: {switch_per_ap}"
        assert len(set(switch_per_ap.values())) == 3, (
            f"varios AP en el mismo switch: {switch_per_ap}"
        )

    def test_ap_lands_on_the_switch_of_its_own_lan(self):
        plan = self._multi_lan_wifi(routers=3)
        switches = [d for d in plan.devices if d.category == "switch"]
        aps = [d for d in plan.devices if d.category == "accesspoint"]
        for idx, ap in enumerate(aps):
            partner = None
            for link in plan.links:
                if link.device_b == ap.name:
                    partner = link.device_a
                elif link.device_a == ap.name:
                    partner = link.device_b
            assert partner == switches[idx].name, (
                f"{ap.name} deberia colgar de {switches[idx].name}, cuelga de {partner}"
            )

    def test_no_ap_for_a_lan_without_wireless_laptops(self):
        req = TopologyRequest(routers=3, pcs_per_lan=1,
                              laptops_per_lan=[2, 0, 2],
                              wireless_laptops=True, dhcp=True)
        plan, _ = plan_from_request(req)
        aps = [d for d in plan.devices if d.category == "accesspoint"]
        assert len(aps) == 2


class TestAmbiguousWirelessAssociationIsWarned:
    """Un AP por LAN hace POSIBLE el direccionamiento correcto, no lo garantiza.

    Medido contra PT 9.0.1 sobre la topologia de 6 LANs: con WAP1 encendido,
    LT9 —que tiene WAP5 al lado, en su propia LAN— siguio con 192.168.0.5, del
    pool de la LAN 1. Apagando WAP1 y reiniciandola, tomo 192.168.4.25: la
    subred que le corresponde. PT NO elige el AP mas cercano; la asociacion es
    pegajosa y, entre APs que comparten el SSID por defecto, arbitraria.

    Y el plan no puede desambiguarla: el SSID del AP si se puede cambiar
    (WirelessServerProcess.setSsid, pt_wireless_ap), pero el cliente solo se une a
    "Default" y su API de perfiles (addProfile) esta rota. Asi que lo honesto es avisar.
    """

    def _plan(self, **kw):
        params = dict(routers=3, pcs_per_lan=1, laptops_per_lan=2,
                      wireless_laptops=True, dhcp=True)
        params.update(kw)
        return plan_from_request(TopologyRequest(**params))

    def test_several_aps_with_wireless_hosts_raises_a_warning(self):
        _, result = self._plan(routers=3)
        codes = {w.code for w in result.warnings}
        assert ErrorCode.WIRELESS_AMBIGUOUS_ASSOCIATION in codes

    def test_the_warning_does_not_invalidate_the_plan(self):
        """Es una limitacion de PT, no un error del plan: se despliega igual."""
        _, result = self._plan(routers=3)
        assert result.is_valid

    def test_a_single_ap_is_unambiguous(self):
        _, result = self._plan(routers=1)
        codes = {w.code for w in result.warnings}
        assert ErrorCode.WIRELESS_AMBIGUOUS_ASSOCIATION not in codes

    def test_wired_laptops_never_warn(self):
        _, result = self._plan(routers=3, wireless_laptops=False)
        codes = {w.code for w in result.warnings}
        assert ErrorCode.WIRELESS_AMBIGUOUS_ASSOCIATION not in codes
