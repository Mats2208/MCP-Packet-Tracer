"""Devices and links are created through one path: the extension's lwAddDevice/lwAddLink.

pt_add_device and pt_add_link called core.js's addDevice/addLink while the deploy
script used lwAddDevice/lwAddLink, so the same model could be created by one tool
and fail in the other (lw.addDevice needs PT's numeric type; core's addDevice
resolves by model name and has no fallback). Both now go through the same
generator functions as the deploy script.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.packet_tracer_mcp.adapters.mcp.tools import topology
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    add_device_call, add_link_call, generate_ptbuilder_script,
)
from src.packet_tracer_mcp.domain.models.plans import DevicePlan, LinkPlan, TopologyPlan
from tests._fake_ctx import FakeCtx, tools


def test_add_device_uses_the_deploy_call():
    ctx = FakeCtx(replies=["OK:L1|Laptop-PT"])
    tools(topology, ctx)["pt_add_device"](name="L1", model="Laptop-PT", x=10, y=20)
    js = ctx.sent[0]
    assert add_device_call("L1", "Laptop-PT", "laptop", 10, 20) in js
    assert not re.search(r"(?<![.\w])addDevice\(", js)
    assert "skipBoot()" in js  # core.js's addDevice skipped the IOS boot; keep that


def test_add_link_uses_the_deploy_call():
    ctx = FakeCtx(replies=["PRE_OK:2911|2960-24TT", "OK:link created"])
    out = tools(topology, ctx)["pt_add_link"](device1="R1", port1="GigabitEthernet0/0",
                                              device2="SW1", port2="GigabitEthernet0/1",
                                              cable_type="straight")
    assert out.startswith("Link created")
    js = ctx.sent[1]
    assert add_link_call("R1", "GigabitEthernet0/0", "SW1", "GigabitEthernet0/1", "straight") in js
    assert "8100" in js and not re.search(r"(?<![.\w])addLink\(", js)


def test_deploy_script_uses_the_same_calls():
    plan = TopologyPlan(name="t", devices=[
        DevicePlan(name="R1", model="2911", category="router", x=1, y=2),
        DevicePlan(name="SW1", model="2960-24TT", category="switch", x=3, y=4),
    ], links=[LinkPlan(device_a="R1", port_a="GigabitEthernet0/0", device_b="SW1",
                       port_b="GigabitEthernet0/1", cable="straight")])
    script = generate_ptbuilder_script(plan)
    assert add_device_call("R1", "2911", "router", 1, 2) + ";" in script
    assert add_link_call("R1", "GigabitEthernet0/0", "SW1", "GigabitEthernet0/1",
                         "straight") + ";" in script


def test_no_python_module_calls_core_add_device_or_link():
    hits = []
    for path in Path("src/packet_tracer_mcp").rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(?<![.\w])add(Device|Link)\(", line) and "pt_send_raw" not in line \
                    and "_LINK_CALLS" not in line and not line.lstrip().startswith("#"):
                hits.append(f"{path}:{n}: {line.strip()}")
    assert hits == []
