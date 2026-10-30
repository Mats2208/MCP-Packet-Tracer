"""Expansion-module tools: list, add, batch install."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....infrastructure.generator.module_js import (
    module_change_js, parse_module_change, power_cycle_notes,
)
from ..bridge_context import BridgeContext
from ....infrastructure.catalog.modules import ALL_MODULES, resolve_module, ports_for_slot
from ....shared.utils import reply_json


def module_target_error(spec, device_name: str, devices: list[dict]) -> str | None:
    """Why `spec` cannot go on `device_name` given the live devices, or None if it can."""
    target = next((d for d in devices if d.get("name") == device_name), None)
    if target is None:
        names = sorted({d.get("name", "") for d in devices if d.get("name")})
        return (
            f"Error: device '{device_name}' does not exist in PT.\n"
            f"Current devices: {', '.join(names) or '(none)'}"
        )
    target_model = target.get("model", "") or ""
    if spec.compatible_with and target_model and target_model not in spec.compatible_with:
        return (
            f"Error: module '{spec.name}' is not compatible with model '{target_model}'.\n"
            f"Compatible with: {', '.join(spec.compatible_with)}"
        )
    return None


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge
    _query_pt_devices = ctx.live_devices

    # ------------------------------------------------------------------
    # MODULES — install expansion modules on live devices
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_list_modules(
        router_model: str = "",
        category: str = "",
    ) -> str:
        """
        Lists the expansion modules available in the PT catalog.

        Without filters it returns ALL modules. Useful to find the exact
        names before calling pt_add_module.

        Parameters:
        - router_model: if given (e.g. "2911", "ISR4321"), filters to
          modules compatible with that router. Includes generic modules
          (no compatible_with list) and the ones that list that model.
        - category: filters by category (e.g. "router_hwic", "router_nm",
          "router_nim", "router_wic"). Empty = all.

        Returns JSON with: name, description, category, ports_added,
        compatible_with.
        """
        rm = (router_model or "").strip()
        cat = (category or "").strip().lower()

        items = []
        for mod in ALL_MODULES.values():
            if cat and mod.category.lower() != cat:
                continue
            if rm and mod.compatible_with and rm not in mod.compatible_with:
                continue
            items.append({
                "name": mod.name,
                "description": mod.description,
                "category": mod.category,
                "module_type": mod.module_type,
                "ports_added": list(mod.ports_added),
                "compatible_with": list(mod.compatible_with) if mod.compatible_with else "any",
            })

        items.sort(key=lambda x: (x["category"], x["name"]))
        return reply_json({
            "count": len(items),
            "filter": {"router_model": rm or None, "category": cat or None},
            "modules": items,
        })

    @mcp.tool()
    def pt_add_module(
        device_name: str,
        slot: str,
        module_name: str,
        dry_run: bool = False,
    ) -> str:
        """
        Installs an expansion module on a device in the active topology.

        PT powers the device off, installs the module and powers it back on (with
        skipBoot). A restart reloads the startup-config, so routers/switches are saved
        first (write memory); the reply says whether the device restarted and was saved.

        Parameters:
        - device_name: exact device name in PT (e.g. "R1"). Use
          pt_query_topology to list valid names.
        - slot: the slot identifier as a STRING. The format depends on the
          device's slot type:
            * HWIC on 2911/2901 → "0/0".."0/3" · on 1941 ONLY "0/0" and "0/1"
              (verified against PT 9.0.1: the 1941 has 2 slots, not 4)
              (chassis-slot/hwic-subslot)
            * NM on 2811/2620XM/Router-PT → "1"
            * NIM on ISR4321/4331   → "0/1", "0/2"  (chassis/subslot — NOT "0"/"1")
            * Cloud-PT/Server-PT/PCs → "0", "1", ... depending on the available slot
          An integer is accepted too and converted to a string.
        - module_name: exact module name, e.g. "HWIC-2T", "NM-4A/S",
          "NIM-2T", "HWIC-1GE-SFP". Use pt_list_modules to discover them.
        - dry_run: if True, validates and returns the JS payload without sending it.

        Example: add 2 serial ports to R1 in HWIC slot 0:
          pt_add_module(device_name="R1", slot="0/0", module_name="HWIC-2T")
        """
        # Coerce slot to a string (accepts int for compatibility) and check it is not empty
        if isinstance(slot, bool) or slot is None:
            return f"Error: invalid slot (received: {slot!r})."
        slot_s = str(slot).strip()
        if not slot_s:
            return "Error: slot cannot be empty."

        # Validate the module name
        spec = resolve_module(module_name)
        if not spec:
            return (
                f"Error: module '{module_name}' not found in the catalog.\n"
                f"Call pt_list_modules to see the valid names."
            )

        # One generator does the power-cycle for every module tool (module_js.py):
        # it saves IOS running-configs first, since the restart reloads startup-config.
        js = module_change_js("add", [{"device": device_name, "slot": slot_s, "module": spec.name}])
        # Ports carry the slot in their name, so they have to be computed
        # for THIS slot: the catalog lists them for the family's first slot.
        slot_ports = ports_for_slot(spec, slot_s)
        ports_added = ", ".join(slot_ports) if slot_ports else "(no ports)"

        if dry_run:
            # Same checks as the real call when PT is reachable; offline, only the catalog.
            devices = [] if _check_bridge() else _query_pt_devices()
            if devices:
                err = module_target_error(spec, device_name, devices)
                if err:
                    return err
            return reply_json({
                "summary": f"[dry_run] Payload generated to install {spec.name} on {device_name} slot {slot_s}.",
                "device": device_name,
                "slot": slot_s,
                "module": spec.name,
                "description": spec.description,
                "ports_added": slot_ports,
                "compatible_with": list(spec.compatible_with) if spec.compatible_with else "any",
                "js_payload": js,
                "sent": False,
                "dry_run": True,
            })

        # Check bridge + PT
        err = _check_bridge()
        if err:
            return err

        # Check the device exists and validate compatibility
        devices = _query_pt_devices()
        if devices:
            err = module_target_error(spec, device_name, devices)
            if err:
                return err

        result = _bridge_send_and_wait(js, timeout=15.0)

        if result is None:
            return (
                f"No answer from PT (timeout). Possible causes:\n"
                f"  - The module is still being installed (the power cycle can take a while)\n"
                f"  - The module name does not exist in PT's allModuleTypes\n"
                f"  - Slot '{slot_s}' is already taken or does not exist\n"
                f"Check by hand with pt_query_topology."
            )

        data = parse_module_change(result)
        if data is None:
            return f"Unexpected reply from PT: {result}"
        success = any(r.get("ok") for r in data["results"])
        notes = "".join(f"\n  {n}" for n in power_cycle_notes(data.get("devices", [])))

        if success:
            return (
                f"Module installed on {device_name}.\n"
                f"  Slot: {slot_s}\n"
                f"  Module: {spec.name} — {spec.description}\n"
                f"  Ports added: {ports_added}"
                + notes
            )
        return (
            f"PT refused to install '{spec.name}' on {device_name} slot '{slot_s}'.\n"
            f"Usual causes:\n"
            f"  - Slot taken by another module\n"
            f"  - Module not compatible with the device's model\n"
            f"  - Slot out of range or in the wrong format (HWIC: '0/0', NM: '1', NIM: '0/1')"
            + notes
        )

    @mcp.tool()
    def pt_install_modules_batch(
        modules: list[dict],
        dry_run: bool = False,
    ) -> str:
        """
        Installs N modules in a single runCode JS — save → power-off → addModule×N → power-on.

        Useful when several serial modules (HWIC-2T, NIM-2T, etc.) have to go into
        several routers at once. PREFER this tool over multiple calls to
        pt_add_module: each individual power cycle can pause PT's script engine
        for > 5s and kill the bridge bootstrap's polling.

        Parameters:
        - modules: list of dicts with {device, slot, module}. Example for RTR-4 with
          4 serial ports on a 2911 (which does NOT accept NM-4A/S):
            [
              {"device": "RTR-4", "slot": "0/0", "module": "HWIC-2T"},
              {"device": "RTR-4", "slot": "0/1", "module": "HWIC-2T"}
            ]
          → gives Serial0/0/0..0/0/1, Serial0/1/0..0/1/1.
        - dry_run: if True, validates and returns the JS payload without sending it.

        Slot rules (string):
          HWIC on 2911/2901 → "0/0".."0/3" · on 1941 ONLY "0/0" and "0/1"
          NIM on ISR4321/4331    → "0/1", "0/2"   (chassis/subslot — NOT "0"/"1")
          NM on 2811/Router-PT    → "1"
          Cloud-PT / hosts        → "0".."7"

        Returns JSON with summary and per-module status (installed or not).
        The JS (js_payload) is only included with dry_run; once sent it is dropped.
        """
        if not isinstance(modules, list) or not modules:
            return json.dumps({"error": "modules must be a non-empty list of {device, slot, module}."})

        # Validate each entry against the catalog
        validated = []
        errors = []
        for idx, entry in enumerate(modules):
            if not isinstance(entry, dict):
                errors.append(f"[{idx}] is not a dict")
                continue
            dev = entry.get("device")
            slot = entry.get("slot")
            mod = entry.get("module")
            if not dev or not isinstance(dev, str):
                errors.append(f"[{idx}] device required (str)")
                continue
            if slot is None or isinstance(slot, bool):
                errors.append(f"[{idx}] slot required")
                continue
            slot_s = str(slot).strip()
            if not slot_s:
                errors.append(f"[{idx}] empty slot")
                continue
            if not mod or not isinstance(mod, str):
                errors.append(f"[{idx}] module required (str)")
                continue
            spec = resolve_module(mod)
            if not spec:
                errors.append(f"[{idx}] module '{mod}' does not exist (use pt_list_modules)")
                continue
            validated.append({
                "device": dev, "slot": slot_s,
                "module": spec.name,
                # Per slot, not from the catalog: two HWIC-2T in "0/0" and "0/1" give
                # different ports, and both used to be reported as 0/0.
                "ports_added": ports_for_slot(spec, slot_s),
                "compatible_with": list(spec.compatible_with) if spec.compatible_with else None,
            })

        if errors:
            return reply_json({
                "error": "Validation failed",
                "details": errors,
            })

        # Build a single one-liner JS: power-off of the unique devices → addModule × N → power-on
        unique_devs = []
        seen = set()
        for v in validated:
            if v["device"] not in seen:
                seen.add(v["device"])
                unique_devs.append(v["device"])

        # One power-cycle per device around all its modules (module_js.py), with the
        # IOS running-config saved first.
        js = module_change_js("add", validated)

        summary = {
            "total_modules": len(validated),
            "devices_affected": unique_devs,
            "modules": validated,
            "js_payload": js,
            "dry_run": dry_run,
            "sent": False,
        }

        if dry_run:
            summary["summary"] = f"[dry_run] {len(validated)} module(s) on {len(unique_devs)} device(s)."
            return reply_json(summary)

        err = _check_bridge()
        if err:
            return err

        # Check the devices exist + validate module compatibility
        pt_devices = _query_pt_devices()
        if pt_devices:
            by_name = {d.get("name"): d for d in pt_devices}
            for v in validated:
                if v["device"] not in by_name:
                    return f"Error: device '{v['device']}' does not exist in PT."
                if v["compatible_with"]:
                    target_model = by_name[v["device"]].get("model", "") or ""
                    if target_model and target_model not in v["compatible_with"]:
                        return (
                            f"Error: module '{v['module']}' is not compatible with model "
                            f"'{target_model}' (device '{v['device']}').\n"
                            f"Compatible with: {', '.join(v['compatible_with'])}"
                        )

        # The result is awaited: the JS reports before powering on, so the
        # power-on — what used to force fire-and-forget — no longer counts.
        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            summary["sent"] = True
            summary["verified"] = False
            summary["summary"] = (
                f"Batch sent ({len(validated)} module(s)) but PT did not confirm in time.\n"
                "Check with pt_query_topology which ones were installed."
            )
            return reply_json(summary)

        data = parse_module_change(raw)
        if data is None:
            summary["sent"] = True
            summary["verified"] = False
            summary["summary"] = f"Unexpected reply from PT: {raw}"
            return reply_json(summary)

        # A slot that doesn't exist on the model returns false without throwing,
        # so without the per-module status phantom ports were reported.
        status = {(r.get("device"), r.get("slot")): bool(r.get("ok")) for r in data["results"]}
        failed = []
        for v in validated:
            v["installed"] = status.get((v["device"], v["slot"]), False)
            if not v["installed"]:
                v["ports_added"] = []
                failed.append(f"{v['device']} slot {v['slot']} ({v['module']})")
        summary["power_cycle"] = power_cycle_notes(data.get("devices", []))

        summary["sent"] = True
        summary["verified"] = True
        summary["installed_count"] = sum(1 for v in validated if v.get("installed"))
        if failed:
            summary["failed"] = failed
            summary["summary"] = (
                f"{summary['installed_count']}/{len(validated)} module(s) installed. "
                f"PT refused: {', '.join(failed)}.\n"
                "That slot does not exist on that model — check pt_list_modules and the "
                "correct slot for the router's family."
            )
            return reply_json(summary)

        summary["summary"] = (
            f"Batch sent: {len(validated)} module(s) on {len(unique_devs)} device(s).\n"
            f"PT saved, powered off, installed and powered back on in one step (see power_cycle). "
            f"Check with pt_query_topology or by querying getPorts() on each router."
        )
        return reply_json(summary)
