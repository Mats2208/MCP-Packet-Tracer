"""Wireless tools: an access point's SSID, broadcast and MAC filter."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ....domain.rules.wireless_rules import normalize_mac, validate_wireless_ap
from ....infrastructure.generator.wireless_js import wireless_ap_js
from ....shared.utils import reply_json
from ..bridge_context import BridgeContext
from ..panel_support import errors_text, parse_panel_reply


def _comparable(field: str, value):
    """A setting in a form that compares equal however PT spells it."""
    if field == "mac_list" and value is not None:
        return sorted(normalize_mac(str(m)) or str(m) for m in value)
    return value


def wireless_ap_report(device: str, requested: dict, data: dict) -> dict:
    """What pt_wireless_ap answers: the state read back, and what moved or did not."""
    before, after = data.get("before") or {}, data.get("after") or {}
    out = {"device": device, "dry_run": bool(data.get("dry_run")), "requested": requested}
    differs = {f: {"from": before.get(f), "to": v} for f, v in requested.items()
               if _comparable(f, before.get(f)) != _comparable(f, v)}
    if out["dry_run"]:
        out["would_change"] = differs
        out["state"] = before
        out["summary"] = (f"{device}: dry run, {len(differs)} setting(s) would change"
                          + (f" ({', '.join(differs)})." if differs else "."))
        return out
    not_applied = [f for f, v in requested.items()
                   if _comparable(f, after.get(f)) != _comparable(f, v)]
    out["applied"] = data.get("applied", [])
    out["changed"] = [f for f in requested if _comparable(f, before.get(f)) != _comparable(f, after.get(f))]
    out["state"] = after
    if data.get("failed"):
        out["failed"] = data["failed"]
    if not requested:
        out["summary"] = f"{device}: SSID '{after.get('ssid')}' (read only, nothing requested)."
    elif not_applied:
        out["not_applied"] = not_applied
        out["summary"] = (f"⚠ {device}: {', '.join(not_applied)} did not read back as requested.")
    else:
        reset = " Associations reset: clients re-associate against the new settings." \
            if "reset_associations" in out["applied"] else ""
        out["summary"] = f"{device}: SSID '{after.get('ssid')}', settings read back as requested.{reset}"
    return out


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""

    @mcp.tool()
    def pt_wireless_ap(
        device: str,
        ssid: str = "",
        broadcast: bool | None = None,
        mac_filter: bool | None = None,
        allow_listed: bool | None = None,
        mac_list: list[str] | None = None,
        reset_associations: bool = True,
        dry_run: bool = False,
    ) -> str:
        """
        An access point's wireless settings (AccessPoint-PT Config > Port 1),
        applied and read back. With no settings it only reads.

        Parameters (empty/None = leave alone):
        - ssid: network name (up to 32 bytes). Clients on another SSID drop
          off; giving each AP its own SSID keeps clients off the APs you rename.
        - broadcast: SSID broadcast on/off.
        - mac_filter: MAC filtering on/off.
        - allow_listed: True = only listed MACs may associate, False = listed
          MACs are denied.
        - mac_list: REPLACES the filter list ("0001.63BE.8468", "00:01:63:BE:84:68").
        - reset_associations: after a change, drop every client so they
          re-associate against the new settings (default True; without it,
          clients already associated may keep the old association).
        - dry_run: only read and report what would change.

        A dropped client's port still reads up/linked: confirm with a ping
        (pt_host_command) or its address, not the port state. WPA/WEP are not
        settable: clients need a matching profile, and PT's client profile API
        is broken.
        """
        macs = None if mac_list is None else [normalize_mac(str(m)) or str(m) for m in mac_list]
        v = validate_wireless_ap(device, ssid=ssid, mac_list=mac_list)
        if not v.is_valid:
            return errors_text(v)
        err = ctx.check_bridge()
        if err:
            return err
        requested = {k: val for k, val in (
            ("ssid", ssid or None), ("broadcast", broadcast), ("mac_filter", mac_filter),
            ("allow_listed", allow_listed), ("mac_list", macs)) if val is not None}
        js = wireless_ap_js(device, ssid=ssid, broadcast=broadcast, mac_filter=mac_filter,
                            allow_listed=allow_listed, mac_list=macs,
                            reset_associations=reset_associations, dry_run=dry_run)
        data, error = parse_panel_reply(ctx.send_and_wait(js, timeout=10.0), device)
        if error:
            return error
        return reply_json(wireless_ap_report(device, requested, data))
