"""Reads added from facts measured live on PT 9 (2026-10-30), without PT.

- Host gateway: getProcess("HostIpProcess").getDefaultGateway(); getProcess("HostIp")
  throws, and neither Device nor Port has the getter.
- Router DHCP (2911): getProcess("DhcpServerProcess") -> getPoolCount/getPoolAt ->
  pool.getDhcpPoolName(), pool.getLeaseAt(i) -> {ipAddress, leaseTime, macAddress,
  port}; no lease count: getLeaseAt throws "invalid vector subscript" past the end.
- SYSLOG: getProcess("SyslogServer").getAllEntries() -> Array (entry fields unmeasured).
- QoS: PolicyMapManager lists "?default-policy-map?"; classes have no name getter
  (getClassName() -> "PolicyMapQosClass"); PolicyMap.toString() throws; names come
  from `enterCommand("show running-config", "enable")` -> {first, second}.

The fakes below copy those shapes; the JS is run under Node (tests/_pt_js.py).
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.adapters.mcp.desktop_service_tools import syslog_messages
from src.packet_tracer_mcp.adapters.mcp.tools import convergence, services
from src.packet_tracer_mcp.domain.services.convergence import dhcp_leases_report, select_devices
from src.packet_tracer_mcp.domain.services.qos import (
    name_classes, policy_map_classes, qos_policy_maps,
)
from src.packet_tracer_mcp.infrastructure.generator import state_js
from src.packet_tracer_mcp.infrastructure.generator.host_js import (
    host_ip_config_js, port_state_js, read_device_panel_js,
)
from src.packet_tracer_mcp.infrastructure.generator.service_js import server_service_js
from tests._fake_ctx import FakeCtx, tools
from tests._pt_js import requires_node, run_pt_js


def _one(js, fake, tmp_path) -> dict:
    reports = run_pt_js(js, fake, tmp_path)
    assert len(reports) == 1, reports
    return json.loads(reports[0])


# ---------------------------------------------------------------------------------------
# Host gateway
# ---------------------------------------------------------------------------------------

FAKE_HOST = r"""
var PC1 = {getName:function(){return 'PC1';}, getModel:function(){return 'PC-PT';},
  getType:function(){return 8;}, getPorts:function(){return [];},
  getProcess:function(n){
    if(n==='HostIpProcess'){return {getDefaultGateway:function(){return '192.168.1.1';}};}
    if(n==='HostIp'){throw new Error('invalid string position');}
    return null;}};
var R1 = {getName:function(){return 'R1';}, getModel:function(){return '2911';},
  getType:function(){return 0;}, getPorts:function(){return [];},
  getProcess:function(n){return null;}};
var ipc = {network:function(){return {getDevice:function(n){return {PC1:PC1,R1:R1}[n]||null;}};}};
"""


def test_gateway_is_read_from_host_ip_process_only():
    for js in (read_device_panel_js("PC1"), port_state_js("PC1", "FastEthernet0"),
               host_ip_config_js("PC1")):
        assert "d.getProcess('HostIpProcess')" in js
        assert "getProcess('HostIp')" not in js
        assert "gateway:__gw(d)" in js


@requires_node
def test_panel_reports_the_host_gateway(tmp_path):
    assert _one(read_device_panel_js("PC1"), FAKE_HOST, tmp_path)["gateway"] == "192.168.1.1"


@requires_node
def test_panel_gateway_is_null_without_the_process(tmp_path):
    assert _one(read_device_panel_js("R1"), FAKE_HOST, tmp_path)["gateway"] is None


def _ip_config_tool(tmp_path, gateway_read):
    from tests.test_host_ipv6_static import _tools
    reply = lambda js: json.dumps({
        "ok": True, "port": "FastEthernet0", "applied": ["gateway 10.0.0.1"],
        "gateway": gateway_read, "state": {"ip": "10.0.0.2", "mask": "255.255.255.0"}})
    return _tools(tmp_path, reply)[0]


def test_ip_config_reports_the_gateway_it_reads_back(tmp_path):
    out = json.loads(_ip_config_tool(tmp_path, "10.0.0.1")(device="PC0", gateway="10.0.0.1"))
    assert out["gateway"] == "10.0.0.1" and "notes" not in out


def test_ip_config_warns_when_the_gateway_did_not_stick(tmp_path):
    out = json.loads(_ip_config_tool(tmp_path, "0.0.0.0")(device="PC0", gateway="10.0.0.1"))
    assert any("asked for 10.0.0.1 but the host reads 0.0.0.0" in n for n in out["notes"])


# ---------------------------------------------------------------------------------------
# SYSLOG messages
# ---------------------------------------------------------------------------------------

FAKE_SYSLOG = r"""
function __Opaque(){} __Opaque.prototype.toString=function(){return 'opaque entry';};
var S0 = {getName:function(){return 'S0';},
  getProcess:function(n){ if(n!=='SyslogServer'){return null;}
    return {isEnabled:function(){return true;}, setEnable:function(){},
      getAllEntries:function(){return [{time:'00:01', host:'10.0.0.1', message:'%LINK-5-CHANGED'},
                                       new __Opaque()];}};}};
