"""Security audit tool: real posture read from the live devices."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....domain.services.security_audit import audit_security
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....shared.utils import reply_json


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge

    # ------------------------------------------------------------------
    # SECURITY AUDIT — real posture read from the live devices
    # ------------------------------------------------------------------

    # Classifies each credential by its prefix and returns ONLY the algorithm
    # label. The hash never crosses the bridge: it would end up in the LLM's context
    # and in the MCP client's logs, and the label is enough to audit.
    # Verified against PT 9.0.0.0810: `enable secret`/`username X secret` give
    # "$1$...", and `username X password` with service-password-encryption gives hex
    # type-7 (reversible with public decoders).
    _AUDIT_ALGO_JS = (
        "function __algo(s){"
        "  if(!s) return null;"
        "  s = String(s);"
        "  if(s.indexOf('$1$')===0) return 'md5';"
        "  if(s.indexOf('$8$')===0) return 'pbkdf2';"
        "  if(s.indexOf('$9$')===0) return 'scrypt';"
        "  if(/^[0-9A-Fa-f]{4,}$/.test(s)) return 'type7';"
        "  return 'plaintext';"
        "}"
    )

    _SECURITY_AUDIT_JS = (
        "try {"
        + _AUDIT_ALGO_JS +
        "  var __net = ipc.network();"
        "  var __out = [];"
        "  var __n = __net.getDeviceCount();"
        "  for (var __i = 0; __i < __n; __i++) {"
        "    try {"
        "      var __d = __net.getDeviceAt(__i);"
        # Hosts (PC/Server/Laptop) expose no IOS configuration: calling
        # these getters there throws and opens a modal that freezes the bridge.
        "      if (!__d || typeof __d.getEnableSecret !== 'function') continue;"
        "      var __users = [];"
        "      try {"
        "        var __uc = __d.getUserPassCount();"
        "        for (var __j = 0; __j < __uc; __j++) {"
        # getUserEntryAt throws 'out of bound' instead of returning null, so
        # each read carries its own guard.
        "          try {"
        "            var __u = String(__d.getUserEntryAt(__j));"
        "            __users.push({ name: __u, algo: __algo(__d.getUserPass(__u)) });"
        "          } catch (__ue) {}"
        "        }"
        "      } catch (__uce) {}"
        "      var __sec = __d.getEnableSecret();"
        "      var __pwd = __d.getEnablePassword();"
        "      __out.push({"
        "        name: __d.getName(),"
        "        model: (typeof __d.getModel === 'function') ? __d.getModel() : '',"
        "        hostname: (typeof __d.getHostName === 'function') ? __d.getHostName() : '',"
        "        enable_secret_set: !!__sec,"
        "        enable_secret_algo: __algo(__sec),"
        "        enable_password_set: !!__pwd,"
        "        service_password_encryption: (typeof __d.getServicePasswordEncryption === 'function')"
        "          ? !!__d.getServicePasswordEncryption() : false,"
        "        banner_set: (typeof __d.getBannerMotd === 'function')"
        "          ? !!__d.getBannerMotd() : false,"
        "        users: __users,"
        "        config_register: (typeof __d.getConfigRegister === 'function')"
        "          ? __d.getConfigRegister() : null"
        "      });"
        "    } catch (__pe) {}"
        "  }"
        "  reportResult(JSON.stringify({ devices: __out }));"
        "} catch (__e) { reportResult('ERROR:' + __e); }"
    )

    @mcp.tool()
    def pt_audit_security(device: str = "") -> str:
        """
        Audits the REAL security posture of the live devices in PT.

        It does not read the plan: it reads the effective configuration of each router/switch
        on the canvas and reports findings with a severity (high/medium/low). Detects
        a missing `enable secret`, credentials stored reversibly
        (type 7), `service password-encryption` off, no local
        users, a missing MOTD banner and config-register at 0x2142 (which discards
        the startup-config on the next reboot).

        Passwords and hashes NEVER leave the device: only the label of the
        algorithm they are stored with is transmitted.

        Hosts (PC/Server/Laptop) are skipped: they have no IOS configuration.

        Parameters:
        - device: if given, audits only that device; empty = all of them.

        Example: audit the whole topology:
          pt_audit_security()
        """
        err = _check_bridge()
        if err:
            return err

        raw = _bridge_send_and_wait(_SECURITY_AUDIT_JS, timeout=12.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"

        try:
            devices = json.loads(raw).get("devices", [])
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        wanted = device.strip()
        if wanted:
            devices = [d for d in devices if d.get("name") == wanted]
            if not devices:
                return (
                    f"'{wanted}' does not exist in the active topology or has no "
                    "IOS configuration (PCs and servers don't have one). "
                    "Use pt_query_topology to see the real names."
                )

        result = audit_security(devices)
        counts = result["counts"]
        if not devices:
            result["summary"] = "There are no devices with an IOS configuration on the canvas."
        elif result["secure"]:
            result["summary"] = (
                f"✅ {result['devices_audited']} device(s) audited, "
                f"no high or medium findings ({counts['low']} low)."
            )
        else:
            result["summary"] = (
                f"⚠ {counts['high']} high finding(s), {counts['medium']} medium, "
                f"{counts['low']} low on {result['devices_audited']} device(s)."
            )
        return reply_json(result)
