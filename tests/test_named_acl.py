"""Named ACLs were sent as `access-list NAME ...`, which IOS rejects.

A name only goes through `ip access-list standard|extended NAME`.
"""

from __future__ import annotations


from src.packet_tracer_mcp.application.use_cases.apply_acl import build_acl_plan, apply_acl_uc
from src.packet_tracer_mcp.domain.models.acls import ACLBinding
from src.packet_tracer_mcp.infrastructure.generator.acl_cli_generator import (
    build_configure_payload, build_remove_payload, generate_acl_cli, generate_acl_statements,
)


class TestNamedAcl:
    ENTRIES = [{"action": "deny", "protocol": "tcp", "source": "any", "destination": "any",
                "dest_port_op": "eq", "dest_port": 23, "remark": "no telnet"},
               {"action": "permit", "protocol": "ip", "source": "any", "destination": "any"}]

    def test_named_extended_uses_ip_access_list(self):
        plan = build_acl_plan("R1", "BLOCK_TELNET", "extended", self.ENTRIES)
        assert generate_acl_cli(plan) == [
            "ip access-list extended BLOCK_TELNET",
            " remark no telnet",
            " deny tcp any any eq 23",
            " permit ip any any",
            " exit",
        ]

    def test_named_standard(self):
        plan = build_acl_plan("R1", "MGMT", "standard", [{"action": "permit", "source": "host 10.0.0.5"}])
        assert generate_acl_cli(plan)[:2] == ["ip access-list standard MGMT", " permit host 10.0.0.5"]

    def test_no_access_list_line_with_a_name(self):
        plan = build_acl_plan("R1", "BLOCK_TELNET", "extended", self.ENTRIES)
        binding = ACLBinding(router="R1", interface="GigabitEthernet0/0",
                             acl_id="BLOCK_TELNET", direction="out")
        payload = build_configure_payload(plan, binding)
        assert "access-list BLOCK_TELNET" not in payload.replace("ip access-list", "")
        assert " ip access-group BLOCK_TELNET out" in payload.splitlines()

    def test_numbered_unchanged(self):
        plan = build_acl_plan("R1", "101", "extended", self.ENTRIES)
        assert generate_acl_cli(plan) == [
            "access-list 101 remark no telnet",
            "access-list 101 deny tcp any any eq 23",
            "access-list 101 permit ip any any",
        ]

    def test_statements_are_bare(self):
        plan = build_acl_plan("R1", "BLOCK_TELNET", "extended", self.ENTRIES)
        assert generate_acl_statements(plan) == [
            "remark no telnet", "deny tcp any any eq 23", "permit ip any any"]

    def test_use_case_payload(self):
        plan = build_acl_plan("R1", "WEB", "extended", self.ENTRIES)
        result = apply_acl_uc(plan, dry_run=True)
        assert result["valid"]
        assert "ip access-list extended WEB" in result["js_payload"]

    def test_remove_named(self):
        lines = build_remove_payload("R1", "WEB").splitlines()
        assert "no ip access-list extended WEB" in lines
        assert "no access-list WEB" not in lines
        assert "no access-list 101" in build_remove_payload("R1", "101").splitlines()