var ipc = {network:function(){return {getDevice:function(n){return n==='S0'?S0:null;}};}};
"""


@requires_node
def test_syslog_returns_each_entry_as_pt_serializes_it(tmp_path):
    data = _one(server_service_js("S0", "syslog"), FAKE_SYSLOG, tmp_path)
    assert data["entries"] == 2
    assert syslog_messages(data["messages"]) == [
        {"time": "00:01", "host": "10.0.0.1", "message": "%LINK-5-CHANGED"}, "opaque entry"]


def test_syslog_messages_tolerates_garbage():
    assert syslog_messages(None) == []
    assert syslog_messages(['not json', None]) == ["not json", None]


def test_syslog_entries_are_capped_and_guarded():
    js = server_service_js("S0", "syslog")
    assert "Math.max(0,all.length-200)" in js
    assert "try{t=JSON.stringify(all[q]);}catch(e){}" in js


# ---------------------------------------------------------------------------------------
# DHCP leases
# ---------------------------------------------------------------------------------------

LEASE = {"ipAddress": "192.168.1.11", "leaseTime": 86400, "macAddress": "0001.4321.ABCD",
         "port": "GigabitEthernet0/0"}

FAKE_DHCP = r"""
var __lease = %(lease)s;
function __pool(name, leases, api){
  var p = {getDhcpPoolName:function(){return name;}};
  if(api){ p.getLeaseAt=function(i){ if(i>=leases.length){throw new Error('invalid vector subscript');}
    return leases[i]; }; }
  return p;
}
function __dhcp(pools){ return {getPoolCount:function(){return pools.length;},
  getPoolAt:function(i){return pools[i];}}; }
var R1 = {getName:function(){return 'R1';}, getModel:function(){return '2911';},
  getPorts:function(){return ['GigabitEthernet0/0'];},
  getProcess:function(n){
    if(n==='DhcpServerProcess'){return __dhcp([__pool('LAN', [__lease], true),
                                              __pool('EMPTY', [], true)]);}
    if(n==='DhcpServerMain'){return null;}
    return null;}};
var S0 = {getName:function(){return 'S0';}, getModel:function(){return 'Server-PT';},
  getPorts:function(){return ['FastEthernet0'];},
  getProcess:function(n){
    if(n==='DhcpServerProcess'){throw new Error('invalid string position');}
    if(n==='DhcpServerMain'){return {getDhcpServerProcessByPortName:function(p){
      return __dhcp([__pool('serverPool', [], false)]);}};}
    return null;}};
var PC1 = {getName:function(){return 'PC1';}, getModel:function(){return 'PC-PT';},
  getPorts:function(){return ['FastEthernet0'];}, getProcess:function(n){return null;}};
