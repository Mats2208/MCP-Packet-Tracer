"""Tools del panel de dispositivo, piezas de la GUI y validación de IP Configuration.

Sin PT y sin Windows: `send_and_wait` y el presentador se inyectan falsos.
"""

from __future__ import annotations

import asyncio
import json
import struct
import zlib
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp import device_panel_tools
from src.packet_tracer_mcp.adapters.mcp.device_panel_tools import (
    register_device_panel_tools, screenshot_path,
)
from src.packet_tracer_mcp.domain.models.errors import ErrorCode
from src.packet_tracer_mcp.domain.rules.panel_rules import (
    normalize_mask, validate_host_ip_config,
)
from src.packet_tracer_mcp.domain.rules.service_rules import validate_dhcp
from src.packet_tracer_mcp.infrastructure.execution.console_session import build_run_js
from src.packet_tracer_mcp.infrastructure.generator.host_js import (
    host_ip_config_js, read_device_panel_js, remove_module_js,
)
from src.packet_tracer_mcp.infrastructure.ui import names
from src.packet_tracer_mcp.infrastructure.ui.png import bgra_to_png, looks_blank
from src.packet_tracer_mcp.infrastructure.ui.presenter import (
    PresenterError, center_on_js, device_state_js, show_dialog_js,
)
from src.packet_tracer_mcp.shared.ui_mode import UiModeStore


# ---------------------------------------------------------------------------
# PNG / nombres
# ---------------------------------------------------------------------------

def _decode_png(blob: bytes) -> tuple[int, int, bytes]:
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        tag = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", blob[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF
        chunks[tag] = data
        pos += 12 + length
    w, h = struct.unpack(">II", chunks[b"IHDR"][:8])
    return w, h, zlib.decompress(chunks[b"IDAT"])


class TestPng:
    def test_bgra_becomes_rgb_rows_with_filter_byte(self):
        # 2x1: un píxel azul (BGRA 255,0,0) y uno rojo (0,0,255).
        blob = bgra_to_png(2, 1, bytes([255, 0, 0, 255, 0, 0, 255, 255]))
        w, h, raw = _decode_png(blob)
        assert (w, h) == (2, 1)
        assert raw == bytes([0, 0, 0, 255, 255, 0, 0])

    def test_short_buffer_is_rejected(self):
        with pytest.raises(ValueError):
            bgra_to_png(2, 2, b"\x00" * 4)

    def test_blank_detection(self):
        assert looks_blank(b"\x00\x00\x00\xff" * 100)
        assert not looks_blank(b"\x00\x00\x00\xff" * 99 + b"\x10\x20\x30\xff")


class TestNames:
    @pytest.mark.parametrize("alias,obj", [
        ("Command Prompt", "CommandPromptBtn"), ("cmd", "CommandPromptBtn"),
        ("ip_configuration", "IPConfigBtn"), ("Web Browser", "WebBrowserBtn"),
        ("MIB Browser", "MIBBroswerBtn"), ("IoT Monitor", "IoT Monitor_btn"),
        ("CommandPromptBtn", "CommandPromptBtn"),
    ])
    def test_aliases(self, alias, obj):
        assert names.desktop_app_object_name(alias) == obj

    def test_unknown_app(self):
        assert names.desktop_app_object_name("minesweeper") is None

    def test_tab_match_ignores_case(self):
        assert names.tab_matches("cli", "CLI")


# ---------------------------------------------------------------------------
# Presenter._open_app con un árbol UIA falso
# ---------------------------------------------------------------------------

_DESK = "CWorkstationDialog.m_desktopTab.scrollArea.qt_scrollarea_viewport.desktopScrollAreaWidgetContents"
# Las dos cabeceras de applet que usa PT 9.0.1 (vistas por UIA en un PC-PT):
# Command Prompt → m_titleBar/m_titleLabel/m_closeButton; Firewall y Email →
# m_titleFrame/m_titleLable (sic)/m_closeBtn.
_BAR = ("m_titleBar", "m_titleLabel", "m_closeButton")
_FRAME = ("m_titleFrame", "m_titleLable", "m_closeBtn")


def _el(aid: str, name: str = ""):
    return SimpleNamespace(automation_id=aid, name=name, offscreen=False, control_type=0)


