"""Convergence reads (pt_read_routes, pt_read_ospf, ... and pt_read_vlans' ports).

The raw shapes below are copied from a live probe of PT 9 (2026-10-30): the
structs come back as plain JS objects. The JS is run under Node against a fake
PT built from those shapes, then normalized by domain/services/convergence.py.
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.domain.services.convergence import (
    acl_report, arp_report, cdp_report, mac_table_report, nat_report, normalize_routes,
    ospf_report, ospf_state_name, protocol_letter, routes_report, select_devices,
    split_bridge_id, stp_report, vlans_report,
)
from src.packet_tracer_mcp.infrastructure.generator import state_js
from tests._pt_js import requires_node, run_pt_js
from tests._registry_src import registry_source

# --- raw structs as PT returned them -------------------------------------------------

R1_TABLE = {"routingNetworks": [
    {"network": "192.168.1.0", "subnetMask": "255.255.255.0", "routingEntries": [
        {"networkId": "192.168.1.0", "subnetMask": "255.255.255.0", "nextHop": "0.0.0.0",
         "portName": "GigabitEthernet0/0", "adminDistance": 0, "metric": 0, "typeOfProtocol": 67}]},
    {"network": "192.168.1.1", "subnetMask": "255.255.255.255", "routingEntries": [
        {"networkId": "192.168.1.1", "subnetMask": "255.255.255.255", "nextHop": "0.0.0.0",
         "portName": "GigabitEthernet0/0", "adminDistance": 0, "metric": 0, "typeOfProtocol": 76}]},
    {"network": "192.168.2.0", "subnetMask": "255.255.255.0", "routingEntries": [
        {"networkId": "192.168.2.0", "subnetMask": "255.255.255.0", "nextHop": "10.0.0.2",
         "portName": "GigabitEthernet0/1", "adminDistance": 110, "metric": 2,
         "typeOfProtocol": 79, "advRoute": False, "realCost": 2}]},
]}

FULL_NEIGHBOR = {"address": "10.0.0.2", "areaId": "0.0.0.0", "backupDr": "10.0.0.1",
                 "deadInterval": 40, "dr": "10.0.0.2", "isMaster": False, "isVirtual": False,
                 "option": 66, "port": "GigabitEthernet0/1", "priority": 1,
                 "routerId": "2.2.2.2", "seqNumber": 0, "state": 8}

FAKE_PT = r"""
function __raise(){throw new Error('invalid string position');}
function __port(name, o){var p={getName:function(){return name;}};
  for(var k in o){(function(k){p[k]=function(){return o[k];};})(k);} return p;}
