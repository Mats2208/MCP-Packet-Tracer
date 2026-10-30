"""The network -> device -> port walk lives in walk_js.WALK_JS, not in copies.

Seven readers each had their own getDeviceCount/getDeviceAt loop (and five of
them a getPortCount/getPortAt one), with different ideas of what could throw:
LIVE_DEVICES_JS and the canvas name list had no guard at all, so one device whose
getters threw lost the whole read. They now share one walk that skips the device
or port that fails and keeps the rest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.packet_tracer_mcp.adapters.mcp.bridge_context import LIVE_DEVICES_JS
from src.packet_tracer_mcp.adapters.mcp.tools import live
from src.packet_tracer_mcp.infrastructure.generator.topology_js import topology_snapshot_js
from tests._pt_js import requires_node, run_pt_js

# R1 (two ports), a device whose getName throws, and SW1 whose second port throws.
FAKE_PT = """
function port(name, ip){ return {
  getName: function(){ return name; }, getIpAddress: function(){ return ip; },
  getSubnetMask: function(){ return '255.255.255.0'; }, isPortUp: function(){ return true; },
  getLink: function(){ return null; } }; }
function dev(name, model, ports){ return {
  getName: function(){ if (name === null) { throw 'broken'; } return name; },
  getModel: function(){ return model; },
  getXCoordinate: function(){ return 1; }, getYCoordinate: function(){ return 2; },
  getPortCount: function(){ return ports.length; },
  getPortAt: function(j){ if (ports[j] === null) { throw 'bad port'; } return ports[j]; } }; }
var __devs = [
  dev('R1', '2911', [port('Gi0/0', '10.0.0.1'), port('Gi0/1', '')]),
  dev(null, 'X', []),
  dev('SW1', '2960', [port('Fa0/1', ''), null]),
];
var ipc = { network: function(){ return {
  getDeviceCount: function(){ return __devs.length; },
  getDeviceAt: function(i){ return __devs[i]; },
  getLinkCount: function(){ return 0; }, getLinkAt: function(){ return null; }
}; } };
"""


@requires_node
def test_live_devices_survives_a_broken_device_and_port(tmp_path):
    data = json.loads(run_pt_js(LIVE_DEVICES_JS, FAKE_PT, tmp_path)[-1])
    assert [d["name"] for d in data["devices"]] == ["R1", "SW1"]
    assert [p["name"] for p in data["devices"][0]["ports"]] == ["Gi0/0", "Gi0/1"]
    assert data["devices"][0]["ports"][0] == {"name": "Gi0/0", "ip": "10.0.0.1",
                                              "mask": "255.255.255.0", "up": True,
                                              "linked": False}
    assert [p["name"] for p in data["devices"][1]["ports"]] == ["Fa0/1"]


@requires_node
def test_canvas_names_survive_a_broken_device(tmp_path):
    assert json.loads(run_pt_js(live._LIVE_NAMES_JS, FAKE_PT, tmp_path)[-1]) == ["R1", "SW1"]


@requires_node
def test_snapshot_keeps_its_contract(tmp_path):
    data = json.loads(run_pt_js(topology_snapshot_js(), FAKE_PT, tmp_path)[-1])
    assert data["ok"] is True and data["device_count"] == 3
    assert data["devices"][0] == {"name": "R1", "model": "2911", "x": 1, "y": 2, "ports": [
        {"name": "Gi0/0", "ip": "10.0.0.1", "mask": "255.255.255.0", "linked": False},
        {"name": "Gi0/1", "ip": "", "mask": "", "linked": False}]}


def test_the_walk_is_written_once():
    loops = []
    for path in Path("src/packet_tracer_mcp").rglob("*.py"):
        if path.name == "walk_js.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"getDeviceAt\(|getPortAt\(", text):
            loops.append(str(path))
    assert loops == []
