"""EMAIL service: Domain Name and SMTP/POP3 on/off through pt_server_service, no PT.

The repo said EMAIL had no domain or on/off API. Measured on PT 9.0
(2026-10-30): getProcess("SmtpServer") has getServerDomainName ("" by default),
setServerDomainName("mcp.test") and isEnabled; getProcess("Pop3Server") has
isEnabled. Both take setEnable(bool).
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.adapters.mcp.desktop_service_tools import email_not_applied
from src.packet_tracer_mcp.domain.rules.service_rules import validate_email_server
from src.packet_tracer_mcp.infrastructure.generator.host_js import read_device_panel_js
from src.packet_tracer_mcp.infrastructure.generator.service_js import server_service_js

from tests.test_desktop_services import _tools


class TestRules:
    def test_valid(self):
        assert validate_email_server("S0", "email", domain="lab.com", smtp=True, pop3=False).is_valid

    @pytest.mark.parametrize("domain", ["lab .com", "-lab.com", "lab..com", "lab.com\n", "a@b.com"])
    def test_bad_domain(self, domain):
        assert not validate_email_server("S0", "email", domain=domain).is_valid

    def test_only_on_email(self):
        assert not validate_email_server("S0", "ftp", domain="lab.com").is_valid
        assert not validate_email_server("S0", "ftp", pop3=True).is_valid
        assert validate_email_server("S0", "ftp").is_valid


class TestJs:
    def test_uses_the_measured_processes(self):
        js = server_service_js("S0", "email", domain="lab.com", smtp=True, pop3=False)
        for call in ("getProcess('SmtpServer')", "getProcess('Pop3Server')",
                     "sm.setServerDomainName(c.domain)", "sm.setEnable(!!c.smtp)",
                     "p3.setEnable(!!c.pop3)", "sm.getServerDomainName()", "p3.isEnabled()"):
            assert call in js
        assert '"domain": "lab.com", "smtp": true, "pop3": false' in js

    def test_domain_travels_through_json(self):
        js = server_service_js("S0", "email", domain="x');alert(1);//..")
        assert '"domain": "x\');alert(1);//.."' in js

    def test_other_services_do_not_touch_email_processes(self):
        js = server_service_js("S0", "ftp", enable=True)
        assert '"domain": null, "smtp": null, "pop3": null' in js

    def test_panel_reports_smtp_and_pop3(self):
        js = read_device_panel_js("S0")
        assert '"SmtpServer"' in js and '"Pop3Server"' in js


def test_not_applied_compares_only_what_was_asked():
    data = {"domain": "lab.com", "smtp": True, "pop3": True}
    assert email_not_applied(data, domain="lab.com", smtp=True) == []
    assert email_not_applied(data, pop3=False) == ["pop3"]
    assert email_not_applied({"domain": ""}, domain="lab.com") == ["domain"]


class TestTool:
    def _reply(self, js):
        return json.dumps({"ok": True, "service": "email", "done": ["SMTP On", "POP3 On"],
                           "enabled": True, "accounts": "", "smtp": True, "pop3": True,
                           "domain": "lab.com"})

    def test_enable_switches_both_protocols(self, tmp_path):
        call, sent = _tools(tmp_path, reply=self._reply)
        out = json.loads(call("pt_server_service", device="S0", service="email", enable=True,
                              domain="lab.com"))
        assert '"smtp": true, "pop3": true' in sent[0]
        assert out["domain"] == "lab.com" and "not_applied" not in out

    def test_explicit_switch_wins_over_enable(self, tmp_path):
        call, sent = _tools(tmp_path, reply=self._reply)
        out = json.loads(call("pt_server_service", device="S0", service="email", enable=True,
                              pop3=False))
        assert '"smtp": true, "pop3": false' in sent[0]
        assert out["not_applied"] == ["pop3"]

    def test_domain_on_another_service_never_reaches_pt(self, tmp_path):
        call, sent = _tools(tmp_path)
        out = call("pt_server_service", device="S0", service="tftp", domain="lab.com")
        assert "Validation failed" in out and sent == []