class FakeDesktopUia:
    """Desktop de una PC. `panels` es la pila de applets abiertos (afuera →
    adentro). Mientras haya uno, los botones del Desktop no están en el árbol, y
    como en PT, el cierre de un panel solo funciona si es el de más adentro."""

    def __init__(self, panels=()):
        self.panels = list(panels)  # [(ruta bajo CDesktopApplet, título, cabecera)]
        self.invoked: list[str] = []

    def descendants(self, hwnd, control_type=None):
        if not self.panels:
            return [_el(f"{_DESK}.m_desktopFrame.{b}")
                    for b in ("IPConfigBtn", "CommandPromptBtn", "EmailBtn", "FirewallBtn")]
        out = []
        for path, title, (frame, label, close) in self.panels:
            base = f"{_DESK}.CDesktopApplet.{path}.{frame}"
            out += [_el(base), _el(f"{base}.{label}", title), _el(f"{base}.{close}", "close")]
        return out

    def invoke(self, el):
        self.invoked.append(el.automation_id.rsplit(".", 1)[-1])
        if el.automation_id.endswith(("m_closeButton", "m_closeBtn")):
            innermost = self.panels[-1][0]
            if f"CDesktopApplet.{innermost}." in el.automation_id:
                self.panels.pop()


_CMD = ("CWorkstationCommandPromptBase", "Command Prompt ", _BAR)
_FIREWALL = ("CPcFirewall", "Firewall", _FRAME)
_MAIL = ("CBaseWorkstationMailBrowser", "MAIL BROWSER", _FRAME)
_MAIL_CFG = ("CBaseWorkstationMailBrowser.BaseWorkstationMailConfiguration", "Configure Mail", _FRAME)


class TestOpenApp:
    def _open(self, u, app, **kw):
        from src.packet_tracer_mcp.infrastructure.ui.presenter import Presenter
        return Presenter(lambda js, t: None, sleep=lambda s: None)._open_app(u, 1, app, **kw)

    def test_clean_desktop(self):
        u = FakeDesktopUia()
        self._open(u, "email")
        assert u.invoked == ["EmailBtn"]

    def test_closes_command_prompt_first(self):
        u = FakeDesktopUia([_CMD])
        self._open(u, "firewall")
        assert u.invoked == ["m_closeButton", "FirewallBtn"]

    def test_closes_firewall_first(self):
        # Antes: la cabecera m_titleFrame no se reconocía → "no tiene la app 'email'".
        u = FakeDesktopUia([_FIREWALL])
        self._open(u, "email")
        assert u.invoked == ["m_closeBtn", "EmailBtn"]

    def test_closes_nested_email_panels_inside_out(self):
        u = FakeDesktopUia([_MAIL, _MAIL_CFG])
        self._open(u, "command_prompt")
        assert u.invoked == ["m_closeBtn", "m_closeBtn", "CommandPromptBtn"]
        assert u.panels == []

    def test_email_already_open(self):
        u = FakeDesktopUia([_MAIL, _MAIL_CFG])
        assert "already open" in self._open(u, "email")
        assert u.invoked == []

    def test_reopen_closes_and_opens_again(self):
        u = FakeDesktopUia([_FIREWALL])
        self._open(u, "firewall", reopen=True)
        assert u.invoked == ["m_closeBtn", "FirewallBtn"]


# ---------------------------------------------------------------------------
# Validación y JS
# ---------------------------------------------------------------------------

