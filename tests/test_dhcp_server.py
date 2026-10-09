"""Tests for DHCP pools on a Server-PT (pt_configure_dhcp_server, issue #23).

Signatures taken from the IpcAPI reference that PT 9.0 installs
(class_dhcp_server_process / class_dhcp_pool) and verified live: a PC on DHCP
received IP, gateway and DNS from a pool created this way. `DhcpServerMain`
itself has no pool methods: you have to go down to
`getDhcpServerProcessByPortName(port)`.
"""

from tests._registry_src import registry_source
from pathlib import Path

import pytest

from src.packet_tracer_mcp.domain.models.dhcp_server import (
    DEFAULT_SERVER_POOL,
    DhcpServerPool,
)
from src.packet_tracer_mcp.domain.models.errors import ErrorCode
from src.packet_tracer_mcp.domain.rules.dhcp_server_rules import (
    resolve_range,
    validate_dhcp_server,
    validate_dhcp_server_against_topology,
)


def _cfg(**overrides) -> DhcpServerPool:
    base = {
        "device": "DHCP-A", "network": "192.168.10.0", "mask": "255.255.255.0",
        "gateway": "192.168.10.1", "dns": "8.8.8.8", "start_ip": "192.168.10.3",
    }
    base.update(overrides)
    return DhcpServerPool(**base)


def _codes(result) -> set[str]:
    return {e.code for e in result.errors}


def _warn_codes(result) -> set[str]:
    return {w.code for w in result.warnings}


def _live(ip="192.168.10.2", ports=("FastEthernet0",)) -> list[dict]:
    return [{"name": "DHCP-A", "model": "Server-PT",
             "ports": [{"name": p, "ip": ip, "mask": "255.255.255.0"} for p in ports]}]


class TestDefaults:
    def test_targets_the_factory_pool_by_default(self):
        """The GUI shows 'serverPool'; editing it is what a human would do."""
        cfg = DhcpServerPool(device="S", network="10.0.0.0")
        assert cfg.pool_name == DEFAULT_SERVER_POOL == "serverPool"
        assert cfg.port == "FastEthernet0"


class TestResolveRange:
    def test_explicit_start_runs_to_end_of_subnet(self):
        assert resolve_range(_cfg()) == ("192.168.10.3", 252)  # .3 .. .254

    def test_default_start_skips_a_dot_one_gateway(self):
        assert resolve_range(_cfg(start_ip="")) == ("192.168.10.2", 253)

    def test_default_start_is_first_host_when_gateway_is_elsewhere(self):
        start, _ = resolve_range(_cfg(start_ip="", gateway="192.168.10.254"))
        assert start == "192.168.10.1"

    def test_explicit_max_users_is_kept(self):
        assert resolve_range(_cfg(max_users=50)) == ("192.168.10.3", 50)


class TestValidation:
    def test_issue_23_config_passes_clean(self):
        result = validate_dhcp_server(_cfg())
        assert result.is_valid
        assert not result.warnings

    def test_network_is_required(self):
        assert ErrorCode.DHCP_SERVER_INVALID_ADDRESS in _codes(
            validate_dhcp_server(_cfg(network="")))

    @pytest.mark.parametrize("field", ["network", "mask", "gateway", "dns", "start_ip"])
    def test_malformed_address_is_rejected(self, field):
        assert ErrorCode.DHCP_SERVER_INVALID_ADDRESS in _codes(
            validate_dhcp_server(_cfg(**{field: "999.1.1.1"})))

    def test_network_with_host_bits_is_rejected(self):
        assert ErrorCode.DHCP_SERVER_INVALID_ADDRESS in _codes(
            validate_dhcp_server(_cfg(network="192.168.10.5")))

    def test_non_contiguous_mask_is_rejected(self):
        assert ErrorCode.DHCP_SERVER_INVALID_ADDRESS in _codes(
            validate_dhcp_server(_cfg(mask="255.0.255.0")))

    def test_slash_31_has_no_hosts(self):
        result = validate_dhcp_server(_cfg(
            network="10.0.0.0", mask="255.255.255.254", gateway="", start_ip=""))
        assert ErrorCode.DHCP_SERVER_INVALID_RANGE in _codes(result)

    def test_gateway_outside_subnet_is_rejected(self):
        assert ErrorCode.DHCP_SERVER_OUT_OF_SUBNET in _codes(
            validate_dhcp_server(_cfg(gateway="192.168.20.1")))

    @pytest.mark.parametrize("start", ["192.168.10.0", "192.168.10.255", "192.168.11.3"])
    def test_start_must_be_a_host_of_the_subnet(self, start):
        assert ErrorCode.DHCP_SERVER_OUT_OF_SUBNET in _codes(
            validate_dhcp_server(_cfg(start_ip=start)))

    def test_range_past_broadcast_is_rejected(self):
        """.3 + 253 users would reach .255, the broadcast."""
        assert ErrorCode.DHCP_SERVER_INVALID_RANGE in _codes(
            validate_dhcp_server(_cfg(max_users=253)))

    def test_range_up_to_last_host_is_fine(self):
        assert validate_dhcp_server(_cfg(max_users=252)).is_valid

    def test_negative_max_users_is_rejected(self):
        assert ErrorCode.DHCP_SERVER_INVALID_RANGE in _codes(
            validate_dhcp_server(_cfg(max_users=-1)))

    @pytest.mark.parametrize("bad", ["LAN\nB", "LAN\rB", "LAN B"])
    def test_line_terminator_in_pool_name_is_rejected(self, bad):
        """The name travels inside a single-line JS literal."""
        assert ErrorCode.DHCP_INVALID_POOL_NAME in _codes(
            validate_dhcp_server(_cfg(pool_name=bad)))

    def test_empty_pool_name_is_rejected(self):
        assert ErrorCode.DHCP_INVALID_POOL_NAME in _codes(
            validate_dhcp_server(_cfg(pool_name="  ")))

    def test_gateway_inside_range_warns(self):
        """Verified in PT 9.0: the server DOES hand out the gateway's IP."""
        result = validate_dhcp_server(_cfg(start_ip="192.168.10.1"))
        assert result.is_valid
        assert ErrorCode.DHCP_SERVER_RANGE_OVERLAP in _warn_codes(result)

    def test_missing_gateway_warns(self):
        result = validate_dhcp_server(_cfg(gateway=""))
        assert result.is_valid
        assert ErrorCode.DHCP_SERVER_INCOMPLETE in _warn_codes(result)


