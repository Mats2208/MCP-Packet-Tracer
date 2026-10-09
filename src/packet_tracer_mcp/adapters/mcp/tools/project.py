"""Project tools: config backup, project metadata, workspace options."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ..bridge_context import BridgeContext, TIMEOUT_MSG


# Workspace options: MCP flag → (PT method, is it inverted?).
#
# PT exposes two of these in the NEGATIVE (`setDisableAutoCabling`,
# `setHideDevLabel`) while the MCP flag says "enable/show". If the
# inversion is lost, the tool does exactly the opposite of what it is
# asked, silently.
WORKSPACE_SETTERS: dict[str, tuple[str, bool]] = {
    "auto_cabling":            ("setDisableAutoCabling", True),
    "show_device_labels":      ("setHideDevLabel", True),
    "external_network_access": ("setEnableExternalNetworkAccess", False),
    "show_port_labels":        ("setIsPortShown", False),
    "show_link_lights":        ("setIsLinkLightShown", False),
}


# Setters that PT declares with a MANDATORY second argument. With only one
# it answers `Invalid arguments for IPC call "..."`. The second value does
# not change the result (tested with true and false against PT 9.0.1), but it
# has to be there.
WORKSPACE_EXTRA_ARG: dict[str, str] = {
    "setHideDevLabel": "true",
}


def workspace_setter_call(flag: str, value: int) -> tuple[str, str]:
    """(PT method, JS arguments) to set `flag` to `value` (0 or 1)."""
    method, inverted = WORKSPACE_SETTERS[flag]
    on = "true" if (value == 1) != inverted else "false"
    extra = WORKSPACE_EXTRA_ARG.get(method)
    return method, (f"{on}, {extra}" if extra else on)


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge

    # ------------------------------------------------------------------
    # Project BACKUP and METADATA
    # ------------------------------------------------------------------

    # The startup-config comes back with the lines separated by COMMAS, not by
    # newlines. Rebuilding it is what makes it pasteable into a CLI.
    _MAX_BACKUP_XML = 200_000

    @mcp.tool()
    def pt_backup_config(device: str, include_xml: bool = False) -> str:
        """
        Backs up a PT device's startup configuration.

        Returns the real startup-config (the one the device rereads on restart),
        plus its serial number, config-register, boot images and uptime.
        Use it to save a known state before touching something, or to
        compare two devices.

        Parameters:
        - device: name of the router or switch in PT.
        - include_xml: if True, adds the device's full XML dump
          (topology + config + modules). It is tens of thousands of characters:
          useful for archiving, heavy to read.

        Example: pt_backup_config(device="R1")
        """
        err = _check_bridge()
        if err:
            return err

        dev = json.dumps(device.strip())
        want_xml = "true" if include_xml else "false"
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({dev});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.getStartupFile !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __out = { found: true, supported: true,"
            "      startup: String(__d.getStartupFile() || ''),"
            "      model: (typeof __d.getModel === 'function') ? __d.getModel() : '',"
            "      hostname: (typeof __d.getHostName === 'function') ? __d.getHostName() : '',"
            "      serial: (typeof __d.getSerialNumber === 'function') ? __d.getSerialNumber() : '',"
            "      config_register: (typeof __d.getConfigRegister === 'function')"
            "        ? __d.getConfigRegister() : null,"
            "      uptime: (typeof __d.getUpTime === 'function') ? __d.getUpTime() : null,"
            "      boot_systems: (typeof __d.getBootSystems === 'function')"
            "        ? String(__d.getBootSystems() || '') : '' };"
            f"    if ({want_xml} && typeof __d.serializeToXml === 'function') {{"
            f"      __out.xml = String(__d.serializeToXml() || '').substring(0, {_MAX_BACKUP_XML});"
            "    }"
            "    reportResult(JSON.stringify(__out));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            return f"'{device}' has no startup-config (PT's hosts don't have one)."

        startup = data.pop("startup", "")
        lines = [ln for ln in startup.split(",") if ln != ""]
        data["startup_config"] = "\n".join(lines)
        data["startup_lines"] = len(lines)
        if not lines:
            data["summary"] = (
                f"'{device}' has no saved startup-config. "
                "Run `write memory` on the device before backing it up."
            )
        else:
            data["summary"] = (
                f"{len(lines)} line(s) of startup-config from '{device}' "
                f"({data.get('model')}, serial {data.get('serial')})."
            )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_project_metadata(description: str = "") -> str:
        """
        Reads (and optionally writes) the metadata of the project open in PT.

        Returns the saved file, the PT version that wrote it and the
        project description, together with the device and link counts.
        Useful to know what you are working with before changing anything.

        Parameters:
        - description: if given, REPLACES the project description.
          Empty = read only.

        Example: pt_project_metadata()
        """
        err = _check_bridge()
        if err:
            return err

        new_desc = description.strip()
        setter = (
            f"  if (typeof __f.setNetworkDescription === 'function') "
            f"{{ __f.setNetworkDescription({json.dumps(new_desc)}); }}"
            if new_desc else ""
        )
        js = (
            "try {"
            "  var __a = ipc.appWindow();"
            "  var __f = __a.getActiveFile();"
            "  if (!__f) { reportResult(JSON.stringify({ found: false })); } else {"
            + setter +
            "    var __n = ipc.network();"
            "    reportResult(JSON.stringify({"
            "      found: true,"
            "      saved_filename: String(__f.getSavedFilename() || ''),"
            "      pt_version: String(__f.getVersion() || ''),"
            "      description: String(__f.getNetworkDescription() || ''),"
            "      devices: __n.getDeviceCount(), links: __n.getLinkCount()"
            "    }));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return "PT has no active network file."

        data["updated_description"] = bool(new_desc)
        saved = data.get("saved_filename") or ""
        data["summary"] = (
            f"{data['devices']} device(s), {data['links']} link(s). "
            + (f"File: {saved}." if saved
               else "Project NOT saved — use pt_save_project to persist it.")
            + (" Description updated." if new_desc else "")
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_workspace_options(
        auto_cabling: int = -1,
        external_network_access: int = -1,
        show_port_labels: int = -1,
        show_link_lights: int = -1,
        show_device_labels: int = -1,
    ) -> str:
        """
        Reads and adjusts PT workspace options that affect how it behaves.

        Without arguments it is read-only. The flags are tri-state: 1 enables,
        0 disables, -1 (default) leaves alone.

        Parameters:
        - auto_cabling: PT's auto-cabling picks the cable and the port for you.
          Turn it off before building topologies by script if you want exact
          control over which port is used.
        - external_network_access: lets PT reach the machine's REAL
          network. Off by default; turning it on takes traffic out of the simulator.
        - show_port_labels / show_link_lights / show_device_labels: what is shown on
          the canvas. It matters for a capture to be readable.

        Example: turn auto-cabling off before a scripted deploy:
          pt_workspace_options(auto_cabling=0)
        """
        err = _check_bridge()
        if err:
            return err

        def _opt_set(method: str, args: str) -> str:
            """One isolated setter: if PT rejects it, it falls alone and is reported.

            Each one goes in its own try/catch on purpose. They used to all be
            under the same one, so one failing setter — `setHideDevLabel` with
            the wrong arity — aborted the whole batch and returned a raw
            error, but the earlier ones HAD ALREADY been applied: the user saw
            "failed" with half the changes in place.
            """
            return (
                f"    try {{ if (typeof __o.{method} === 'function')"
                f" {{ __o.{method}({args}); __applied.push('{method}'); }}"
                f" else {{ __failed.push('{method}: does not exist'); }} }}"
                f" catch (__se) {{ __failed.push('{method}: ' + __se); }}"
            )

        # The polarity and arity live in WORKSPACE_SETTERS /
        # WORKSPACE_EXTRA_ARG (module level) so they can be tested without PT.
        sets: list[str] = []
        for flag, value in (
            ("auto_cabling", auto_cabling),
            ("show_device_labels", show_device_labels),
            ("external_network_access", external_network_access),
            ("show_port_labels", show_port_labels),
            ("show_link_lights", show_link_lights),
        ):
            if value in (0, 1):
                sets.append(_opt_set(*workspace_setter_call(flag, value)))

        js = (
            "try {"
            "  var __o = ipc.options();"
            "  var __applied = []; var __failed = [];"
            + "".join(sets) +
            "  reportResult(JSON.stringify({"
            "    applied: __applied, failed: __failed,"
            "    auto_cabling: !__o.isAutoCablingDisabled(),"
            "    external_network_access: !!__o.isExternalNetworkAccessEnabled(),"
            "    show_port_labels: !!__o.isPortShown(),"
            "    show_link_lights: !!__o.isLinkLightsShown(),"
            "    show_device_labels: !__o.isHideDevLabel(),"
            "    using_metric: !!__o.isUsingMetric(),"
            "    language: String(__o.getCurrentLanguage() || ''),"
            "    config_path: String(__o.getConfigFilePath() || '')"
            "  }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        # What PT really accepted, not what was attempted: counting the attempts
        # gave "5 options changed" even when one had been rejected.
        applied = data.get("applied") or []
        failed = data.get("failed") or []
        data["changed"] = len(applied)
        notes = []
        if not data["auto_cabling"]:
            notes.append("auto-cabling OFF (you pick the ports)")
        if data["external_network_access"]:
            notes.append("⚠ access to the REAL network enabled")
        if failed:
            notes.append(f"⚠ {len(failed)} option(s) rejected by PT: {'; '.join(failed)}")
        data["summary"] = (
            f"{len(applied)} option(s) changed. " if sets else "Read only. "
        ) + ("; ".join(notes) if notes else "Default configuration.")
        return json.dumps(data, indent=2, ensure_ascii=False)
