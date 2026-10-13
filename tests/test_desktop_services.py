"""Apps del Desktop y servicios de Server-PT: reglas, JS y tools.

Sin PT y sin Windows: `send_and_wait` y el presentador se inyectan falsos,
igual que en test_device_panel.py.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp import desktop_service_tools
from src.packet_tracer_mcp.adapters.mcp.desktop_service_tools import html_to_text
from src.packet_tracer_mcp.adapters.mcp.device_panel_tools import register_device_panel_tools
from src.packet_tracer_mcp.domain.models.errors import ErrorCode
from src.packet_tracer_mcp.domain.rules.service_rules import (
    parse_range, validate_dhcp, validate_dns, validate_texts, validate_users,
)
from src.packet_tracer_mcp.infrastructure.generator.service_js import (
    console_peer_js, email_client_js, host_firewall_js, server_dhcp_js, server_dns_js,
    server_http_js, server_service_js, web_go_js, web_read_js,
)
from src.packet_tracer_mcp.infrastructure.ui.presenter import PresenterError
from src.packet_tracer_mcp.shared.ui_mode import UiModeStore
from tests._pt_js import requires_node, run_pt_js


def _codes(res) -> set:
    return {e.code for e in res.errors}


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------

class TestParseRange:
    @pytest.mark.parametrize("raw,expected", [
        ("192.168.1.1-192.168.1.9", ("192.168.1.1", "192.168.1.9")),
        (" 10.0.0.5 - 10.0.0.5 ", ("10.0.0.5", "10.0.0.5")),
        ("10.0.0.7", ("10.0.0.7", "10.0.0.7")),
    ])
    def test_valid(self, raw, expected):
        assert parse_range(raw) == expected

    @pytest.mark.parametrize("raw", [
        "192.168.1.9-192.168.1.1",  # al revés
        "1.1.1.1-2.2.2.2-3.3.3.3", "10.0.0.300", "abc", "",
    ])
    def test_invalid(self, raw):
        assert parse_range(raw) is None


def _dhcp(**kw):
    args = dict(pool="serverPool", gateway="", dns="", start_ip="", mask="", max_users=0,
                tftp="", wlc="", exclude=[])
    args.update(kw)
    return validate_dhcp("Server0", **args)


class TestDhcpRules:
    def test_ok(self):
        assert _dhcp(gateway="192.168.1.1", dns="192.168.1.20", start_ip="192.168.1.100",
                     mask="24", max_users=50, exclude=["192.168.1.1-192.168.1.30"]).is_valid

    def test_bad_ips_and_mask(self):
        res = _dhcp(gateway="192.168.1.256", dns="x", mask="255.0.255.0")
        assert len(res.errors) == 3 and _codes(res) == {ErrorCode.PANEL_INVALID_IP}

    @pytest.mark.parametrize("n", [-1, 65536])
    def test_max_users_range(self, n):
        assert ErrorCode.PANEL_INVALID_VALUE in _codes(_dhcp(max_users=n))

    def test_bad_exclude(self):
        assert not _dhcp(exclude=["192.168.1.30-192.168.1.1"]).is_valid

    def test_newline_in_pool_name(self):
        assert ErrorCode.PANEL_INVALID_CHARS in _codes(_dhcp(pool="p\nool"))


class TestDnsRules:
    def test_a_and_cname_ok(self):
        assert validate_dns("S0", [
            {"name": "www.lab.com", "type": "A", "value": "192.168.1.20"},
            {"name": "web.lab.com", "type": "CNAME", "value": "www.lab.com"},
        ], []).is_valid

    def test_a_needs_an_ip(self):
        assert not validate_dns("S0", [{"name": "www", "type": "A", "value": "www2"}], []).is_valid

    def test_unsupported_type(self):
        res = validate_dns("S0", [{"name": "lab.com", "type": "MX", "value": "mail"}], [])
        assert ErrorCode.PANEL_INVALID_VALUE in _codes(res)

    def test_missing_name_and_cname_target(self):
        res = validate_dns("S0", [{"name": "", "type": "A", "value": "1.1.1.1"},
                                  {"name": "x", "type": "CNAME", "value": ""}], [])
        assert len(res.errors) == 2

    def test_remove_list_is_validated_too(self):
        assert not validate_dns("S0", [], [{"name": "a\nb", "type": "A", "value": "1.1.1.1"}]).is_valid


class TestUserRules:
    def test_ftp_users_ok(self):
        assert validate_users("S0", "ftp", [{"username": "student", "password": "pw",
                                              "permissions": "RL"}]).is_valid

    def test_bad_ftp_permissions(self):
        res = validate_users("S0", "ftp", [{"username": "u", "password": "p", "permissions": "RWX"}])
        assert ErrorCode.PANEL_INVALID_VALUE in _codes(res)

    def test_tftp_has_no_accounts(self):
        assert not validate_users("S0", "tftp", [{"username": "u", "password": "p"}]).is_valid

    def test_unknown_service(self):
        assert not validate_users("S0", "http", []).is_valid

    def test_missing_username_and_control_chars(self):
        res = validate_users("S0", "email", [{"username": "", "password": "a\rb"}])
        assert {ErrorCode.PANEL_INVALID_VALUE, ErrorCode.PANEL_INVALID_CHARS} <= _codes(res)


class TestTextRules:
    def test_body_and_html_may_have_newlines(self):
        assert validate_texts("PC0", body="hola\nchau", html="<p>\n</p>").is_valid

    @pytest.mark.parametrize("field", ["url", "subject", "to", "smtp_server"])
    def test_other_fields_may_not(self, field):
        assert ErrorCode.PANEL_INVALID_CHARS in _codes(validate_texts("PC0", **{field: "a\nb"}))


# ---------------------------------------------------------------------------
# JS
# ---------------------------------------------------------------------------

_BUILDERS = [
    lambda d: web_go_js(d, "http://x"),
    lambda d: web_read_js(d),
    lambda d: server_dhcp_js(d, pool="p", exclude=[("1.1.1.1", "1.1.1.2")]),
    lambda d: server_dns_js(d, records=[{"name": "a", "type": "A", "value": "1.1.1.1"}]),
    lambda d: server_http_js(d, pages={"index.html": "<p>x</p>"}, enable=True),
    lambda d: server_service_js(d, "ftp", enable=True, users=[{"username": "u", "password": "p"}]),
    lambda d: server_service_js(d, "email", users=[{"username": "u", "password": "p"}]),
    lambda d: email_client_js(d, action="send", to="b@x", subject="s", body="b"),
    lambda d: host_firewall_js(d, ipv4=True),
    lambda d: console_peer_js(d),
]

_EVIL = ['S0"); evil(); //', "a\nb", "..\\x", "</script> "]


class TestServiceJs:
    @pytest.mark.parametrize("build", _BUILDERS)
    @pytest.mark.parametrize("evil", _EVIL)
    def test_builders_escape_and_are_iifes(self, build, evil):
        js = build(evil)
        assert js.startswith("(function(){") and js.endswith("})();")
        assert "\n" not in js and " " not in js
        assert json.dumps(evil)[1:-1] in js

    @pytest.mark.parametrize("build", _BUILDERS)
    def test_for_in_only_over_our_own_json(self, build):
        # for...in sobre un objeto nativo de PT lo tira abajo; sobre `c` (json.dumps) no.
        for target in re.findall(r"for\(var \w+ in ([\w.]+)\)", build("S0")):
            assert target.startswith("c.")

    @pytest.mark.parametrize("build", _BUILDERS)
    def test_never_probes_aaa(self, build):
        # getProcess("Aaa") lanza "invalid string position" en PT 9.0.1.
        assert "Aaa" not in build("S0")

    @pytest.mark.parametrize("field", ["url", "subject", "body", "page"])
    def test_user_text_is_data_not_code(self, field):
        evil = '"); ipc.network().removeDevice("R1"); ("\n'
        js = {
            "url": lambda: web_go_js("PC0", evil),
            "subject": lambda: email_client_js("PC0", action="send", to="b", subject=evil),
            "body": lambda: email_client_js("PC0", action="send", to="b", body=evil),
            "page": lambda: server_http_js("S0", pages={"index.html": evil}),
        }[field]()
        assert json.dumps(evil)[1:-1] in js and "\n" not in js

    def test_dhcp_uses_documented_methods(self):
        js = server_dhcp_js("S0", pool="p", gateway="1.1.1.1", remove_pool="old",
                            exclude=[("1.1.1.1", "1.1.1.9")], enable=True)
        for m in ("getDhcpServerProcessByPortName", "addNewPool", "setDefaultRouter",
                  "setDnsServerIp", "setStartIp", "removePool", "addExcludedAddress", "setEnable"):
            assert m in js
        assert '"exclude": [["1.1.1.1", "1.1.1.9"]]' in js
        assert '"enable": true' in js

    def test_dhcp_none_enable_is_left_alone(self):
        assert '"enable": null' in server_dhcp_js("S0", pool="p", enable=None)

    def test_dns_uses_documented_methods(self):
        js = server_dns_js("S0", records=[], remove=[], enable=False)
        for m in ("addARecordToNameServerDb", "addCNAMEToNameServerDb",
                  "removeARecordFromNameServerDb", "removeCNAMEFromNameServerDb"):
            assert m in js

    def test_dns_confirms_records_in_the_record_db(self):
        # getIpAddOfDomain e isDomainNameExisted leen otra tabla: en PT 9.0.1
        # dan 0.0.0.0/false para registros que nslookup sí resuelve. Lo que
        # encuentra el registro guardado es getARecordWithAddress /
        # getCNameRecordWithHostname (null si no está).
        js = server_dns_js("S0", records=[{"name": "w", "type": "CNAME", "value": "x"}])
        assert "getIpAddOfDomain" not in js and "isDomainNameExisted" not in js
        assert "getARecordWithAddress" in js and "getCNameRecordWithHostname" in js

    @pytest.mark.parametrize("service,methods", [
        ("tftp", ["TftpServer", '"setter": "setEnabled"']),
        ("ftp", ["FtpServer", "getFtpUserAccountManager", "addFtpUser", "removeFtpUser"]),
        ("syslog", ["SyslogServer", '"setter": "setEnable"']),
        ("email", ["EmailServer", "addUser", "changePassword", "getAllEmailAcctAsStrings"]),
    ])
    def test_services_use_their_process(self, service, methods):
        js = server_service_js("S0", service, enable=True, users=[])
        for m in methods:
            assert m in js

    def test_unknown_service_is_a_programming_error(self):
        with pytest.raises(KeyError):
            server_service_js("S0", "http")

    @pytest.mark.parametrize("action,method", [
        ("configure", "setSmtpServer"), ("send", "sendMail"), ("receive", "getMailIpc"),
    ])
    def test_email_actions(self, action, method):
        assert method in email_client_js("PC0", action=action)

    def test_firewall_uses_port_setters(self):
        js = host_firewall_js("PC0", ipv4=False, ipv6=True)
        assert "setInboundFirewallService" in js and "setInboundIpv6FirewallService" in js
        assert '"v4": false' in js and '"v6": true' in js

    def test_console_peer_reads_links(self):
        js = console_peer_js("PC1")
        for m in ("getRs232Port", "getLinkCount", "getLinkAt", "getOwnerDevice"):
            assert m in js


# ---------------------------------------------------------------------------
# html_to_text
# ---------------------------------------------------------------------------

class TestHtmlToText:
    def test_strips_tags_scripts_and_entities(self):
        page = ("<html><head><title>Lab</title><style>p{color:red}</style>"
                "<script>alert('x')</script></head><body><h1>Hola</h1>"
                "<p>uno<br>dos &amp; tres</p></body></html>")
        assert html_to_text(page) == "Lab\nHola\nuno\ndos & tres"

    def test_collapses_blank_lines(self):
        assert html_to_text("a<p></p><p></p><p></p>b") == "a\n\nb"

    def test_empty(self):
        assert html_to_text("") == "" and html_to_text(None) == ""


# ---------------------------------------------------------------------------
# Tools con dobles
# ---------------------------------------------------------------------------

class FakePresenter:
    def __init__(self, fail=False, gui_navigates=False):
        self.fail = fail
        self.gui_navigates = gui_navigates
        self.opened: list[dict] = []
        self.captured: list = []
        self.filled: list = []
        self.buttons: list = []

    def device_info(self, device):
        return {"host": True}

    def open(self, device, **kw):
        if self.fail:
            raise PresenterError("sin GUI")
        self.opened.append({"device": device, **kw})
        return {"steps": ["abierto"], "hwnd": 42}

    def scroll_consoles(self, hwnd, u=None):
        return True

    def capture(self, device, path):
        self.captured.append(path)
        return {"path": str(path), "width": 10, "height": 10, "blank": False}

    def fill_and_go(self, hwnd, edit_suffix, text, button_suffix):
        self.filled.append((edit_suffix, text, button_suffix))
        return self.gui_navigates

    def invoke_button(self, hwnd, name):
        self.buttons.append(name)
        return True

    def close(self, device=""):
        return "ok"


def _ok(js: str) -> str:
    return json.dumps({"ok": True})


def _tools(tmp_path, presenter=None, *, mode="headless", reply=_ok):
    store = UiModeStore(tmp_path, environ={})
    store.set(mode)
    mcp = FastMCP("t")
    sent: list[str] = []

    def fake_send(js, timeout):
        sent.append(js)
        return reply(js)

    register_device_panel_tools(mcp, send_and_wait=fake_send, check_bridge=lambda: None,
                                store=store, presenter=presenter or FakePresenter())

    def call(name, **args):
        res = asyncio.run(mcp.call_tool(name, args))
        blocks = res[0] if isinstance(res, tuple) else res
        return "\n".join(getattr(b, "text", "") for b in blocks)

    return call, sent


class TestServiceTools:
    def test_dhcp_rejects_bad_input_before_pt(self, tmp_path):
        call, sent = _tools(tmp_path)
        out = call("pt_server_dhcp", device="Server0", gateway="10.0.0.999")
        assert "Validation failed" in out and sent == []

    def test_dhcp_normalizes_mask_and_ranges(self, tmp_path):
        call, sent = _tools(tmp_path)
        call("pt_server_dhcp", device="Server0", start_ip="192.168.1.100", mask="/24",
             exclude=["192.168.1.1-192.168.1.30", "192.168.1.50"])
        js = sent[0]
        assert '"mask": "255.255.255.0"' in js
        assert '"exclude": [["192.168.1.1", "192.168.1.30"], ["192.168.1.50", "192.168.1.50"]]' in js

    def test_dhcp_headless_never_opens_the_gui(self, tmp_path):
        p = FakePresenter()
        call, _ = _tools(tmp_path, p)
        call("pt_server_dhcp", device="Server0", gateway="192.168.1.1")
        assert p.opened == []

    def test_dhcp_ui_rereads_services_page(self, tmp_path):
        # Las páginas de Services leen los valores al mostrarse: pasar por otra
        # pestaña y volver es lo que las refresca.
        p = FakePresenter()
        call, _ = _tools(tmp_path, p, mode="ui")
        call("pt_server_dhcp", device="Server0", gateway="192.168.1.1")
        assert p.opened[0]["tab"] == "Physical"
        assert (p.opened[1]["tab"], p.opened[1]["section"], p.opened[1]["reopen"]) == \
            ("Services", "DHCP", True)

    def test_missing_service_maps_to_a_readable_error(self, tmp_path):
        call, _ = _tools(tmp_path, reply=lambda js: json.dumps(
            {"ok": False, "error": "no_DhcpServerMain"}))
        assert "has no DHCP service" in call("pt_server_dhcp", device="PC0")

    def test_port_not_found_lists_ports(self, tmp_path):
        call, _ = _tools(tmp_path, reply=lambda js: json.dumps(
            {"ok": False, "error": "port_not_found", "ports": "FastEthernet0"}))
        out = call("pt_server_dhcp", device="Server0", interface="Gig9")
        assert "FastEthernet0" in out

    def test_dns_normalizes_type(self, tmp_path):
        call, sent = _tools(tmp_path)
        call("pt_server_dns", device="Server0",
             records=[{"name": "web.lab", "type": "cname", "value": "www.lab"}])
        assert '"type": "CNAME"' in sent[0]

    def test_dns_rejects_bad_record_before_pt(self, tmp_path):
        call, sent = _tools(tmp_path)
        out = call("pt_server_dns", device="Server0",
                   records=[{"name": "www", "type": "A", "value": "nope"}])
        assert "Validation failed" in out and sent == []

    def test_http_page_name_with_newline_is_rejected(self, tmp_path):
        call, sent = _tools(tmp_path)
        out = call("pt_server_http", device="Server0", pages={"in\ndex.html": "x"})
        assert "Validation failed" in out and sent == []

    def test_http_page_body_may_be_multiline(self, tmp_path):
        call, sent = _tools(tmp_path)
        call("pt_server_http", device="Server0", pages={"index.html": "<h1>a</h1>\n<p>b</p>"})
        assert sent and "\\n" in sent[0] and "\n" not in sent[0]

    def test_service_rejects_dhcp_dns_http(self, tmp_path):
        call, sent = _tools(tmp_path)
        out = call("pt_server_service", device="Server0", service="HTTP", enable=True)
        assert "Validation failed" in out and sent == []

    def test_service_ftp_users_and_section(self, tmp_path):
        p = FakePresenter()
        call, sent = _tools(tmp_path, p)
        call("pt_server_service", device="Server0", service=" FTP ", show=True,
             users=[{"username": "student", "password": "cisco", "permissions": "RL"}])
        assert '"service": "ftp"' in sent[0] and '"username": "student"' in sent[0]
        assert p.opened[-1]["section"] == "FTP"


class TestDesktopTools:
    def test_email_invalid_action(self, tmp_path):
        call, sent = _tools(tmp_path)
        assert "Invalid action" in call("pt_email_client", device="PC0", action="delete")
        assert sent == []

    def test_email_send_needs_to(self, tmp_path):
        call, sent = _tools(tmp_path)
        assert "'to' is missing" in call("pt_email_client", device="PC0", action="send")
        assert sent == []

    def test_email_send_and_show(self, tmp_path):
        p = FakePresenter()
        call, sent = _tools(tmp_path, p)
        call("pt_email_client", device="PC0", action="SEND", to="bob@lab", subject="hi",
             body="línea 1\nlínea 2", show=True)
        assert '"action": "send"' in sent[0]
        assert (p.opened[-1]["tab"], p.opened[-1]["app"]) == ("Desktop", "email")

    def test_firewall_needs_a_flag(self, tmp_path):
        call, sent = _tools(tmp_path)
        assert "ipv4=True/False" in call("pt_host_firewall", device="PC0")
        assert sent == []

    @pytest.mark.parametrize("args,app", [
        ({"ipv4": True}, "firewall"), ({"ipv6": False}, "ipv6_firewall"),
        ({"ipv4": False, "ipv6": False}, "firewall"),
    ])
    def test_firewall_shows_the_right_app(self, tmp_path, args, app):
        p = FakePresenter()
        call, _ = _tools(tmp_path, p, mode="ui")
        call("pt_host_firewall", device="PC0", **args)
        assert p.opened[-1]["app"] == app

    def test_firewall_on_a_router(self, tmp_path):
        call, _ = _tools(tmp_path, reply=lambda js: json.dumps(
            {"ok": False, "error": "not_host_port", "ports": "GigabitEthernet0/0"}))
        assert "is not a host" in call("pt_host_firewall", device="R1", ipv4=True)


def _terminal_reply(peer):
    def reply(js):
        if "getRs232Port" in js:
            return json.dumps({"ok": True, "rs232": "RS 232", "peer": peer, "peer_port": "Console"})
        if "var cmds=" in js:
            return json.dumps({"ok": True, "host": False, "primed": [], "results": [
                {"i": 0, "from": 0, "len": 20, "out": "show clock\nnow\nR1#", "cut": False,
                 "prompt": "R1#", "mode": "enable", "done": True}]})
        return json.dumps({"ok": True})
    return reply


class TestTerminal:
    def test_without_console_cable(self, tmp_path):
        call, sent = _tools(tmp_path, reply=_terminal_reply(None))
        out = call("pt_terminal", device="PC1", commands=["show clock"])
        assert "has no console cable" in out and "cable_type='console'" in out
        assert len(sent) == 1

    def test_types_on_the_peer_console(self, tmp_path):
        call, sent = _tools(tmp_path, reply=_terminal_reply("R1"))
        out = call("pt_terminal", device="PC1", commands=["show clock"])
        assert "console of R1" in out and "now" in out
        run = next(js for js in sent if "var cmds=" in js)
        assert '"R1"' in run and '"PC1"' not in run

    def test_ui_shows_the_pc_terminal_and_accepts_its_settings(self, tmp_path):
        p = FakePresenter()
        call, _ = _tools(tmp_path, p, mode="ui", reply=_terminal_reply("R1"))
        call("pt_terminal", device="PC1", commands=["show clock"])
        assert [(o["device"], o["app"]) for o in p.opened] == [("PC1", "terminal")]
        assert p.buttons == ["OK"]


def test_terminal_unbounded_timeout_is_rejected_before_pt(tmp_path):
    call, sent = _tools(tmp_path, reply=_terminal_reply("R1"))
    out = call("pt_terminal", device="PC1", commands=["show clock"], timeout=1e9)
    assert "Validation failed" in out and not any("var cmds=" in js for js in sent)


def _browser_reply(pages):
    """Cada lectura de getLastPageContent devuelve la siguiente página de la lista."""
    state = {"reads": 0}

    def reply(js):
        if "s.go(c.url)" in js:
            return json.dumps({"ok": True, "started": True})
        if "getLastPageContent" in js:
            i = min(state["reads"], len(pages) - 1)
            state["reads"] += 1
            return json.dumps({"ok": True, "content": pages[i]})
        return json.dumps({"ok": True})
    return reply


class TestWebBrowser:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(desktop_service_tools.time, "sleep", lambda s: None)

    def test_headless_navigates_by_api(self, tmp_path):
        call, sent = _tools(tmp_path, reply=_browser_reply(["", "<h1>Bienvenido</h1>"]))
        out = json.loads(call("pt_web_browser", device="PC0", url="192.168.1.20"))
        assert out["url"] == "http://192.168.1.20" and out["text"] == "Bienvenido"
        assert any('"url": "http://192.168.1.20"' in js and "s.go(c.url)" in js for js in sent)

    def test_gui_navigation_skips_the_api_go(self, tmp_path):
        p = FakePresenter(gui_navigates=True)
        call, sent = _tools(tmp_path, p, reply=_browser_reply(["", "<p>ok</p>"]))
        call("pt_web_browser", device="PC0", url="https://www.lab.com", show=True)
        assert p.filled == [("m_urlEdit", "https://www.lab.com", "m_goButton")]
        assert not any("s.go(c.url)" in js for js in sent)

    def test_gui_failure_falls_back_to_the_api(self, tmp_path):
        p = FakePresenter(gui_navigates=False)
        call, sent = _tools(tmp_path, p, reply=_browser_reply(["", "<p>ok</p>"]))
        call("pt_web_browser", device="PC0", url="www.lab.com", show=True)
        assert any("s.go(c.url)" in js for js in sent)

    def test_error_page_is_flagged(self, tmp_path):
        call, _ = _tools(tmp_path, reply=_browser_reply(["", "<p>Request Timeout</p>"]))
        out = json.loads(call("pt_web_browser", device="PC0", url="http://10.9.9.9"))
        assert "warning" in out

    def test_no_answer(self, tmp_path):
        call, _ = _tools(tmp_path, reply=_browser_reply([""]))
        out = call("pt_web_browser", device="PC0", url="http://10.9.9.9", timeout=0.2)
        assert "no answer" in out

    def test_unchanged_page_is_not_the_new_urls_content(self, tmp_path):
        # The browser keeps showing the last page: the navigation failed or
        # is still loading. That old page must not come back as 10.9.9.9's.
        call, _ = _tools(tmp_path, reply=_browser_reply(["<h1>Old site</h1>"]))
        out = json.loads(call("pt_web_browser", device="PC0", url="http://10.9.9.9", timeout=0.3))
        assert out["ok"] is False and "warning" in out
        assert "text" not in out and "Old site" not in json.dumps(out)

    def test_same_url_twice_returns_the_same_page(self, tmp_path):
        call, _ = _tools(tmp_path, reply=_browser_reply(["", "<h1>Lab</h1>"]))
        first = json.loads(call("pt_web_browser", device="PC0", url="http://10.0.0.20"))
        again = json.loads(call("pt_web_browser", device="PC0", url="http://10.0.0.20",
                                timeout=0.3))
        assert first["text"] == again["text"] == "Lab"
        assert again.get("ok", True) is True
        assert any("identical" in n for n in again["notes"])

    def test_a_different_url_after_a_good_one_still_needs_new_content(self, tmp_path):
        call, _ = _tools(tmp_path, reply=_browser_reply(["", "<h1>Lab</h1>"]))
        call("pt_web_browser", device="PC0", url="http://10.0.0.20")
        out = json.loads(call("pt_web_browser", device="PC0", url="http://10.0.0.99",
                              timeout=0.3))
        assert out["ok"] is False and "text" not in out

    def test_unbounded_timeout_is_rejected_before_pt(self, tmp_path):
        call, sent = _tools(tmp_path)
        out = call("pt_web_browser", device="PC0", url="http://a", timeout=1e9)
        assert "Validation failed" in out and sent == []

    def test_url_with_newline_is_rejected(self, tmp_path):
        call, sent = _tools(tmp_path)
        assert "Validation failed" in call("pt_web_browser", device="PC0", url="http://a\nb")
        assert sent == []

    def test_long_pages_are_cut(self, tmp_path):
        call, _ = _tools(tmp_path, reply=_browser_reply(["", "<p>" + "x" * 9000 + "</p>"]))
        out = json.loads(call("pt_web_browser", device="PC0", url="http://a"))
        assert out["text"].endswith("(cut)") and out["html_bytes"] == 9007


# ---------------------------------------------------------------------------
# pt_server_dhcp: only touch what was asked (executed under Node, fake PT)
# ---------------------------------------------------------------------------

# A DhcpServerProcess with PT's factory "serverPool". Only the methods the
# generated JS calls are faked; addNewPool takes PT's 8 positional arguments.
_DHCP_FAKE = r"""
function __Pool(name, gw, dns, start, mask, max, tftp, wlc) {
  var p = {n: name, net: '0.0.0.0', gw: gw, dns: dns, start: start, mask: mask, max: max,
           tftp: tftp, wlc: wlc};
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
    getTftpAddress: function () { return p.tftp; }, getWlcAddress: function () { return p.wlc; }
  };
}
var __pools = FACTORY ? [__Pool('serverPool', '0.0.0.0', '0.0.0.0', '192.168.1.0',
                                '255.255.255.0', 256, '0.0.0.0', '0.0.0.0')] : [];