var __nbr = %(neighbor)s;
var R1 = {
  getName:function(){return 'R1';}, getModel:function(){return '2911';},
  getPorts:function(){return ['GigabitEthernet0/0','GigabitEthernet0/1','Vlan1'];},
  getPort:function(n){
    if(n==='GigabitEthernet0/0'){return __port(n,{getAclInID:'BLOCK',getAclOutID:'',getNatMode:'1'});}
    if(n==='GigabitEthernet0/1'){return __port(n,{getAclInID:'',getAclOutID:'GHOST',getNatMode:'2'});}
    return __port(n,{getAclInID:'',getAclOutID:'',getNatMode:'0'});},
  getProcess:function(n){
    if(n==='RoutingProcess'){return {getRoutingTable:function(){return %(table)s;}};}
    if(n==='OspfMainProcess'){return {getOspfProcessCount:function(){return 1;},
      getOspfProcessAt:function(){return {getProcessId:function(){return 1;},
        getRouterId:function(){return '1.1.1.1';}, getAreaCount:function(){return 1;},
        getAreaAt:function(){return {getAreaId:function(){return '0.0.0.0';},
          getNeighborTable:function(p){
            if(p==='Vlan1'){throw new Error('no table');}
            if(p!=='GigabitEthernet0/1'){return null;}
            return {getNeighborCount:function(){return 1;}, getNeighborAt:function(){return __nbr;},
                    getAdjNeighborCount:function(){return 1;}};}};}};}};}
    if(n==='ArpProcess'){return {getArpTable:function(){return {entries:[
      {ipAddress:'192.168.1.10',macAddress:'0001.4321.ABCD',portName:'GigabitEthernet0/0'}]};}};}
    if(n==='CdpProcess'){return {getNeighborTable:function(){return {
      getNeighborTableCount:function(){return 0;}, getCdpNeighborAt:function(){return null;}};}};}
    if(n==='AclProcess'){return {getAclCount:function(){return 1;}, getAclAt:function(){return {
      getAclId:function(){return 'BLOCK';}, isExtended:function(){return true;},
      getCommandCount:function(){return 2;},
      getCommandAt:function(i){return ['deny ip any host 8.8.8.8','permit ip any any'][i];}};}};}
    if(n==='NatProcess'){return {getNatTable:function(){return {entries:[]};},
      getInSrcListCount:function(){return 1;}};}
    if(n==='Ospf'||n==='Arp'||n==='Cdp'||n==='Nat'||n==='Stp'){__raise();}
    return null;}
};
function __sw(name, isRoot, rootId){ return {
  getName:function(){return name;}, getModel:function(){return '2960-24TT';},
  getPorts:function(){return ['FastEthernet0/1','FastEthernet0/2','GigabitEthernet0/1'];},
  getPort:function(n){
    if(n==='FastEthernet0/1'){return __port(n,{isAccessPort:true,getAccessVlan:10,getNativeVlanId:1,
      isPortUp:true,getPortSecurity:{isEnabled:function(){return true;},
        getMaxMacNumber:function(){return 2;}, getViolationCount:function(){return 0;}}});}
    if(n==='FastEthernet0/2'){return __port(n,{isAccessPort:true,getAccessVlan:30,getNativeVlanId:1,
      isPortUp:false,getPortSecurity:null});}
    return __port(n,{isAccessPort:false,getAccessVlan:1,getNativeVlanId:99,isPortUp:true,
      getPortSecurity:null});},
  getProcess:function(n){
    if(n==='VlanManager'){var vs=[[1,'default',true],[10,'SALES',false],[1002,'fddi-default',true]];
      return {getVlanCount:function(){return vs.length;}, getMaxVlans:function(){return 255;},
        getVlanIntCount:function(){return 1;},
        getVlanAt:function(i){var v=vs[i];return {getVlanNumber:function(){return v[0];},
          getName:function(){return v[1];}, isDefault:function(){return v[2];},
          getMacTable:function(){return v[0]===1?{entries:[
            {isDynamic:true,macAddress:'0001.4321.ABCD',port:'FastEthernet0/1'}]}:{entries:[]};}};}};}
    if(n==='StpMainProcess'){return {getStpProcess:function(v){ if(v===1002){throw new Error('x');}
      return {isRootBridge:function(){return isRoot;}, getRootBridgeId:function(){return rootId;},
        getPortCount:function(){return 3;}};}};}
    if(n==='Stp'){__raise();}
    return null;}
};}
var SW1 = __sw('SW1', true, '32769 / 0001.63BE.8468');
var SW2 = __sw('SW2', false, '%(sw2_root)s');
var PC1 = {getName:function(){return 'PC1';}, getModel:function(){return 'PC-PT';},
  getPorts:function(){return ['FastEthernet0'];}, getPort:function(){return null;},
  getProcess:function(n){ if(n==='ArpProcess'){return {getArpTable:function(){return {entries:[]};}};}
    return null;}};
var __devs = [R1, SW1, SW2, PC1];
var ipc = {network:function(){return {
  getDeviceCount:function(){return __devs.length;}, getDeviceAt:function(i){return __devs[i];},
  getDevice:function(n){for(var i=0;i<__devs.length;i++){if(__devs[i].getName()===n){return __devs[i];}}
    return null;}};}};
