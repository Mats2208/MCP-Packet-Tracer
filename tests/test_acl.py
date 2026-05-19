"""Tests unitarios para ACLs: modelos, validador, generator y use case."""

from __future__ import annotations
import pytest

from packet_tracer_mcp.domain.models.acls import ACLPlan, ACLEntry, ACLBinding
from packet_tracer_mcp.domain.models.errors import ErrorCode
from packet_tracer_mcp.domain.rules.acl_rules import (
    validate_acl_plan,
    validate_acl_binding,
)
from packet_tracer_mcp.infrastructure.generator.acl_cli_generator import (
    generate_acl_cli,
    generate_acl_binding_cli,
    build_configure_payload,
    build_remove_payload,
)
from packet_tracer_mcp.application.use_cases.apply_acl import (
    build_acl_plan,
    apply_acl_uc,
    remove_acl_uc,
    validate_against_topology,
)


# ----------------------------------------------------------------------
# Validator tests
# ----------------------------------------------------------------------

class TestACLValidator:
    def test_valid_extended_acl_passes(self):
        plan = ACLPlan(
            router="R1",
            name_or_number="101",
            acl_type="extended",
            entries=[
                ACLEntry(
                    action="deny",
                    protocol="icmp",
                    source="192.168.1.0 0.0.0.255",
                    destination="192.168.0.0 0.0.0.255",
                    icmp_type="echo",
                ),
                ACLEntry(action="permit", protocol="ip", source="any", destination="any"),
            ],
        )
        result = validate_acl_plan(plan)
        assert result.is_valid, result.error_messages()

    def test_empty_acl_fails(self):
        plan = ACLPlan(router="R1", name_or_number="101", acl_type="extended", entries=[])
        result = validate_acl_plan(plan)
        assert not result.is_valid
        assert any(e.code == ErrorCode.ACL_EMPTY for e in result.errors)

    def test_standard_with_destination_fails(self):
        plan = ACLPlan(
            router="R1", name_or_number="10", acl_type="standard",
            entries=[ACLEntry(action="permit", source="192.168.1.0 0.0.0.255", destination="any")],
        )
        result = validate_acl_plan(plan)
        assert not result.is_valid
        assert any(e.code == ErrorCode.ACL_TYPE_MISMATCH for e in result.errors)

    def test_extended_number_in_standard_range_fails(self):
        plan = ACLPlan(
            router="R1", name_or_number="50", acl_type="extended",
            entries=[ACLEntry(action="permit", source="any", destination="any")],
        )
        result = validate_acl_plan(plan)
        assert not result.is_valid
        assert any(e.code == ErrorCode.ACL_TYPE_MISMATCH for e in result.errors)

    def test_named_acl_does_not_validate_number_range(self):
        plan = ACLPlan(
            router="R1", name_or_number="BLOCK_HTTP", acl_type="extended",
            entries=[ACLEntry(action="permit", source="any", destination="any")],
        )
        result = validate_acl_plan(plan)
        assert result.is_valid

    def test_ports_with_non_tcp_udp_fails(self):
        plan = ACLPlan(
            router="R1", name_or_number="101", acl_type="extended",
            entries=[
                ACLEntry(
                    action="deny", protocol="ip",  # ip no soporta puertos
                    source="any", destination="any",
                    dest_port_op="eq", dest_port=80,
                ),
            ],
        )
        result = validate_acl_plan(plan)
        assert not result.is_valid
        assert any(e.code == ErrorCode.ACL_INVALID_PROTOCOL_FOR_PORTS for e in result.errors)

    def test_invalid_wildcard_format_fails(self):
        plan = ACLPlan(
            router="R1", name_or_number="101", acl_type="extended",
            entries=[ACLEntry(action="permit", source="not.a.valid.ip 0.0.0.255", destination="any")],
        )
        result = validate_acl_plan(plan)
        assert not result.is_valid
        assert any(
            e.code in (ErrorCode.ACL_INVALID_WILDCARD, ErrorCode.INVALID_IP_ADDRESS)
            for e in result.errors
        )

    def test_unreachable_rule_warning(self):
        plan = ACLPlan(
            router="R1", name_or_number="101", acl_type="extended",
            entries=[
                ACLEntry(action="permit", protocol="ip", source="any", destination="any"),
                ACLEntry(action="deny", protocol="tcp", source="any", destination="any",
                         dest_port_op="eq", dest_port=23),
            ],
        )
        result = validate_acl_plan(plan)
        # No errores pero sí warning
        assert result.is_valid
        assert any(w.code == ErrorCode.ACL_UNREACHABLE_RULE for w in result.warnings)

    def test_binding_router_mismatch(self):
        plan = ACLPlan(router="R1", name_or_number="101", acl_type="extended",
                       entries=[ACLEntry(action="permit", source="any", destination="any")])
        binding = ACLBinding(router="R2", interface="Gig0/0", acl_id="101", direction="in")
        result = validate_acl_binding(binding, plan)
        assert not result.is_valid
        assert any(e.code == ErrorCode.ACL_ROUTER_NOT_FOUND for e in result.errors)


