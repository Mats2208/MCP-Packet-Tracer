"""Hostile device names run through the generated JS under Node: they stay data.

Five configureIosDevice builders (ACL, NAT, hardening, switch security, VLAN)
escaped by hand: `\\`, `"` and `\\n` only, so a `\\r` or U+2028 in a device name or
payload ended the JS string literal and the command died in a SyntaxError (or ran
whatever followed). The live topology tools used a `js_escape` helper instead of
json.dumps. Every value now reaches JS through json.dumps; these tests execute the
generated code against a fake PT and check that PT receives exactly the name sent
and that nothing else ran.
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.adapters.mcp.tools import topology
from src.packet_tracer_mcp.application.use_cases.apply_acl import _build_js_call
from src.packet_tracer_mcp.infrastructure.generator.hardening_cli_generator import (
    build_hardening_js_call,
)
from src.packet_tracer_mcp.infrastructure.generator.nat_cli_generator import build_nat_js_call
from src.packet_tracer_mcp.infrastructure.generator.switch_security_cli_generator import (
    build_switch_js_call,
)
from src.packet_tracer_mcp.infrastructure.generator.vlan_cli_generator import build_vlan_js_call
from tests._fake_ctx import FakeCtx, tools
from tests._pt_js import requires_node, run_pt_js

HOSTILE = [
    'R"1',
    "R'1",
    "R\\1",
    "R</script>1",
    "R\n1",
    "R\r1",
    "R 1",
    "R 1",
    "R);alert(1);//",
    "\");alert(1);//",
    "');alert(1);//",
]

# Records every name PT is asked about; `alert` marks injected code.
FAKE_PT = """
var __seen = [];
function alert(){ __seen.push('INJECTED'); }
var ipc = {
  network: function(){ return {
    getDevice: function(n){ __seen.push(n); return null; },
    getDeviceCount: function(){ return 0; },
    getDeviceAt: function(){ return null; }
  }; },
  appWindow: function(){ return { getActiveWorkspace: function(){ return {
    getLogicalWorkspace: function(){ return {
      addDevice: function(){ return ''; }, createLink: function(){ return false; },
      removeDevice: function(){}
    }; } }; } }; }
};
function addDevice(n){ __seen.push(n); return false; }
function addLink(a, b, c, d){ __seen.push(a); return false; }
function lwAddDevice(n){ __seen.push(n); return n; }
function lwAddLink(a){ __seen.push(a); return false; }
function skipBootIfIos(){}
function configureIosDevice(n, c){ __seen.push(n); __seen.push(c); return true; }
"""

REPORT_SEEN = "\nreportResult(JSON.stringify(__seen));"


def _seen(js: str, tmp_path) -> list[str]:
    reports = run_pt_js(js + REPORT_SEEN, FAKE_PT, tmp_path)
    return json.loads(reports[-1])


@requires_node
@pytest.mark.parametrize("name", HOSTILE)
@pytest.mark.parametrize("build", [
    _build_js_call, build_nat_js_call, build_hardening_js_call,
    build_switch_js_call, build_vlan_js_call,
])
def test_configure_ios_calls_keep_names_and_payload_as_data(build, name, tmp_path):
    payload = f"enable\nconfigure terminal\ndescription {name}\nend"
    seen = _seen(build(name, payload), tmp_path)
    assert seen == [name, payload]


def _tool_js(tool: str, tmp_path, **kwargs) -> list[str]:
    ctx = FakeCtx(replies=["ERROR:stop"] * 3)
    tools(topology, ctx)[tool](**kwargs)
    assert ctx.sent, f"{tool} sent nothing"
    return ctx.sent


@requires_node
@pytest.mark.parametrize("name", HOSTILE)
def test_delete_device_keeps_the_name_as_data(name, tmp_path):
    js = _tool_js("pt_delete_device", tmp_path, device_name=name)[0]
    assert _seen(js, tmp_path) == [name]


@requires_node
@pytest.mark.parametrize("name", HOSTILE)
def test_add_link_keeps_names_as_data(name, tmp_path):
    js = _tool_js("pt_add_link", tmp_path, device1=name, port1="Gi0/0",
                  device2="SW1", port2=name, cable_type="straight")[0]
    seen = _seen(js, tmp_path)
    assert "INJECTED" not in seen and seen[0] == name


@requires_node
@pytest.mark.parametrize("name", [n for n in HOSTILE if n.isprintable()])
def test_set_port_keeps_names_as_data(name, tmp_path):
    js = _tool_js("pt_set_port", tmp_path, device=name, interface=name, description=name)[0]
    reports = run_pt_js(js + REPORT_SEEN, FAKE_PT, tmp_path)
    assert json.loads(reports[0]) == {"success": False, "error": f"device not found: {name}"}
    assert json.loads(reports[-1]) == [name]


@requires_node
@pytest.mark.parametrize("name", [n for n in HOSTILE if n.isprintable()])
def test_add_device_keeps_the_name_as_data(name, tmp_path):
    # Names with line breaks never reach JS: pt_add_device refuses them first.
    js = _tool_js("pt_add_device", tmp_path, name=name, model="2911")
    seen = _seen(js[0], tmp_path)
    assert "INJECTED" not in seen and name in seen
