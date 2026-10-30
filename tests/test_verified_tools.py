"""The CLI tools and pt_apply_acl_object no longer report success unchecked.

They answered "📤 Applied" as soon as the payload was queued; now the reply says
`verified` and lists the lines IOS rejected. No answer is "unverified".
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.adapters.mcp.tools import acl, nat, switching
from src.packet_tracer_mcp.adapters.mcp.tools.acl import acl_object_outcome
from tests._fake_ctx import FakeCtx, ok_reply, rejected_reply, tools


# ----------------------------------------------------------------------
# The 7 CLI tools
# ----------------------------------------------------------------------

ACL_ARGS = dict(router="R1", name_or_number="10", acl_type="standard",
                entries=[{"action": "permit", "source": "any"}])
TOOL_CASES = [
    (acl, "pt_apply_acl", ACL_ARGS),
    (nat, "pt_apply_nat", dict(router="R1", mode="pat", inside_interface="GigabitEthernet0/0",
                               outside_interface="GigabitEthernet0/1",
                               inside_networks=["192.168.1.0 0.0.0.255"],
                               use_interface_overload=True)),
    (switching, "pt_apply_vlan", dict(switch="SW1", vlans=[{"vlan_id": 10, "name": "A"}])),
    (switching, "pt_apply_stp", dict(switch="SW1", root_primary_vlans=[1])),
    (switching, "pt_apply_port_security", dict(switch="SW1", port="FastEthernet0/1")),
    (switching, "pt_apply_hardening", dict(device="R1", hostname="R1")),
    (switching, "pt_apply_interface_tuning", dict(router="R1", interface="GigabitEthernet0/0",
                                                  bandwidth=1000)),
]
IDS = [c[1] for c in TOOL_CASES]


class TestCliTools:
    @pytest.mark.parametrize("module,name,args", TOOL_CASES, ids=IDS)
    def test_accepted(self, module, name, args):
        ctx = FakeCtx(replies=[lambda js: ok_reply("R1" if '"R1"' in js else "SW1")])
        out = json.loads(tools(module, ctx)[name](**args))
        assert out["valid"], out["errors"]
        assert out["verified"] is True and out["verification"] == "verified"
        assert out["rejected_lines"] == []
        assert "verified" in out["summary"]
        assert ctx.sent and ctx.sent[0].startswith("(function(){")

    @pytest.mark.parametrize("module,name,args", TOOL_CASES, ids=IDS)
    def test_rejected(self, module, name, args):
        ctx = FakeCtx(replies=[rejected_reply("R1", "bad line")])
        out = json.loads(tools(module, ctx)[name](**args))
        assert out["verified"] is False and out["verification"] == "rejected"
        assert out["rejected_lines"][0]["command"] == "bad line"
        assert "Applied" not in out["summary"] and "rejected" in out["summary"]

    @pytest.mark.parametrize("module,name,args", TOOL_CASES, ids=IDS)
    def test_timeout_is_unverified(self, module, name, args):
        ctx = FakeCtx(replies=[None])
        out = json.loads(tools(module, ctx)[name](**args))
        assert out["verified"] is False and out["verification"] == "unverified"
        assert "Applied" not in out["summary"]

    @pytest.mark.parametrize("module,name,args", TOOL_CASES, ids=IDS)
    def test_dry_run_sends_nothing(self, module, name, args):
        ctx = FakeCtx()
        out = json.loads(tools(module, ctx)[name](dry_run=True, **args))
        assert ctx.sent == [] and ctx.queued == []
        assert out["sent"] is False and out["verification"] == "not_sent"
        assert out["js_payload"].startswith("configureIosDevice(")


# ----------------------------------------------------------------------
# pt_apply_acl_object: a partial ACL is not a success
# ----------------------------------------------------------------------

class TestAclObject:
    ARGS = dict(router="R1", name_or_number="WEB", acl_type="extended",
                entries=[{"action": "deny", "protocol": "icmp", "source": "any",
                          "destination": "any"},
                         {"action": "permit", "protocol": "ip", "source": "any",
                          "destination": "any"}])

    def test_outcome(self):
        assert acl_object_outcome(2, 2)["verified"] is True
        assert acl_object_outcome(1, 2) == {"verified": False, "verification": "rejected",
                                            "statements_rejected": 1}
        assert acl_object_outcome(None, 2)["verified"] is False

    def test_named_acl_statements_are_not_dropped(self):
        """The statements used to be cut out of `access-list NAME ...` CLI lines."""
        out = json.loads(tools(acl, FakeCtx())["pt_apply_acl_object"](dry_run=True, **self.ARGS))
        assert out["statements"] == ["deny icmp any any", "permit ip any any"]

    def test_partial_is_not_success(self):
        ctx = FakeCtx(replies=[json.dumps({"success": True, "added": 1, "cmdCount": 1})])
        out = json.loads(tools(acl, ctx)["pt_apply_acl_object"](**self.ARGS))
        assert out["verified"] is False and out["statements_rejected"] == 1
        assert "1/2" in out["summary"] and not out["summary"].startswith("📤")

    def test_full(self):
        ctx = FakeCtx(replies=[json.dumps({"success": True, "added": 2, "cmdCount": 2})])
        out = json.loads(tools(acl, ctx)["pt_apply_acl_object"](**self.ARGS))
        assert out["verified"] is True and out["summary"].startswith("📤")

    def test_timeout(self):
        out = json.loads(tools(acl, FakeCtx(replies=[None]))["pt_apply_acl_object"](**self.ARGS))
        assert out["verified"] is False and out["verification"] == "unverified"