class TestPanelRules:
    @pytest.mark.parametrize("raw,mask", [
        ("24", "255.255.255.0"), ("/30", "255.255.255.252"),
        ("255.255.0.0", "255.255.0.0"),
    ])
    def test_mask_forms(self, raw, mask):
        assert normalize_mask(raw) == mask

    @pytest.mark.parametrize("raw", ["33", "255.0.255.0", "abc", ""])
    def test_bad_masks(self, raw):
        assert normalize_mask(raw) is None

    @pytest.mark.parametrize("raw,mask", [
        ("/1", "128.0.0.0"), ("32", "255.255.255.255"), ("8", "255.0.0.0"),
        ("255.255.255.255", "255.255.255.255"), ("128.0.0.0", "128.0.0.0"),
        (" 255.255.255.252 ", "255.255.255.252"),
    ])
    def test_prefix_and_dotted_edges_still_accepted(self, raw, mask):
        assert normalize_mask(raw) == mask

    @pytest.mark.parametrize("raw", [
        "0.0.0.255",    # a wildcard (ACL-style) mask, not a netmask
        "0.0.255.255",  # same
        "0", "/0",      # /0 is never a host or pool mask
        "0.0.0.0",
        "²",       # superscript two: str.isdigit() is True, int() raises
        "٣",       # Arabic-Indic three: a non-ASCII decimal digit
        "2⁴",      # mixed ASCII and superscript digits
    ])
    def test_wildcard_zero_and_non_ascii_masks_are_rejected(self, raw):
        assert normalize_mask(raw) is None

    @pytest.mark.parametrize("raw", ["²", "0.0.0.255", "0"])
    def test_bad_mask_is_a_validation_error_not_an_exception(self, raw):
        res = validate_host_ip_config("PC0", mode="static", ip="10.0.0.2", mask=raw)
        assert ErrorCode.PANEL_INVALID_IP in {e.code for e in res.errors}

    def test_dhcp_rules_reject_non_ascii_mask_without_raising(self):
        res = validate_dhcp("S0", pool="serverPool", gateway="", dns="", start_ip="",
                            mask="²", max_users=0, tftp="", wlc="", exclude=[])
        assert ErrorCode.PANEL_INVALID_IP in {e.code for e in res.errors}

    def test_static_ok(self):
        assert validate_host_ip_config("PC0", mode="static", ip="10.0.0.2", mask="24",
                                       gateway="10.0.0.1").is_valid

    def test_dhcp_with_fixed_ip_is_contradictory(self):
        res = validate_host_ip_config("PC0", mode="dhcp", ip="10.0.0.2", mask="24")
        assert ErrorCode.PANEL_INVALID_VALUE in {e.code for e in res.errors}

    def test_bad_values(self):
        res = validate_host_ip_config("PC0", ip="10.0.0.300", mask="24", gateway="x",
                                      ipv6_gateway="zz::")
        assert len(res.errors) == 3

    def test_newline_is_rejected(self):
        res = validate_host_ip_config("PC0", gateway="10.0.0.1\n")
        assert ErrorCode.PANEL_INVALID_CHARS in {e.code for e in res.errors}

    def test_nothing_to_do(self):
        assert not validate_host_ip_config("PC0").is_valid


class TestJs:
    @pytest.mark.parametrize("build", [
        lambda d: host_ip_config_js(d, "", mode="static", ip="1.1.1.1", mask="255.0.0.0"),
        lambda d: read_device_panel_js(d),
        lambda d: remove_module_js(d, "0/0"),
        lambda d: device_state_js(d),
        lambda d: show_dialog_js(d, True),
        lambda d: center_on_js(d),
    ])
    @pytest.mark.parametrize("evil", ['R1"); evil(); //', "a\nb", "..\\x"])
    def test_builders_escape_and_are_iifes(self, build, evil):
        js = build(evil)
        assert js.startswith("(function(){") and js.endswith("})();")
        assert "\n" not in js
        assert json.dumps(evil)[1:-1] in js  # el nombre va escapado como JSON

    def test_ip_config_uses_documented_setters(self):
        js = host_ip_config_js("PC0", mode="dhcp", gateway="1.1.1.1", dns="8.8.8.8",
                               ipv6_mode="auto")
        for m in ("setDhcpFlag", "setDhcpClientFlag", "dhcpRun", "setDefaultGateway",
                  "setDnsServerIp", "setIpv6AddressAutoConfig"):
            assert m in js

    def test_aaa_process_is_never_probed(self):
        # getProcess("Aaa") lanza "invalid string position" en PT 9.0.1.
        assert '"Aaa"' not in read_device_panel_js("S0")

    def test_console_primes_after_exec_timeout(self):
        # Tras el exec-timeout IOS dice "Press RETURN to get started." (punto).
        assert "started[.!]?" in build_run_js("R1", ["x"], 0)


# ---------------------------------------------------------------------------
# Tools con dobles
# ---------------------------------------------------------------------------

