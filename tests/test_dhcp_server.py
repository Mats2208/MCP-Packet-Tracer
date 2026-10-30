"""Tests for DHCP pools on a Server-PT (pt_configure_dhcp_server, issue #23).

Signatures taken from the IpcAPI reference that PT 9.0 installs
(class_dhcp_server_process / class_dhcp_pool) and verified live: a PC on DHCP
received IP, gateway and DNS from a pool created this way. `DhcpServerMain`
itself has no pool methods: you have to go down to
`getDhcpServerProcessByPortName(port)`.
"""

import json

from tests._registry_src import registry_source
from tests._pt_js import requires_node, run_pt_js
from src.packet_tracer_mcp.infrastructure.generator.service_js import server_dhcp_js
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
    """pt_configure_dhcp_server sends server_dhcp_js, the backend pt_server_dhcp uses.

    The JS used to be its own f-string copy of the DHCP API; the two tools had
    drifted (one created pools with addPool, the other with addNewPool). Now the
    tool only maps its arguments and PT's answer, and the JS is run under Node
    against a fake DhcpServerProcess that enforces the measured signatures.
    """

    def _send(self, reply: dict | None = None, **kw) -> tuple[str, list[str]]:
        from src.packet_tracer_mcp.adapters.mcp.tools import services
        from tests._fake_ctx import FakeCtx, tools

        ctx = FakeCtx(replies=[json.dumps(reply or {"ok": True, "pools": [], "enabled": True,
                                                    "created": True, "error": ""})])
        ctx.pick_channel = lambda: ""  # skip the live-topology validation
        args = {"device": "DHCP-A", "network": "192.168.10.0", "gateway": "192.168.10.1",
                "dns": "8.8.8.8", "start_ip": "192.168.10.3", **kw}
        return tools(services, ctx)["pt_configure_dhcp_server"](**args), ctx.sent

    def test_sends_the_shared_backend(self):
        _, sent = self._send()
        assert sent[0] == server_dhcp_js(
            "DHCP-A", port="FastEthernet0", pool="serverPool", network="192.168.10.0",
            mask="255.255.255.0", gateway="192.168.10.1", dns="8.8.8.8",
            start_ip="192.168.10.3", max_users=252, enable=True, drop_factory_pool=True)

    def test_read_only_and_remove_touch_nothing_else(self):
        _, sent = self._send(network="")
        assert '"pool": ""' in sent[0] and '"enable": null' in sent[0]
        _, sent = self._send(network="", remove=True, pool_name="LAN")
        assert '"remove": "LAN"' in sent[0] and '"enable": null' in sent[0]

    def test_pt_errors_are_named(self):
        out, _ = self._send(reply={"ok": False, "error": "no_DhcpServerMain"})
        assert "has no Server-PT DHCP server" in out

    def test_sent_response_drops_the_js(self):
        out = json.loads(self._send()[0])
        assert "js_payload" not in out and out["sent"] is True

    def test_add_new_pool_only_with_its_eight_measured_arguments(self):
        """addNewPool(name, gateway, dns, start, mask, max, tftp, wlc), measured live on
        PT 9.0.1, is the only call that stores TFTP/WLC; it is never called otherwise."""
        js = self._js()
        assert "dp.addNewPool(ar[0],ar[1],ar[2],ar[3],ar[4],ar[5],ar[6],ar[7])" in js
        assert "if(c.pool&&(c.tftp||c.wlc))" in js

    def _js(self, **kw) -> str:
        args = dict(port="FastEthernet0", pool="LAN", network="192.168.10.0",
                    mask="255.255.255.0", gateway="192.168.10.1", start_ip="192.168.10.3",
                    max_users=50, enable=True, drop_factory_pool=True)
        args.update(kw)
        return server_dhcp_js("DHCP-A", **args)

    @requires_node
    def test_creates_fills_and_reads_back_under_node(self, tmp_path):
        out = json.loads(run_pt_js(self._js(), _FAKE_DHCP, tmp_path)[-1])
        lan = {p["name"]: p for p in out["pools"]}["LAN"]
        assert out["created"] is True and out["enabled"] is True
        assert (lan["network"], lan["mask"], lan["gateway"], lan["start"], lan["max_users"]) ==             ("192.168.10.0", "255.255.255.0", "192.168.10.1", "192.168.10.3", 50)
        # The unconfigured factory pool (start = network, no gateway) is dropped.
        assert out["dropped_factory_pool"] is True and "serverPool" not in             {p["name"] for p in out["pools"]}

    @requires_node
    def test_existing_pool_is_reused_not_duplicated(self, tmp_path):
        out = json.loads(run_pt_js(self._js(pool="serverPool"), _FAKE_DHCP, tmp_path)[-1])
        assert out["created"] is False
        assert [p["name"] for p in out["pools"]] == ["serverPool"]


# A DhcpServerProcess that throws on the signatures PT rejects: setNetworkMask with
# one argument, and setMaxUsers before setStartIp is checked by the read-back.
_FAKE_DHCP = r"""
function __Pool(name) {
  var p = {n: name, net: '0.0.0.0', mask: '0.0.0.0', gw: '0.0.0.0', dns: '0.0.0.0',
           start: '0.0.0.0', max: 0};
  return {
    getDhcpPoolName: function () { return p.n; },
    getNetworkAddress: function () { return p.net; }, setNetworkAddress: function (x) { p.net = x; },
    getSubnetMask: function () { return p.mask; },
    setNetworkMask: function (net, m) {
      if (arguments.length !== 2) { throw new Error('Invalid arguments for IPC call'); }
      p.net = net; p.mask = m; },
    getStartIp: function () { return p.start; }, setStartIp: function (x) { p.start = x; },
    getEndIp: function () { return '0.0.0.0'; },
    getDefaultRouter: function () { return p.gw; }, setDefaultRouter: function (x) { p.gw = x; },
    getDnsServerIp: function () { return p.dns; }, setDnsServerIp: function (x) { p.dns = x; },
    getMaxUsers: function () { return p.max; }, setMaxUsers: function (x) { p.max = x; },
    getTftpAddress: function () { return '0.0.0.0'; }, getWlcAddress: function () { return '0.0.0.0'; }
  };
}
var __pools = [__Pool('serverPool')];
__pools[0].setNetworkAddress('192.168.10.0');
__pools[0].setStartIp('192.168.10.0');
var __on = false;
var dp = {
  getPool: function (n) { for (var i = 0; i < __pools.length; i++) {
    if (__pools[i].getDhcpPoolName() === n) { return __pools[i]; } } return null; },
  addPool: function (n) { if (!dp.getPool(n)) { __pools.push(__Pool(n)); } },
  removePool: function (n) { __pools = __pools.filter(function (p) {
    return p.getDhcpPoolName() !== n; }); },
  getPoolCount: function () { return __pools.length; },
  getPoolAt: function (i) { return __pools[i]; },
  getExcludedAddressCount: function () { return 0; },
  setEnable: function (x) { __on = x; }, isEnable: function () { return __on; }
};
var dm = {getDhcpServerProcessByPortName: function (n) { return n === 'FastEthernet0' ? dp : null; }};
var d = {getPorts: function () { return ['FastEthernet0']; },
         getPort: function () { return {getIpAddress: function () { return '192.168.10.2'; }}; },
         getProcess: function (n) { return n === 'DhcpServerMain' ? dm : null; }};
var ipc = {network: function () { return {getDevice: function () { return d; }}; }};
"""

