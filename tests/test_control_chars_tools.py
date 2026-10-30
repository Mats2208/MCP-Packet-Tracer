"""Free-text fields with line breaks reached PT without the shared text_rules check.

pt_add_device and pt_rename_device created or renamed a device to "R1\\nR2"; a
name like that cannot be typed back in any later tool and breaks every IOS payload
that mentions it. pt_set_port passed line breaks to the port setters, and the
interface-tuning rules checked their OSPF keys with a private copy that missed
U+2028/U+2029 and did not look at the interface name at all, which is interpolated
into the IOS payload.
"""

from __future__ import annotations

import pytest

from src.packet_tracer_mcp.adapters.mcp.tools import topology
from src.packet_tracer_mcp.domain.models.interface_tuning import InterfaceTuning
from src.packet_tracer_mcp.domain.rules.interface_tuning_rules import validate_interface_tuning
from tests._fake_ctx import FakeCtx, tools

BREAKS = ["R\n1", "R\r1", "R 1", "R 1"]


@pytest.mark.parametrize("name", BREAKS)
def test_add_device_refuses_line_breaks(name):
    ctx = FakeCtx(replies=["OK:x|2911"])
    out = tools(topology, ctx)["pt_add_device"](name=name, model="2911")
    assert out.startswith("ERROR") and "line break" in out
    assert ctx.sent == []


@pytest.mark.parametrize("name", BREAKS)
def test_rename_device_refuses_line_breaks(name):
    ctx = FakeCtx(replies=['{"ok":true,"verified":true}'])
    out = tools(topology, ctx)["pt_rename_device"](old_name="R1", new_name=name)
    assert out.startswith("Error") and "line break" in out
    assert ctx.sent == []


@pytest.mark.parametrize("field", ["device", "interface", "description", "zone_member"])
def test_set_port_refuses_line_breaks(field):
    kwargs = {"device": "R1", "interface": "GigabitEthernet0/0", field: "x\ny"}
    ctx = FakeCtx(replies=['{"success":true,"applied":[]}'])
    out = tools(topology, ctx)["pt_set_port"](**kwargs)
    assert out.startswith("ERROR") and field in out
    assert ctx.sent == []


@pytest.mark.parametrize("value", BREAKS)
def test_interface_tuning_rejects_breaks_in_interface_and_keys(value):
    assert not validate_interface_tuning(InterfaceTuning(router="R1", interface=value)).is_valid
    assert not validate_interface_tuning(
        InterfaceTuning(router="R1", interface="GigabitEthernet0/0", ospf_auth_key=value)
    ).is_valid