class TestAgainstTopology:
    def test_unknown_device(self):
        result = validate_dhcp_server_against_topology(_cfg(device="NOPE"), _live())
        assert ErrorCode.DHCP_SERVER_DEVICE_NOT_FOUND in _codes(result)

    def test_unknown_port_lists_real_ones(self):
        result = validate_dhcp_server_against_topology(_cfg(port="Gig0/0"), _live())
        assert ErrorCode.DHCP_SERVER_PORT_NOT_FOUND in _codes(result)
        assert "FastEthernet0" in result.errors[0].suggestion

    def test_server_ip_inside_subnet_is_fine(self):
        """PT skips the server's own IP even if it falls in the range."""
        result = validate_dhcp_server_against_topology(_cfg(), _live())
        assert result.is_valid
        assert not result.warnings

    @pytest.mark.parametrize("ip", ["", "0.0.0.0", "10.9.9.9"])
    def test_server_without_usable_ip_warns(self, ip):
        result = validate_dhcp_server_against_topology(_cfg(), _live(ip=ip))
        assert result.is_valid
        assert ErrorCode.DHCP_SERVER_NO_IP in _warn_codes(result)

    def test_unreadable_ports_fail_open(self):
        result = validate_dhcp_server_against_topology(
            _cfg(), [{"name": "DHCP-A", "model": "Server-PT"}])
        assert result.is_valid


class TestToolPayload:
    """Guards on the JS. Closures inside register_tools → checked as text.

    The JS is built with f-strings, so in the SOURCE the braces are doubled.
    """

    def _src(self) -> str:
        return registry_source()

    def test_goes_through_the_per_port_process(self):
        """The issue's bug: the pool methods are NOT on DhcpServerMain."""
        src = self._src()
        assert "getProcess('DhcpServerMain')" in src
        assert "__m.getDhcpServerProcessByPortName(" in src

    def test_network_mask_takes_network_and_mask(self):
        """With a single argument PT answers 'Invalid arguments for IPC call'."""
        assert (
            "__p.setNetworkMask({json.dumps(cfg.network)}, {json.dumps(cfg.mask)})"
            in self._src()
        )

    def test_start_is_set_before_max_users(self):
        """setMaxUsers recomputes the end from the start."""
        src = self._src()
        assert src.index("__p.setStartIp(") < src.index("__p.setMaxUsers(")

    def test_existing_pool_is_reused_not_duplicated(self):
        assert (
            "if (!__p) {{ __s.addPool({name}); __p = __s.getPool({name}); __created = true; }}"
            in self._src()
        )

    def test_turns_the_service_on(self):
        assert "__s.setEnable(" in self._src()

    def test_factory_pool_dropped_only_when_unconfigured(self):
        src = self._src()
        assert "String(__f.getDefaultRouter()) === '0.0.0.0'" in src
        assert "String(__f.getStartIp()) === String(__f.getNetworkAddress())" in src

    def test_reads_back_after_writing(self):
        src = self._src()
        assert "enabled: !!__s.isEnable()" in src
        assert "max_users: __q.getMaxUsers()" in src

    def test_feature_detected_for_non_servers(self):
        assert "if (!__m) { reportResult(JSON.stringify({ found: true, supported: false })); }" \
            in self._src()

    def test_sent_response_drops_the_js(self):
        """The JS is only useful before sending it (dry_run); afterwards it is noise."""
        assert 'payload.pop("js_payload", None)' in self._src()

    def test_does_not_use_the_undocumented_add_new_pool(self):
        """addNewPool has 8 unnamed arguments in the reference: don't guess."""
        assert "addNewPool(" not in self._src()
