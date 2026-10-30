"""Canvas edits that read themselves back, and the real cable type of each link.

Facts measured live on PT 9 (2026-10-30), which the fake PT below copies:
- Link.getClassName() -> "Cable"; Link.getConnectionType() -> "8100" (straight)
  or "8101" (cross), a string.
- Device.moveToLocation(x, y) -> true, and getXCoordinate()/getYCoordinate()
  then read back exactly (x, y).
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.adapters.mcp.tools import inspect as inspect_tools
from src.packet_tracer_mcp.adapters.mcp.tools import topology
from src.packet_tracer_mcp.domain.services.port_inspect import summarize_ports
from src.packet_tracer_mcp.domain.services.topology_view import (
    format_export, format_query, link_line,
)
from src.packet_tracer_mcp.infrastructure.catalog.cables import cable_type_name
from src.packet_tracer_mcp.infrastructure.generator.topology_js import (
    PORT_LINK_JS, delete_link_js, move_device_js, rename_device_js, topology_snapshot_js,
)
from tests._fake_ctx import FakeCtx, tools
from tests._pt_js import requires_node, run_pt_js

# A fake PT: R1 Gi0/0 <-straight-> SW1 Fa0/1, R1 Gi0/1 <-cross-> R2 Gi0/0.
# `__stuck` makes moveToLocation / setName / deleteLink silently do nothing,
# to prove the read-back catches it.
FAKE_PT = r"""
var __stuck = %(stuck)s;
var __devs = {};
function __dev(name, model, ports){
  var d = {__n:name, __x:100, __y:100, __ports:{}, __order:[]};
  d.getName=function(){return d.__n;}; d.getModel=function(){return model;};
  d.setName=function(n){ if(__stuck){return;} delete __devs[d.__n]; d.__n=n; __devs[n]=d; };
  d.moveToLocation=function(x,y){ if(!__stuck){d.__x=x;d.__y=y;} return true; };
  d.getXCoordinate=function(){return d.__x;}; d.getYCoordinate=function(){return d.__y;};
  for(var i=0;i<ports.length;i++){(function(pn){
    var p={__link:null};
    p.getName=function(){return pn;}; p.getOwnerDevice=function(){return d;};
    p.getLink=function(){return p.__link;}; p.getIpAddress=function(){return '0.0.0.0';};
    p.getSubnetMask=function(){return '0.0.0.0';};
    p.deleteLink=function(){ if(__stuck||!p.__link){return;} var l=p.__link;
      l.__a.__link=null; l.__b.__link=null; __links.splice(__links.indexOf(l),1); };
    d.__ports[pn]=p; d.__order.push(pn);})(ports[i]);}
  d.getPort=function(n){return d.__ports[n]||null;};
  d.getPortCount=function(){return d.__order.length;};
  d.getPortAt=function(i){return d.__ports[d.__order[i]];};
  __devs[name]=d; return d;
}
var __links = [];
function __cable(a, b, enumStr){
  var l={__a:a,__b:b};
  l.getClassName=function(){return 'Cable';};
  l.getConnectionType=function(){return enumStr;};
  l.getPort1=function(){return a;}; l.getPort2=function(){return b;};
  a.__link=l; b.__link=l; __links.push(l);
}
var R1=__dev('R1','2911',['GigabitEthernet0/0','GigabitEthernet0/1']);
var SW1=__dev('SW1','2960-24TT',['FastEthernet0/1']);
var R2=__dev('R2','2911',['GigabitEthernet0/0']);
R1.__ports['GigabitEthernet0/0'].getIpAddress=function(){return '10.0.0.1';};
R1.__ports['GigabitEthernet0/0'].getSubnetMask=function(){return '255.255.255.0';};
__cable(R1.getPort('GigabitEthernet0/0'), SW1.getPort('FastEthernet0/1'), '8100');
__cable(R2.getPort('GigabitEthernet0/0'), R1.getPort('GigabitEthernet0/1'), '8101');
var ipc = {network:function(){return {
  getDevice:function(n){return __devs[n]||null;},
  getDeviceCount:function(){return Object.keys(__devs).length;},
  getDeviceAt:function(i){return __devs[Object.keys(__devs)[i]];},
  getLinkCount:function(){return __links.length;},
  getLinkAt:function(i){return __links[i];}
};}};
"""


def _run(js: str, tmp_path, stuck: bool = False) -> dict:
    reports = run_pt_js(js, FAKE_PT % {"stuck": "true" if stuck else "false"}, tmp_path)
    assert len(reports) == 1, reports
    return json.loads(reports[0])


# --- cable enum -> name ---------------------------------------------------------------

@pytest.mark.parametrize("raw,name", [("8100", "straight"), ("8101", "cross"), (8106, "serial"),
                                      ("8103", "fiber"), ("8108", "console")])
def test_cable_enum_is_mapped(raw, name):
    assert cable_type_name(raw) == name


def test_unknown_or_missing_cable_is_not_guessed():
    assert cable_type_name("9999") == "unknown:9999"
    assert cable_type_name("") is None
    assert cable_type_name(None) is None


# --- generated JS: one config object, no hand interpolation --------------------------

@pytest.mark.parametrize("make", [
    lambda n: move_device_js(n, 1, 2), lambda n: rename_device_js(n, n + "x"),
    lambda n: delete_link_js(n, n),
])
def test_names_travel_only_through_json(make):
    evil = 'R1"); reportResult("pwned'
    js = make(evil)
    assert json.dumps(evil) in js
    assert 'reportResult("pwned' not in js.replace(json.dumps(evil), "")


def test_snapshot_reads_the_cable_from_the_link():
    js = topology_snapshot_js()
    assert "l.getConnectionType()" in js and "l.getClassName()" in js


# --- run under Node against the fake PT ----------------------------------------------

@requires_node
class TestRunsAgainstFakePt:
    def test_snapshot_reports_each_link_with_its_cable(self, tmp_path):
        data = _run(topology_snapshot_js(), tmp_path)
        assert data["link_count"] == 2
        lines = [link_line(l) for l in data["links"]]
        assert "R1:GigabitEthernet0/0  <--[straight]-->  SW1:FastEthernet0/1" in lines
        assert "R2:GigabitEthernet0/0  <--[cross]-->  R1:GigabitEthernet0/1" in lines
        r1 = next(d for d in data["devices"] if d["name"] == "R1")
        assert r1["ports"][0] == {"name": "GigabitEthernet0/0", "ip": "10.0.0.1",
                                  "mask": "255.255.255.0", "linked": True}

    def test_move_is_verified_by_reading_back(self, tmp_path):
        data = _run(move_device_js("R1", 420, 310), tmp_path)
        assert data == {"ok": True, "returned": True, "x": 420, "y": 310, "verified": True}

    def test_move_that_did_not_happen_is_not_verified(self, tmp_path):
        data = _run(move_device_js("R1", 420, 310), tmp_path, stuck=True)
        assert data["verified"] is False and (data["x"], data["y"]) == (100, 100)

    def test_rename_is_verified(self, tmp_path):
        data = _run(rename_device_js("SW1", "SW-Core"), tmp_path)
        assert data["verified"] is True and data["name"] == "SW-Core"
        assert data["old_absent"] is True

    def test_rename_that_did_not_happen_is_not_verified(self, tmp_path):
        data = _run(rename_device_js("SW1", "SW-Core"), tmp_path, stuck=True)
        assert data["verified"] is False and data["new_present"] is False

    def test_rename_to_a_taken_name_is_refused_before_setName(self, tmp_path):
        assert _run(rename_device_js("SW1", "R1"), tmp_path) == {"ok": False, "error": "duplicate"}

    def test_delete_link_checks_both_ends(self, tmp_path):
        data = _run(delete_link_js("R1", "GigabitEthernet0/1"), tmp_path)
        assert data["peer"] == {"device": "R2", "port": "GigabitEthernet0/0"}
        assert data["cable_raw"] == "8101"
        assert data["local_free"] is True and data["peer_free"] is True
        assert data["verified"] is True

    def test_delete_link_that_stayed_is_not_verified(self, tmp_path):
        data = _run(delete_link_js("R1", "GigabitEthernet0/0"), tmp_path, stuck=True)
        assert data["verified"] is False and data["local_free"] is False

    def test_delete_link_on_a_free_port(self, tmp_path):
        _run(delete_link_js("R1", "GigabitEthernet0/1"), tmp_path)
        assert _run(delete_link_js("SW1", "FastEthernet0/1"), tmp_path)["ok"] is True

    def test_port_link_helper_finds_the_far_end(self, tmp_path):
        js = ("(function(){" + PORT_LINK_JS +
              "var d=ipc.network().getDevice('R1');"
              "reportResult(JSON.stringify([__plink('R1',d.getPort('GigabitEthernet0/1')),"
              "__plink('R1',d.getPort('GigabitEthernet0/0'))]));})();")
        first, second = json.loads(run_pt_js(js, FAKE_PT % {"stuck": "false"}, tmp_path)[0])
        assert first == {"cable_raw": "8101", "peer_device": "R2", "peer_port": "GigabitEthernet0/0"}
        assert second["peer_device"] == "SW1" and second["cable_raw"] == "8100"


# --- formatters ------------------------------------------------------------------------

SNAPSHOT = {
    "ok": True, "device_count": 2, "link_count": 2,
    "devices": [{"name": "R1", "model": "2911", "x": 100, "y": 200, "ports": [
        {"name": "GigabitEthernet0/0", "ip": "10.0.0.1", "mask": "255.255.255.0", "linked": True},
        {"name": "GigabitEthernet0/1", "ip": "", "mask": "", "linked": False}]},
        {"name": "AP1", "model": "AccessPoint-PT", "x": 1, "y": 2, "ports": []}],
    "links": [
        {"cls": "Cable", "cable_raw": "8100", "a": {"device": "R1", "port": "GigabitEthernet0/0"},
         "b": {"device": "SW1", "port": "FastEthernet0/1"}},
        {"cls": "Antenna", "cable_raw": "", "wireless": True,
         "a": {"device": "AP1", "port": "Port 1"}},
    ],
}


def test_export_lists_the_cable_and_the_wireless_signal():
    text = format_export(SNAPSHOT)
    assert "R1 [2911] @ (100, 200)" in text
    assert "GigabitEthernet0/0 IP=10.0.0.1/255.255.255.0 [linked]" in text
    assert "R1:GigabitEthernet0/0  <--[straight]-->  SW1:FastEthernet0/1" in text
    assert "AP1:Port 1  )))  [wireless signal]" in text


def test_query_lists_links_after_devices():
    text = format_query(SNAPSHOT)
    assert text.startswith("DEVICES:2|LINKS:2")
    assert "GigabitEthernet0/0=10.0.0.1/255.255.255.0" in text
    assert text.index("Links:") > text.index("R1")


# --- the tools, driven with scripted replies --------------------------------------------

def _tool(name, *replies):
    return tools(topology, FakeCtx(replies=list(replies)))[name]


def test_move_tool_reports_verified():
    reply = json.dumps({"ok": True, "returned": True, "x": 420, "y": 310, "verified": True})
    out = json.loads(_tool("pt_move_device", reply)("R1", 420, 310))
    assert out["verified"] is True and "verified" in out["summary"]


def test_move_tool_warns_when_position_differs():
    reply = json.dumps({"ok": True, "returned": True, "x": 100, "y": 100, "verified": False})
    out = json.loads(_tool("pt_move_device", reply)("R1", 420, 310))
    assert out["verified"] is False and "(100, 100)" in out["summary"]


def test_delete_link_tool_names_the_cable_and_far_end():
    reply = json.dumps({"ok": True, "peer": {"device": "R2", "port": "GigabitEthernet0/0"},
                        "cable_raw": "8101", "local_free": True, "peer_free": True,
                        "verified": True})
    out = json.loads(_tool("pt_delete_link", reply)("R1", "GigabitEthernet0/1"))
    assert out["cable"] == "cross" and "R2/GigabitEthernet0/0" in out["summary"]


def test_delete_link_tool_maps_reader_errors():
    reply = json.dumps({"ok": False, "error": "no_link"})
    assert _tool("pt_delete_link", reply)("R1", "Gi0/2") == "Error: No link on this interface"


def test_rename_tool_explains_a_duplicate():
    reply = json.dumps({"ok": False, "error": "duplicate"})
    assert "a device named 'R1' already exists" in _tool("pt_rename_device", reply)("SW1", "R1")


def test_rename_tool_reports_verified():
    reply = json.dumps({"ok": True, "new_present": True, "old_absent": True, "name": "SW2",
                        "verified": True})
    assert json.loads(_tool("pt_rename_device", reply)("SW1", "SW2"))["verified"] is True


def test_export_tool_formats_the_snapshot():
    out = _tool("pt_export_topology", json.dumps(SNAPSHOT))()
    assert "<--[straight]-->" in out


def test_inspect_ports_reports_cable_and_peer():
    reply = json.dumps({"devices": [{"name": "R1", "model": "2911", "ports": [
        {"name": "GigabitEthernet0/0", "up": False, "protocol_up": False, "linked": True,
         "nat_mode_raw": "0", "link": {"cable_raw": "8101", "peer_device": "SW1",
                                       "peer_port": "FastEthernet0/1"}},
        {"name": "GigabitEthernet0/1", "up": False, "linked": False, "link": None}]}]})
    fn = tools(inspect_tools, FakeCtx(replies=[reply]))["pt_inspect_ports"]
    out = json.loads(fn("R1"))
    p0, p1 = out["devices"][0]["ports"]
    assert p0["cable"] == "cross" and p0["peer"] == "SW1:FastEthernet0/1"
    assert "cable" not in p1
    assert "Cable: cross to SW1:FastEthernet0/1" in out["anomalies"][0]["detail"]


def test_inspect_ports_js_reads_the_link():
    reply = json.dumps({"devices": []})
    ctx = FakeCtx(replies=[reply])
    tools(inspect_tools, ctx)["pt_inspect_ports"]("R1")
    assert "link: __plink(String(__dn), __p)" in ctx.sent[0]


def test_summarize_ports_without_cable_keeps_the_old_detail():
    res = summarize_ports([{"name": "R1", "ports": [{"name": "g", "up": False, "linked": True}]}])
    assert res["anomalies"][0]["detail"].endswith("(shutdown, or the wrong cable?).")