if (FACTORY) { __pools[0].setNetworkAddress('192.168.1.0'); }
var __ex = [];
var __on = false;
var dp = {
  getPool: function (n) { for (var i = 0; i < __pools.length; i++) {
    if (__pools[i].getDhcpPoolName() === n) { return __pools[i]; } } return null; },
  addNewPool: function (n, gw, dns, start, mask, max, tftp, wlc) {
    if (arguments.length !== 8) { throw new Error('Invalid arguments for IPC call'); }
    __pools.push(__Pool(n, gw, dns, start, mask, max, tftp, wlc)); },
  removePool: function (n) { __pools = __pools.filter(function (p) {
    return p.getDhcpPoolName() !== n; }); },
  getPoolCount: function () { return __pools.length; },
  getPoolAt: function (i) { return __pools[i]; },
  addExcludedAddress: function (a, b) { __ex.push([a, b]); },
  getExcludedAddressCount: function () { return __ex.length; },
  setEnable: function (x) { __on = x; }, isEnable: function () { return __on; }
};
var dm = {getDhcpServerProcessByPortName: function (n) { return n === 'FastEthernet0' ? dp : null; }};
var d = {getPorts: function () { return ['FastEthernet0']; },
         getProcess: function (n) { return n === 'DhcpServerMain' ? dm : null; }};