# ----------------------------------------------------------------------
# Generator tests
# ----------------------------------------------------------------------

class TestACLGenerator:
    def test_extended_icmp_echo_deny(self):
        plan = ACLPlan(
            router="R1", name_or_number="101", acl_type="extended",
            entries=[
                ACLEntry(action="deny", protocol="icmp",
                         source="192.168.1.0 0.0.0.255",
                         destination="192.168.0.0 0.0.0.255",
                         icmp_type="echo"),
                ACLEntry(action="permit", protocol="ip", source="any", destination="any"),
            ],
        )
        lines = generate_acl_cli(plan)
        assert lines[0] == "access-list 101 deny icmp 192.168.1.0 0.0.0.255 192.168.0.0 0.0.0.255 echo"
        assert lines[1] == "access-list 101 permit ip any any"

    def test_extended_tcp_with_port(self):
        plan = ACLPlan(
            router="R1", name_or_number="102", acl_type="extended",
            entries=[
                ACLEntry(action="deny", protocol="tcp",
                         source="192.168.1.0 0.0.0.255", destination="any",
                         dest_port_op="eq", dest_port=80),
            ],
        )
        lines = generate_acl_cli(plan)
        assert lines[0] == "access-list 102 deny tcp 192.168.1.0 0.0.0.255 any eq 80"

    def test_standard_acl_source_only(self):
        plan = ACLPlan(
            router="R1", name_or_number="10", acl_type="standard",
            entries=[ACLEntry(action="permit", source="192.168.0.0 0.0.0.255")],
        )
        lines = generate_acl_cli(plan)
        assert lines[0] == "access-list 10 permit 192.168.0.0 0.0.0.255"

    def test_binding_cli(self):
        binding = ACLBinding(router="R1", interface="GigabitEthernet0/0",
                              acl_id="101", direction="in")
        lines = generate_acl_binding_cli(binding)
        assert lines == [
            "interface GigabitEthernet0/0",
            " ip access-group 101 in",
            " exit",
        ]

    def test_full_payload_with_binding(self):
        plan = ACLPlan(
            router="R1", name_or_number="101", acl_type="extended",
            entries=[ACLEntry(action="permit", protocol="ip", source="any", destination="any")],
        )
        binding = ACLBinding(router="R1", interface="Gig0/0", acl_id="101", direction="in")
        payload = build_configure_payload(plan, binding)
        assert payload.startswith("enable\nconfigure terminal\n")
        assert "access-list 101 permit ip any any" in payload
        assert "interface Gig0/0" in payload
        assert "ip access-group 101 in" in payload
        assert payload.endswith("end\nwrite memory")

    def test_remove_payload(self):
        payload = build_remove_payload("R1", "101", binding_interface="Gig0/0", direction="in")
        assert "no ip access-group 101 in" in payload
        assert "no access-list 101" in payload

    def test_remark_emitted_before_rule(self):
        plan = ACLPlan(
            router="R1", name_or_number="101", acl_type="extended",
            entries=[
                ACLEntry(action="deny", protocol="tcp",
                         source="any", destination="any",
                         dest_port_op="eq", dest_port=23,
                         remark="Block telnet from anywhere"),
            ],
        )
        lines = generate_acl_cli(plan)
        assert lines[0] == "access-list 101 remark Block telnet from anywhere"
        assert lines[1] == "access-list 101 deny tcp any any eq 23"


# ----------------------------------------------------------------------
# Use case tests
# ----------------------------------------------------------------------