class FakePresenter:
    def __init__(self, host=False, fail=False):
        self.host = host
        self.fail = fail
        self.opened: list[dict] = []
        self.captured: list = []

    def device_info(self, device):
        return {"host": self.host}

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

    def close(self, device=""):
        return "ok"


def _reply_for(js: str) -> str:
    if "var cmds=" in js:
        return json.dumps({"ok": True, "host": False, "primed": [], "results": [
            {"i": 0, "from": 0, "len": 20, "out": "show clock\nnow\nR1#", "cut": False,
             "prompt": "R1#", "mode": "enable", "done": True}]})
    if "var c=" in js and "setIpSubnetMask" in js:
        return json.dumps({"ok": True, "port": "FastEthernet0", "applied": ["static"],
                           "state": {"ip": "10.0.0.2", "mask": "255.255.255.0", "dhcp": False}})
    return json.dumps({"ok": True})


def _tools(tmp_path, presenter, mode="headless", send=None):
    store = UiModeStore(tmp_path, environ={})
    store.set(mode)
    mcp = FastMCP("t")
    sent: list[str] = []

    def fake_send(js, timeout):
        sent.append(js)
        return (send or _reply_for)(js)

    register_device_panel_tools(mcp, send_and_wait=fake_send, check_bridge=lambda: None,
                                store=store, presenter=presenter)

    def call(name, **args):
        res = asyncio.run(mcp.call_tool(name, args))
        blocks = res[0] if isinstance(res, tuple) else res
        return "\n".join(getattr(b, "text", "") for b in blocks)

    return call, sent, store