"""


def _fake(neighbor: dict = FULL_NEIGHBOR, sw2_root: str = "32769 / 0001.63BE.8468") -> str:
    return FAKE_PT % {"neighbor": json.dumps(neighbor), "table": json.dumps(R1_TABLE),
                      "sw2_root": sw2_root}


def _run(js: str, tmp_path, **fake) -> dict:
    reports = run_pt_js(js, _fake(**fake), tmp_path)
    assert len(reports) == 1, reports
    assert not reports[0].startswith("ERROR:"), reports[0]
    return json.loads(reports[0])


def _devices(data: dict, device: str = "", what: str = "x") -> list[dict]:
    out = select_devices(data, device, what)
    assert isinstance(out, list), out
    return out


# --- pure normalization ---------------------------------------------------------------

class TestRouteNormalization:
    def test_protocol_letters_from_ascii_codes(self):
        """67 C, 76 L, 79 O measured; 83 S, 68 D, 82 R by the same rule."""
        assert [protocol_letter(c) for c in (67, 76, 79, 83, 68, 82)] == list("CLOSDR")
        assert protocol_letter("79") == "O" and protocol_letter("S") == "S"
        assert protocol_letter(None) == "?"

    def test_flattens_networks(self):
        routes = normalize_routes(R1_TABLE)
        assert [(r["prefix"], r["protocol"], r["protocol_name"]) for r in routes] == [
            ("192.168.1.0/24", "C", "connected"), ("192.168.1.1/32", "L", "local"),
            ("192.168.2.0/24", "O", "OSPF")]
        assert routes[0]["next_hop"] is None, "0.0.0.0 on a connected route means no next hop"
        assert routes[2]["next_hop"] == "10.0.0.2"
        assert (routes[2]["admin_distance"], routes[2]["metric"]) == (110, 2)

    def test_garbage_does_not_crash(self):
        assert normalize_routes(None) == []
        assert normalize_routes({"routingNetworks": [None, {"routingEntries": None}]}) == []

    def test_gap_between_routers_is_reported(self):
        r2 = {"routingNetworks": [
            {"network": "192.168.2.0", "subnetMask": "255.255.255.0", "routingEntries": [
                {"networkId": "192.168.2.0", "subnetMask": "255.255.255.0", "nextHop": "0.0.0.0",
                 "portName": "GigabitEthernet0/0", "adminDistance": 0, "metric": 0,
                 "typeOfProtocol": 67}]},
            {"network": "10.9.0.0", "subnetMask": "255.255.0.0", "routingEntries": [
                {"networkId": "10.9.0.0", "subnetMask": "255.255.0.0", "nextHop": "10.0.0.1",
                 "portName": "GigabitEthernet0/1", "adminDistance": 110, "metric": 3,
                 "typeOfProtocol": 79}]}]}
        out = routes_report([{"name": "R1", "table": R1_TABLE}, {"name": "R2", "table": r2}])
        gaps = {g["device"]: g["missing"] for g in out["convergence_gaps"]}
        assert gaps["R1"] == ["10.9.0.0/16"]
        assert gaps["R2"] == ["192.168.1.0/24"], "an L /32 is never a gap"
        assert out["summary"].startswith("⚠")


class TestOspfNormalization:
    @pytest.mark.parametrize("raw,name", [(8, "FULL"), (4, "2WAY"), (3, "INIT"), ("8", "FULL")])
    def test_state_names(self, raw, name):
        assert ospf_state_name(raw) == name

    def test_unknown_state_returns_the_number(self):
        assert ospf_state_name(42) == 42

    def _dev(self, name, rid, neighbors):
        return {"name": name, "processes": [{"process_id": 1, "router_id": rid, "areas": [
            {"area_id": "0.0.0.0", "adjacent": len(neighbors),
             "neighbors": [{"local_port": "GigabitEthernet0/1", "neighbor": n} for n in neighbors]}]}]}

    def test_converged(self):
        out = ospf_report([self._dev("R1", "1.1.1.1", [FULL_NEIGHBOR])])
        nb = out["devices"][0]["processes"][0]["areas"][0]["neighbors"][0]
        assert (nb["state"], nb["state_code"], nb["role"]) == ("FULL", 8, "DR")
        assert out["summary"].startswith("✅")

    def test_stuck_neighbor_duplicate_rid_and_lonely_router(self):
        init = dict(FULL_NEIGHBOR, state=3)
        out = ospf_report([self._dev("R1", "1.1.1.1", [init]),
                           self._dev("R2", "1.1.1.1", []),
                           {"name": "SW1", "processes": []}])
        assert out["not_full"] == [{"device": "R1", "neighbor": "2.2.2.2",
                                    "port": "GigabitEthernet0/1", "state": "INIT"}]
        assert out["duplicate_router_ids"] == {"1.1.1.1": ["R1", "R2"]}
        assert out["without_neighbors"] == ["R2"]
        assert out["without_ospf"] == ["SW1"]
        assert out["summary"].startswith("⚠")

    def test_two_way_between_drothers_is_normal(self):
        drother = dict(FULL_NEIGHBOR, state=4, address="10.0.0.3", routerId="3.3.3.3")
        assert ospf_report([self._dev("R1", "1.1.1.1", [drother])])["not_full"] == []

    def test_point_to_point_has_no_role(self):
        p2p = dict(FULL_NEIGHBOR, dr="0.0.0.0", backupDr="0.0.0.0")
        nb = ospf_report([self._dev("R1", "1.1.1.1", [p2p])])["devices"][0]
        assert nb["processes"][0]["areas"][0]["neighbors"][0]["role"] is None


class TestOtherNormalization:
    def test_arp(self):
        out = arp_report([{"name": "R1", "table": {"entries": [
            {"ipAddress": "192.168.1.10", "macAddress": "0001.4321.ABCD",
             "portName": "GigabitEthernet0/0"}]}}])
        assert out["devices"][0]["entries"] == [
            {"ip": "192.168.1.10", "mac": "0001.4321.ABCD", "port": "GigabitEthernet0/0"}]

    def test_cdp_fields_and_unknown_struct(self):
        nb = {"capability": "R", "cdpVersion": 2, "deviceId": "R2", "holdTime": 170,
              "localPort": "GigabitEthernet0/1", "nativeVlan": 0, "platform": "2911",
              "remotePort": "GigabitEthernet0/1", "softwareVersion": "15.1"}
        out = cdp_report([{"name": "R1", "neighbors": [nb, {"weird": 1}]}])
        first, second = out["devices"][0]["neighbors"]
        assert (first["device_id"], first["local_port"], first["remote_port"]) == (
            "R2", "GigabitEthernet0/1", "GigabitEthernet0/1")
        assert second == {"raw": {"weird": 1}}

    def test_empty_cdp_explains_the_timer(self):
        assert "60 s" in cdp_report([{"name": "R1", "neighbors": []}])["summary"]

    def test_bridge_id(self):
        assert split_bridge_id("32769 / 0001.63BE.8468") == (32769, "0001.63BE.8468")
        assert split_bridge_id(None) == (None, None)

    def test_stp_disagreement(self):
        sw = lambda n, root, rid: {"name": n, "instances": [{"vlan": 1, "is_root": root, "root_id": rid}]}
        ok = stp_report([sw("SW1", True, "32769 / 0001.63BE.8468"),
                         sw("SW2", False, "32769 / 0001.63BE.8468")])
        assert ok["summary"].startswith("✅") and "VLAN 1 root SW1" in ok["summary"]
        bad = stp_report([sw("SW1", True, "32769 / 0001.63BE.8468"),
                          sw("SW2", True, "32769 / 0002.0000.0001")])
        assert bad["root_disagreements"] and bad["summary"].startswith("⚠ Not converged")

    def test_acl_bound_but_undefined(self):
        out = acl_report([{"name": "R1", "acls": [{"id": "BLOCK", "extended": True, "commands": []}],
                           "bindings": [{"port": "Gi0/1", "acl_in": "", "acl_out": "GHOST"}]}])
        assert out["undefined_acls"] == [{"device": "R1", "port": "Gi0/1", "direction": "out",
                                          "acl": "GHOST"}]
        assert out["devices"][0]["acls"][0]["type"] == "extended"

    def test_nat_needs_inside_and_outside(self):
        out = nat_report([{"name": "R1", "table": {"entries": []}, "counters": {"inside_source_lists": 1},
                           "ports": [{"port": "Gi0/0", "nat_mode_raw": "1"},
                                     {"port": "Gi0/2", "nat_mode_raw": "0"}]}])
        assert out["devices"][0]["ports"] == [{"port": "Gi0/0", "nat_mode": "inside"}]
        assert out["summary"].startswith("⚠ R1: NAT needs both")

    def test_nat_entries_pass_through(self):
        entry = {"insideLocal": "192.168.1.10", "insideGlobal": "200.0.0.1"}
        out = nat_report([{"name": "R1", "table": {"entries": [entry]}, "ports": []}])
        assert out["devices"][0]["translations"] == [entry]

    def test_mac_table(self):
        out = mac_table_report([{"name": "SW1", "vlans": [
            {"number": 1, "name": "default", "table": {"entries": [
                {"isDynamic": True, "macAddress": "0001.4321.ABCD", "port": "FastEthernet0/1"}]}},
            {"number": 10, "name": "SALES", "table": {"entries": []}}]}])
        assert out["devices"][0]["macs"] == 1
        assert out["devices"][0]["vlans"][0]["entries"][0]["dynamic"] is True


class TestSelectDevices:
    def test_missing_device(self):
        assert "does not exist" in select_devices({"found": False}, "R9", "OSPF")

    def test_named_device_without_the_process(self):
        data = {"found": True, "devices": [{"name": "PC1", "supported": False}]}
        assert "does not expose OSPF" in select_devices(data, "PC1", "OSPF")

    def test_sweep_skips_unsupported(self):
        data = {"found": True, "devices": [{"name": "PC1", "supported": False},
                                           {"name": "R1", "supported": True}]}
        assert [d["name"] for d in select_devices(data, "", "OSPF")] == ["R1"]


# --- the generated JS, run against the fake PT -----------------------------------------

class TestGeneratedJs:
    def test_every_reader_is_one_line_without_comments(self):
        for js in (state_js.routes_js("R1"), state_js.ospf_js(), state_js.arp_js(),
                   state_js.cdp_js(), state_js.stp_js(vlans=[1]), state_js.acl_js(),
                   state_js.nat_js(), state_js.mac_table_js(vlan=10), state_js.vlans_js("SW1")):
            assert "\n" not in js and "//" not in js
            assert js.startswith("(function(){try{") and js.endswith("})();")

    def test_process_names_keep_the_process_suffix(self):
        """getProcess("Ospf"/"Arp"/"Cdp"/"Nat"/"Stp") throws "invalid string position"."""
        src = "".join([state_js._ROUTES_JS, state_js._OSPF_JS, state_js._ARP_JS, state_js._CDP_JS,
                       state_js._STP_JS, state_js._NAT_JS])
        for bad in ("'Ospf'", "'Arp'", "'Cdp'", "'Nat'", "'Stp'"):
            assert bad not in src
        assert "for(var k in" not in src and "for (var k in" not in src

    def test_device_name_is_data_not_code(self):
        js = state_js.routes_js('R1"});reportResult("pwned\n..')
        assert json.dumps('R1"});reportResult("pwned\n..') in js

    @requires_node
    def test_injected_name_is_just_a_missing_device(self, tmp_path):
        data = _run(state_js.routes_js('R1"});reportResult("pwned\n..'), tmp_path)
        assert data == {"found": False}

    @requires_node
    def test_routes_single_device(self, tmp_path):
        data = _run(state_js.routes_js("R1"), tmp_path)
        out = routes_report(_devices(data, "R1"))
        assert out["devices"][0]["by_protocol"] == {"C": 1, "L": 1, "O": 1}

    @requires_node
    def test_routes_sweep_skips_devices_without_the_process(self, tmp_path):
        data = _run(state_js.routes_js(), tmp_path)
        assert [d["name"] for d in _devices(data)] == ["R1"]

    @requires_node
    def test_ospf_reads_neighbors_per_port(self, tmp_path):
        out = ospf_report(_devices(_run(state_js.ospf_js("R1"), tmp_path), "R1"))
        proc = out["devices"][0]["processes"][0]
        assert (proc["process_id"], proc["router_id"]) == (1, "1.1.1.1")
        area = proc["areas"][0]
        assert area["area_id"] == "0.0.0.0" and area["adjacent"] == 1
        assert area["neighbors"][0]["state"] == "FULL"
        assert area["neighbors"][0]["local_port"] == "GigabitEthernet0/1"

    @requires_node
    def test_ospf_stuck_neighbor(self, tmp_path):
        data = _run(state_js.ospf_js("R1"), tmp_path, neighbor=dict(FULL_NEIGHBOR, state=6))
        assert ospf_report(_devices(data, "R1"))["not_full"][0]["state"] == "EXCHANGE"

    @requires_node
    def test_arp_sweep_includes_hosts(self, tmp_path):
        out = arp_report(_devices(_run(state_js.arp_js(), tmp_path)))
        assert [(d["device"], len(d["entries"])) for d in out["devices"]] == [("R1", 1), ("PC1", 0)]

    @requires_node
    def test_cdp_empty_table(self, tmp_path):
        out = cdp_report(_devices(_run(state_js.cdp_js("R1"), tmp_path), "R1"))
        assert out["devices"][0]["neighbors"] == []

    @requires_node
    def test_stp_reads_vlans_from_the_database(self, tmp_path):
        """VLANs 1 and 10 from VlanManager; 1002-1005 are skipped."""
        out = stp_report(_devices(_run(state_js.stp_js(), tmp_path)))
        assert [d["device"] for d in out["devices"]] == ["SW1", "SW2"]
        assert [i["vlan"] for i in out["devices"][0]["instances"]] == [1, 10]
        assert out["devices"][1]["instances"][0]["root_mac"] == "0001.63BE.8468"
        assert out["summary"].startswith("✅")

    @requires_node
    def test_stp_disagreement_across_switches(self, tmp_path):
        data = _run(state_js.stp_js(vlans=[1]), tmp_path, sw2_root="4097 / 00D0.0000.0001")
        out = stp_report(_devices(data))
        assert out["root_disagreements"] == {1: ["32769 / 0001.63BE.8468", "4097 / 00D0.0000.0001"]}

    @requires_node
    def test_acl_commands_and_bindings(self, tmp_path):
        out = acl_report(_devices(_run(state_js.acl_js("R1"), tmp_path), "R1"))
        acl = out["devices"][0]["acls"][0]
        assert acl == {"id": "BLOCK", "type": "extended",
                       "commands": ["deny ip any host 8.8.8.8", "permit ip any any"]}
        assert out["undefined_acls"][0]["acl"] == "GHOST"

    @requires_node
    def test_nat_modes_from_strings(self, tmp_path):
        out = nat_report(_devices(_run(state_js.nat_js("R1"), tmp_path), "R1"))
        dev = out["devices"][0]
        assert dev["ports"] == [{"port": "GigabitEthernet0/0", "nat_mode": "inside"},
                                {"port": "GigabitEthernet0/1", "nat_mode": "outside"}]
        assert dev["counters"] == {"inside_source_lists": 1}

    @requires_node
    def test_mac_table_filtered_by_vlan(self, tmp_path):
        out = mac_table_report(_devices(_run(state_js.mac_table_js("SW1", 1), tmp_path), "SW1"))
        assert out["devices"][0]["vlans"][0]["entries"][0]["port"] == "FastEthernet0/1"
        empty = mac_table_report(_devices(_run(state_js.mac_table_js("SW1", 10), tmp_path), "SW1"))
        assert empty["devices"][0]["macs"] == 0

    @requires_node
    def test_vlans_with_port_membership(self, tmp_path):
        out = vlans_report(_devices(_run(state_js.vlans_js("SW1"), tmp_path), "SW1")[0])
        by_num = {v["number"]: v for v in out["vlans"]}
        assert by_num[10]["access_ports"] == ["FastEthernet0/1"]
        assert by_num[1]["access_ports"] == []
        assert out["trunk_ports"] == [{"port": "GigabitEthernet0/1", "native_vlan": 99}]
        assert out["port_security"] == [{"port": "FastEthernet0/1", "enabled": True,
                                         "max_mac": 2, "violations": 0}]
        assert out["ports_in_missing_vlan"] == [{"port": "FastEthernet0/2", "vlan": 30}]
        assert out["max_vlans"] == 255 and out["summary"].startswith("⚠")

    @requires_node
    def test_router_is_not_a_vlan_switch(self, tmp_path):
        data = _run(state_js.vlans_js("R1"), tmp_path)
        assert "does not expose VlanManager" in select_devices(data, "R1", "VlanManager")


class TestRegistration:
    def test_tools_are_registered(self):
        src = registry_source()
        for tool in ("pt_read_routes", "pt_read_ospf", "pt_read_arp", "pt_read_cdp",
                     "pt_read_stp", "pt_read_acl", "pt_read_nat", "pt_read_mac_table"):
            assert f"def {tool}(" in src