var ipc = {network: function () { return {getDevice: function () { return d; }}; }};
"""


def _run_dhcp(tmp_path, *, factory=True, **kw) -> dict:
    kw.setdefault("pool", "serverPool")  # the tool's default
    js = server_dhcp_js("Server0", **kw)
    fake = _DHCP_FAKE.replace("FACTORY", "true" if factory else "false")
    reports = run_pt_js(js, fake, tmp_path)
    assert len(reports) == 1
    return json.loads(reports[0])


@requires_node
class TestDhcpTouchesOnlyWhatWasAsked:
    def test_remove_only_does_not_recreate_the_pool(self, tmp_path):
        out = _run_dhcp(tmp_path, remove_pool="serverPool", enable=None)
        assert out["pools"] == []
        assert not any("created" in x or "updated" in x for x in out["done"])

    def test_exclude_only_creates_no_pool(self, tmp_path):
        out = _run_dhcp(tmp_path, factory=False, exclude=[("10.0.0.1", "10.0.0.9")], enable=None)
        assert out["pools"] == [] and out["excluded"] == 1

    def test_exclude_only_leaves_the_factory_pool_alone(self, tmp_path):
        out = _run_dhcp(tmp_path, exclude=[("192.168.1.1", "192.168.1.9")], enable=True)
        assert not any("updated" in x or "created" in x for x in out["done"])
        assert out["pools"][0]["gateway"] == "0.0.0.0" and out["excluded"] == 1

    def test_enable_only_touches_no_pool(self, tmp_path):
        out = _run_dhcp(tmp_path, factory=False, enable=True)
        assert out["pools"] == [] and out["enabled"] is True

    def test_fields_still_create_a_missing_pool(self, tmp_path):
        out = _run_dhcp(tmp_path, pool="LAN10", gateway="10.0.10.1", start_ip="10.0.10.10",
                        mask="255.255.255.0", tftp="10.0.10.5")
        lan = {p["name"]: p for p in out["pools"]}["LAN10"]
        assert (lan["gateway"], lan["start"], lan["tftp"]) == ("10.0.10.1", "10.0.10.10",
                                                               "10.0.10.5")

    def test_existing_pool_applies_mask_without_start(self, tmp_path):
        out = _run_dhcp(tmp_path, mask="255.255.0.0")
        pool = out["pools"][0]
        assert pool["mask"] == "255.255.0.0" and pool["network"] == "192.168.0.0"

    def test_existing_pool_applies_every_settable_field(self, tmp_path):
        out = _run_dhcp(tmp_path, gateway="192.168.1.1", dns="192.168.1.20",
                        start_ip="192.168.1.100", mask="255.255.255.0", max_users=50)
        pool = out["pools"][0]
        assert (pool["gateway"], pool["dns"], pool["start"], pool["mask"], pool["max_users"]) == \
            ("192.168.1.1", "192.168.1.20", "192.168.1.100", "255.255.255.0", 50)
        assert pool["network"] == "192.168.1.0"

    def test_existing_pool_reports_tftp_and_wlc_as_not_applied(self, tmp_path):
        out = _run_dhcp(tmp_path, tftp="192.168.1.5", wlc="192.168.1.6", gateway="192.168.1.1")
        assert set(out["not_applied"]) == {"tftp", "wlc"}
        assert out["pools"][0]["gateway"] == "192.168.1.1"


class TestDhcpRemoveRules:
    def test_removing_and_editing_the_same_pool_is_contradictory(self):
        res = _dhcp(remove_pool="serverPool", gateway="192.168.1.1")
        assert ErrorCode.PANEL_INVALID_VALUE in _codes(res)

    def test_removing_the_default_pool_alone_is_fine(self):
        assert _dhcp(remove_pool="serverPool").is_valid

    def test_remove_pool_name_with_newline_is_rejected(self):
        assert ErrorCode.PANEL_INVALID_CHARS in _codes(_dhcp(remove_pool="a\nb"))

    def test_tool_remove_only_sends_no_pool_edit(self, tmp_path):
        call, sent = _tools(tmp_path)
        call("pt_server_dhcp", device="Server0", remove_pool="serverPool")
        assert '"pool": ""' in sent[0] and '"remove": "serverPool"' in sent[0]


def _dhcp_tool_reply(pools, not_applied=()):
    def reply(js):
        return json.dumps({"ok": True, "port": "FastEthernet0", "done": ["pool serverPool updated"],
                           "enabled": True, "pools": pools, "excluded": 0,
                           "not_applied": list(not_applied)})
    return reply


_FACTORY_READBACK = {"name": "serverPool", "network": "192.168.1.0", "mask": "255.255.255.0",
                     "start": "192.168.1.0", "end": "192.168.1.255", "gateway": "0.0.0.0",
                     "dns": "0.0.0.0", "max_users": 256, "tftp": "0.0.0.0", "wlc": "0.0.0.0"}


class TestDhcpToolHonesty:
    def test_tftp_not_applied_is_not_reported_as_success(self, tmp_path):
        call, _ = _tools(tmp_path, reply=_dhcp_tool_reply([_FACTORY_READBACK], ["tftp"]))
        out = json.loads(call("pt_server_dhcp", device="Server0", tftp="192.168.1.5"))
        assert out["ok"] is False
        assert any(x.startswith("tftp") for x in out["not_applied"])
        assert "warning" in out

    def test_read_back_mismatch_is_reported(self, tmp_path):
        # PT kept another value (e.g. it capped max_users): the read-back says so.
        call, _ = _tools(tmp_path, reply=_dhcp_tool_reply([_FACTORY_READBACK]))
        out = json.loads(call("pt_server_dhcp", device="Server0", gateway="192.168.1.1"))
        assert out["ok"] is False and any("gateway" in x for x in out["not_applied"])

    def test_matching_read_back_is_ok(self, tmp_path):
        pool = dict(_FACTORY_READBACK, gateway="192.168.1.1")
        call, _ = _tools(tmp_path, reply=_dhcp_tool_reply([pool]))
        out = json.loads(call("pt_server_dhcp", device="Server0", gateway="192.168.1.1"))
        assert out["ok"] is True and "not_applied" not in out