class TestTools:
    def test_headless_cli_never_touches_the_gui(self, tmp_path):
        p = FakePresenter()
        call, sent, _ = _tools(tmp_path, p)
        out = call("pt_cli", device="R1", commands=["show clock"])
        assert "now" in out and p.opened == []

    def test_ui_mode_opens_the_cli_tab(self, tmp_path):
        p = FakePresenter()
        call, _, _ = _tools(tmp_path, p, mode="ui")
        call("pt_cli", device="R1", commands="show clock")
        assert p.opened[0]["tab"] == "CLI"

    def test_host_goes_to_command_prompt(self, tmp_path):
        p = FakePresenter(host=True)
        call, _, _ = _tools(tmp_path, p)
        call("pt_host_command", device="PC0", command="ipconfig", show=True)
        assert (p.opened[0]["tab"], p.opened[0]["app"]) == ("Desktop", "command_prompt")

    def test_show_false_beats_ui_mode(self, tmp_path):
        p = FakePresenter()
        call, _, _ = _tools(tmp_path, p, mode="ui")
        call("pt_cli", device="R1", commands=["show clock"], show=False)
        assert p.opened == []

    def test_capture_implies_show_and_saves(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        p = FakePresenter()
        call, _, _ = _tools(tmp_path, p)
        out = call("pt_cli", device="R1", commands=["show clock"], capture=True)
        assert p.opened and p.captured and "Capture:" in out

    def test_gui_failure_does_not_lose_the_work(self, tmp_path):
        call, _, _ = _tools(tmp_path, FakePresenter(fail=True), mode="ui")
        out = call("pt_cli", device="R1", commands=["show clock"])
        assert "now" in out and "could not show it" in out

    def test_invalid_commands_are_rejected_before_pt(self, tmp_path):
        call, sent, _ = _tools(tmp_path, FakePresenter())
        out = call("pt_cli", device="R1", commands=["hostname X\nreload"])
        assert "CONSOLE_INVALID_CHARS" in out and sent == []

    def test_ui_mode_tool_persists(self, tmp_path):
        call, _, store = _tools(tmp_path, FakePresenter())
        call("pt_ui_mode", mode="gui")
        assert store.get() == "ui"
        assert "Current mode: ui" in call("pt_ui_mode")

    def test_ip_config_presents_after_applying_and_reopens(self, tmp_path):
        p = FakePresenter()
        call, sent, _ = _tools(tmp_path, p)
        out = json.loads(call("pt_host_ip_config", device="PC0", mode="static",
                              ip="10.0.0.2", mask="24", show=True))
        assert out["ip"] == "10.0.0.2"
        assert p.opened[0]["app"] == "ip_configuration" and p.opened[0]["reopen"] is True
        assert '"mask": "255.255.255.0"' in sent[0]

    @staticmethod
    def _dhcp_send(before: dict, after_ips: list[str]):
        """Host that had `before` and whose port reads `after_ips` in turn afterwards."""
        reads = {"n": 0}

        def state(ip, dhcp=True):
            return {"ip": ip, "mask": "255.255.255.0", "dhcp": dhcp}

        def send(js):
            if "setIpSubnetMask" in js:
                return json.dumps({"ok": True, "port": "FastEthernet0", "applied": ["dhcp"],
                                   "before": before, "state": state(after_ips[0])})
            reads["n"] += 1
            ip = after_ips[min(reads["n"], len(after_ips) - 1)]
            return json.dumps({"ok": True, "state": state(ip)})
        return send, reads

    @pytest.fixture
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(device_panel_tools.time, "sleep", lambda s: None)

    def test_dhcp_waits_past_the_old_static_address(self, tmp_path, _no_sleep):
        static = {"ip": "192.168.1.50", "mask": "255.255.255.0", "dhcp": False}
        send, reads = self._dhcp_send(static, ["192.168.1.50", "192.168.1.50", "192.168.1.101"])
        call, _, _ = _tools(tmp_path, FakePresenter(), send=send)
        out = json.loads(call("pt_host_ip_config", device="PC0", mode="dhcp", wait_dhcp_s=5))
        assert out["ip"] == "192.168.1.101" and reads["n"] >= 2
        assert "notes" not in out

    def test_stale_static_address_is_not_reported_as_a_lease(self, tmp_path, _no_sleep):
        static = {"ip": "192.168.1.50", "mask": "255.255.255.0", "dhcp": False}
        send, _ = self._dhcp_send(static, ["192.168.1.50"])
        call, _, _ = _tools(tmp_path, FakePresenter(), send=send)
        out = json.loads(call("pt_host_ip_config", device="PC0", mode="dhcp", wait_dhcp_s=0.2))
        assert out["lease"] is False
        assert any("192.168.1.50" in n and "not a DHCP lease" in n for n in out["notes"])

    def test_existing_dhcp_lease_is_accepted_at_once(self, tmp_path, _no_sleep):
        leased = {"ip": "192.168.1.101", "mask": "255.255.255.0", "dhcp": True}
        send, reads = self._dhcp_send(leased, ["192.168.1.101"])
        call, _, _ = _tools(tmp_path, FakePresenter(), send=send)
        out = json.loads(call("pt_host_ip_config", device="PC0", mode="dhcp", wait_dhcp_s=5))
        assert out["ip"] == "192.168.1.101" and reads["n"] == 0
        assert "notes" not in out

    def test_ip_config_js_reads_the_port_before_changing_it(self):
        js = host_ip_config_js("PC0", mode="dhcp")
        assert "before:before" in js
        assert js.index("var before=__port(d,pn);") < js.index("setDhcpFlag")

    @pytest.mark.parametrize("tool,args", [
        ("pt_cli", {"device": "R1", "commands": ["show clock"], "timeout": 1e9}),
        ("pt_host_command", {"device": "PC0", "command": "ping 1.1.1.1", "timeout": 1e9}),
        ("pt_host_ip_config", {"device": "PC0", "mode": "dhcp", "wait_dhcp_s": 1e9}),
    ])
    def test_unbounded_waits_are_rejected_before_pt(self, tmp_path, tool, args):
        call, sent, _ = _tools(tmp_path, FakePresenter())
        out = call(tool, **args)
        assert "Validation failed" in out and sent == []

    def test_ip_config_validation(self, tmp_path):
        call, sent, _ = _tools(tmp_path, FakePresenter())
        out = call("pt_host_ip_config", device="PC0", mode="dhcp", ip="10.0.0.2", mask="24")
        assert "PANEL_INVALID_VALUE" in out and sent == []


def test_screenshot_path_stays_inside(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = screenshot_path("../../etc/passwd", "../out")
    assert p.parent.resolve().is_relative_to(tmp_path.resolve())
    assert p.suffix == ".png"