class TestApplyACLUseCase:
    def test_dry_run_returns_payload_without_sending(self):
        plan = build_acl_plan(
            router="R1", name_or_number="101", acl_type="extended",
            entries_dicts=[
                {"action": "permit", "protocol": "ip", "source": "any", "destination": "any"},
            ],
        )
        result = apply_acl_uc(plan=plan, dry_run=True)
        assert result["valid"]
        assert not result["sent"]
        assert result["dry_run"]
        assert 'configureIosDevice("R1"' in result["js_payload"]
        assert "access-list 101 permit ip any any" in result["js_payload"]

    def test_invalid_plan_does_not_send(self):
        plan = build_acl_plan("R1", "101", "extended", [])  # vacía
        sent_calls = []
        result = apply_acl_uc(
            plan=plan,
            bridge_send=lambda js: sent_calls.append(js) or True,
        )
        assert not result["valid"]
        assert not result["sent"]
        assert sent_calls == []

    def test_validates_router_against_pt_topology(self):
        plan = build_acl_plan(
            router="R-NONEXISTENT", name_or_number="101", acl_type="extended",
            entries_dicts=[{"action": "permit", "source": "any", "destination": "any"}],
        )
        result = apply_acl_uc(
            plan=plan,
            query_pt_topology=lambda: [{"name": "R1", "model": "2911"}],
            dry_run=True,
        )
        assert not result["valid"]
        assert any(e["error_code"] == ErrorCode.ACL_ROUTER_NOT_FOUND.value
                   for e in result["errors"])

    def test_validates_interface_against_catalog(self):
        plan = build_acl_plan(
            router="R1", name_or_number="101", acl_type="extended",
            entries_dicts=[{"action": "permit", "source": "any", "destination": "any"}],
        )
        binding = ACLBinding(router="R1", interface="FakePort99",
                              acl_id="101", direction="in")
        result = apply_acl_uc(
            plan=plan,
            binding=binding,
            query_pt_topology=lambda: [{"name": "R1", "model": "2911"}],
            dry_run=True,
        )
        assert not result["valid"]
        assert any(e["error_code"] == ErrorCode.ACL_INTERFACE_NOT_FOUND.value
                   for e in result["errors"])

    def test_accepts_subinterface_when_base_port_exists(self):
        """Sub-interfaces (G0/0/1.20) son válidas si el puerto base (G0/0/1) existe.
        PT las crea dinámicamente al configurar encapsulation dot1Q."""
        plan = build_acl_plan(
            router="R1", name_or_number="101", acl_type="extended",
            entries_dicts=[{"action": "permit", "source": "any", "destination": "any"}],
        )
        binding = ACLBinding(router="R1", interface="GigabitEthernet0/0.20",
                              acl_id="101", direction="in")
        result = apply_acl_uc(
            plan=plan,
            binding=binding,
            query_pt_topology=lambda: [{"name": "R1", "model": "2911"}],
            dry_run=True,
        )
        assert result["valid"], f"Sub-interface rechazada: {result.get('errors')}"

    def test_valid_plan_sends_when_bridge_ok(self):
        plan = build_acl_plan(
            router="R1", name_or_number="101", acl_type="extended",
            entries_dicts=[{"action": "permit", "source": "any", "destination": "any"}],
        )
        sent_payload = []
        result = apply_acl_uc(
            plan=plan,
            bridge_send=lambda js: sent_payload.append(js) or True,
        )
        assert result["valid"]
        assert result["sent"]
        assert len(sent_payload) == 1
        # JS payload viaja en una sola línea (sin \n REALES en el código JS)
        assert "\n" not in sent_payload[0]
        # Pero los \\n literales sí están dentro del string
        assert "\\n" in sent_payload[0]

    def test_remove_acl_dry_run(self):
        result = remove_acl_uc(
            router="R1", name_or_number="101",
            binding_interface="Gig0/0", direction="in",
            dry_run=True,
        )
        assert not result["sent"]
        assert "no ip access-group 101 in" in result["js_payload"]
        assert "no access-list 101" in result["js_payload"]


# ----------------------------------------------------------------------
# JS payload encoding (regresión: \n no debe ser literal de código)
# ----------------------------------------------------------------------

class TestJSPayloadEncoding:
    def test_payload_is_single_js_line(self):
        plan = build_acl_plan(
            router="CORE-R1", name_or_number="101", acl_type="extended",
            entries_dicts=[
                {"action": "deny", "protocol": "icmp",
                 "source": "192.168.1.0 0.0.0.255",
                 "destination": "192.168.0.0 0.0.0.255",
                 "icmp_type": "echo"},
                {"action": "permit", "protocol": "ip", "source": "any", "destination": "any"},
            ],
        )
        result = apply_acl_uc(plan=plan, dry_run=True)
        # Crítico: ningún \n REAL en el string del payload — todo en una línea JS
        assert "\n" not in result["js_payload"]
        # El string IOS dentro del primer arg tiene \\n escapado (literales)
        assert "enable\\nconfigure terminal\\n" in result["js_payload"]

    def test_router_name_with_quotes_is_escaped(self):
        plan = build_acl_plan(
            router='Device"with"quotes', name_or_number="101", acl_type="extended",
            entries_dicts=[{"action": "permit", "source": "any", "destination": "any"}],
        )
        result = apply_acl_uc(plan=plan, dry_run=True)
        # Las comillas dobles dentro del nombre se escapan
        assert 'Device\\"with\\"quotes' in result["js_payload"]