var __all=[R1,S0,PC1];
var ipc = {network:function(){return {
  getDevice:function(n){for(var i=0;i<__all.length;i++){if(__all[i].getName()===n){return __all[i];}}return null;},
  getDeviceCount:function(){return __all.length;}, getDeviceAt:function(i){return __all[i];}};}};
""" % {"lease": json.dumps(LEASE)}


@requires_node
def test_router_leases_are_walked_until_get_lease_at_throws(tmp_path):
    data = _one(state_js.dhcp_leases_js("R1"), FAKE_DHCP, tmp_path)
    rec = data["devices"][0]
    assert rec["source"] == "router"
    assert [p["name"] for p in rec["pools"]] == ["LAN", "EMPTY"]
    assert rec["pools"][0]["leases"] == [LEASE] and rec["pools"][0]["truncated"] is False


@requires_node
def test_server_pool_without_lease_api_is_flagged(tmp_path):
    data = _one(state_js.dhcp_leases_js("S0"), FAKE_DHCP, tmp_path)
    rec = data["devices"][0]
    assert rec["source"] == "server"
    assert rec["pools"] == [{"name": "serverPool", "port": "FastEthernet0", "lease_api": False,
                             "leases": [], "truncated": False}]
    report = dhcp_leases_report(select_devices(data, "S0", "a DHCP server"))
    assert "unknown, not zero" in report["summary"]


@requires_node
def test_sweep_keeps_only_dhcp_servers(tmp_path):
    data = _one(state_js.dhcp_leases_js(""), FAKE_DHCP, tmp_path)
    report = dhcp_leases_report(select_devices(data, "", "a DHCP server"))
    assert [d["device"] for d in report["devices"]] == ["R1", "S0"]
    assert report["devices"][0]["pools"][0]["leases"] == [
        {"ip": "192.168.1.11", "mac": "0001.4321.ABCD", "port": "GigabitEthernet0/0",
         "lease_time": 86400}]
    assert report["summary"].startswith("1 lease(s): R1 1 in 2 pool(s), S0 0 in 1 pool(s)")


def test_lease_walk_is_capped():
    js = state_js.dhcp_leases_js("R1")
    assert f"i<{state_js.DHCP_MAX_LEASES}" in js
    assert "try{l=p.getLeaseAt(i);}catch(e){break;}" in js
    assert "getLeaseCount" not in js


def test_report_dedupes_a_pool_seen_on_two_server_ports():
    dev = {"name": "S0", "source": "server", "pools": [
        {"name": "P", "port": "FastEthernet0", "lease_api": True, "leases": [LEASE]},
        {"name": "P", "port": "FastEthernet1", "lease_api": True, "leases": [LEASE]}]}
    assert dhcp_leases_report([dev])["devices"][0]["leases"] == 1


def test_dhcp_leases_tool_end_to_end():
    reply = json.dumps({"found": True, "devices": [{"name": "R1", "model": "2911",
        "supported": True, "source": "router", "pools": [
            {"name": "LAN", "port": None, "lease_api": True, "truncated": False,
             "leases": [LEASE]}]}]})
    ctx = FakeCtx(replies=[reply])
    out = json.loads(tools(convergence, ctx)["pt_read_dhcp_leases"]("R1"))
    assert out["devices"][0]["pools"][0]["leases"][0]["ip"] == "192.168.1.11"
    assert json.dumps("R1") in ctx.sent[0]


# ---------------------------------------------------------------------------------------
# QoS
# ---------------------------------------------------------------------------------------

RUNNING = """!
hostname R1
!
class-map match-any VOICE
 match protocol rtp
!
policy-map WAN-OUT
 class VOICE
  priority 512
 class VIDEO
  bandwidth 256
 class class-default
  fair-queue
!
interface GigabitEthernet0/1
 service-policy output WAN-OUT
!
end
"""

FAKE_QOS = r"""
function __cls(o){ return {getClassName:function(){return 'PolicyMapQosClass';},
  getPriority:function(){return o.prio;}, isBandwidthConfigured:function(){return o.bw>0;},
  getBandwidth:function(){return o.bw;}, isFairQueueConfigured:function(){return !!o.fq;},
  isClassDefault:function(){return !!o.def;}}; }
function __pm(name, classes){ return {getMapName:function(){return name;},
  getClassCnt:function(){return classes.length;}, getClassAt:function(i){return classes[i];},
  isPriorityConfigured:function(){return true;}, isFairQueueConfigured:function(){return true;},
  toString:function(){throw new Error('PolicyMap.toString must not be called');}}; }
var __pms = [__pm('?default-policy-map?', []),
  __pm('WAN-OUT', [__cls({prio:512,bw:0}), __cls({prio:0,bw:256}), __cls({prio:0,bw:0,fq:1,def:1})])];
var R1 = {getName:function(){return 'R1';},
  getClassMapManager:function(){return {getClassMapCount:function(){return 1;},
    getClassMapAt:function(){return {getMapName:function(){return 'VOICE';},
      getMatchTypeString:function(){return 'match-any';}};}};},
  getPolicyMapManager:function(){return {getPolicyMapCount:function(){return __pms.length;},
    getPolicyMapAt:function(i){return __pms[i];}};},
  enterCommand:function(cmd, mode){ return {first:true, second:%(running)s}; }};
var ipc = {network:function(){return {getDevice:function(n){return n==='R1'?R1:null;}};}};
""" % {"running": json.dumps(RUNNING)}


def test_policy_map_classes_from_running_config():
    assert policy_map_classes(RUNNING) == {"WAN-OUT": ["VOICE", "VIDEO", "class-default"]}


def test_class_default_is_matched_by_flag_not_position():
    classes = name_classes([{"class_default": True}, {"class_default": False}],
                           ["VOICE", "class-default"])
    assert [c["name"] for c in classes] == ["class-default", "VOICE"]


def test_mismatch_between_api_and_cli_is_warned():
    pms, warnings = qos_policy_maps(
        [{"name": "P", "classes": [{"priority_kbps": 0}]}],
        {"first": "", "second": "policy-map P\n class A\n class B\n"})
    assert pms[0]["classes"][0]["name"] == "A"
    assert warnings and "names may not line up" in warnings[0]


def test_unreadable_running_config_leaves_classes_unnamed():
    pms, warnings = qos_policy_maps([{"name": "P", "classes": [{}]}], None)
    assert pms[0]["classes"][0]["name"] is None
    assert warnings == ["The running-config could not be read: class names are unknown."]


def _qos_tool(reply):
    ctx = FakeCtx(replies=[reply])
    return tools(services, ctx)["pt_read_qos"], ctx


def test_qos_js_never_calls_policy_map_to_string():
    fn, ctx = _qos_tool(None)
    fn("R1")
    js = ctx.sent[0]
    assert "toString(true)" not in js and "__p.toString" not in js
    assert "default-policy-map" in js and "enterCommand('show running-config', 'enable')" in js


@requires_node
def test_qos_reads_each_class_and_names_it_from_the_running_config(tmp_path):
    fn, ctx = _qos_tool(None)
    fn("R1")
    raw = run_pt_js(ctx.sent[0], FAKE_QOS, tmp_path)[0]
    assert not raw.startswith("ERROR"), raw
    fn2, _ = _qos_tool(raw)
    out = json.loads(fn2("R1"))
    assert [p["name"] for p in out["policy_maps"]] == ["WAN-OUT"]
    assert out["policy_maps"][0]["classes"] == [
        {"name": "VOICE", "class_default": False, "priority_kbps": 512, "bandwidth_kbps": None,
         "fair_queue": False},
        {"name": "VIDEO", "class_default": False, "priority_kbps": None, "bandwidth_kbps": 256,
         "fair_queue": False},
        {"name": "class-default", "class_default": True, "priority_kbps": None,
         "bandwidth_kbps": None, "fair_queue": True},
    ]
    assert "running_config" not in out and "warnings" not in out
    assert "VOICE priority 512 kbps" in out["summary"]
    assert out["class_maps"][0]["name"] == "VOICE"
