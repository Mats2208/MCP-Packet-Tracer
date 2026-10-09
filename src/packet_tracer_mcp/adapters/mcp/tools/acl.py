"""ACL tools: apply and remove access lists (numbered/named, object groups)."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....domain.models.acls import ACLBinding
from ....application.use_cases.apply_acl import build_acl_plan, apply_acl_uc, remove_acl_uc
from ....infrastructure.generator.acl_cli_generator import generate_acl_cli
from ..bridge_context import BridgeContext


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _pick_channel = ctx.pick_channel
    _bridge_send_and_wait = ctx.send_and_wait
    _query_pt_devices = ctx.live_devices
    _bridge_send_payload = ctx.send_payload


    @mcp.tool()
    def pt_apply_acl(
        router: str,
        name_or_number: str,
        acl_type: str,
        entries: list[dict],
        binding_interface: str = "",
        binding_direction: str = "in",
        dry_run: bool = False,
    ) -> str:
        """
        Applies an Access Control List (ACL) to a router in PT's active topology.

        Pipeline: builds the plan → static validation (ranges, types, IPs/wildcards,
        unreachable rules) → checks router/interface against PT through the bridge →
        generates IOS CLI → sends it through configureIosDevice.

        Parameters:
        - router: device name in PT (e.g. "CORE-R1"). Call
          pt_query_topology if you are not sure of the exact names.
        - name_or_number: the ACL's IOS identifier.
            * 1-99 or 1300-1999 → standard
            * 100-199 or 2000-2699 → extended
            * any alphanumeric string → named ACL
        - acl_type: "standard" or "extended". Standard only filters by source.
          Extended allows source + destination + protocol + ports.
        - entries: list of rules. Each rule is a dict with:
            * action: "permit" | "deny" (required)
            * protocol: "ip" | "icmp" | "tcp" | "udp" | ... (default "ip")
            * source: "any" | "host A.B.C.D" | "A.B.C.D wildcard" (required)
            * destination: same as source (extended only)
            * source_port_op / source_port: e.g. "eq" / 80 (TCP/UDP, optional)
            * dest_port_op / dest_port / dest_port_end: same (optional)
            * icmp_type: "echo" | "echo-reply" | ... (ICMP only)
            * tcp_flags: ["established"] | ["syn"] (TCP only, optional)
            * log: bool (optional)
            * remark: optional comment
        - binding_interface: if given, applies the ACL to that interface
          (e.g. "GigabitEthernet0/0"). If empty, the ACL is only defined, not applied.
        - binding_direction: "in" or "out" (default "in"). Only applies if
          binding_interface is set.
        - dry_run: if True, sends NOTHING to the bridge — only validates and returns
          the CLI/JS payload for inspection.

        Example: block ping from 192.168.1.0/24 to 192.168.0.0/24 on CORE-R1:
          pt_apply_acl(
              router="CORE-R1",
              name_or_number="101",
              acl_type="extended",
              entries=[
                  {"action": "deny", "protocol": "icmp",
                   "source": "192.168.1.0 0.0.0.255",
                   "destination": "192.168.0.0 0.0.0.255",
                   "icmp_type": "echo"},
                  {"action": "permit", "protocol": "ip",
                   "source": "any", "destination": "any"},
              ],
              binding_interface="GigabitEthernet0/0",
              binding_direction="in",
          )
        """
        plan = build_acl_plan(router, name_or_number, acl_type, entries)
        binding = None
        if binding_interface:
            binding = ACLBinding(
                router=router,
                interface=binding_interface,
                acl_id=str(name_or_number),
                direction=binding_direction,
            )

        # Only queries PT if the bridge is connected (dynamic validation)
        bridge_ok = _pick_channel() != ""
        query_fn = _query_pt_devices if bridge_ok else None
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = apply_acl_uc(
            plan=plan,
            binding=binding,
            query_pt_topology=query_fn,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        # Friendly summary
        summary_lines = []
        if result["valid"]:
            summary_lines.append(f"✅ ACL '{plan.name_or_number}' valid ({len(plan.entries)} rules).")
        else:
            summary_lines.append(f"❌ ACL '{plan.name_or_number}' has {len(result['errors'])} error(s).")

        if dry_run:
            summary_lines.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary_lines.append(f"📤 Applied on '{router}' through the bridge (configureIosDevice).")
            if binding:
                summary_lines.append(f"   Binding: {binding.interface} {binding.direction}")
        elif result["valid"] and not bridge_ok:
            summary_lines.append("⚠ Bridge not connected — payload generated but NOT sent.")
        elif result["valid"] and not result["sent"]:
            summary_lines.append("⚠ Bridge OK but sending failed.")

        return json.dumps({
            "summary": "\n".join(summary_lines),
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_apply_acl_object(
        router: str,
        name_or_number: str,
        acl_type: str,
        entries: list[dict],
        binding_interface: str = "",
        binding_direction: str = "in",
        replace_existing: bool = True,
        dry_run: bool = False,
    ) -> str:
        """
        Applies an ACL using PT's object API (AclProcess.addAcl/addStatement)
        instead of CLI through configureIosDevice.

        Same input as pt_apply_acl. It is faster (no CLI parsing) and less
        prone to raising modal popups that break the bridge if a line goes wrong.

        Limitation: binding only works on the catalog's physical ports (e.g.
        GigabitEthernet0/0). For sub-interfaces (G0/0/1.20) use pt_apply_acl (CLI),
        since port.setAclInID only applies to the base port and not the sub-interface.

        Pipeline: validate the plan → generate statements (without the "access-list NAME " prefix)
        → run addAcl + addStatement one by one + optional binding.
        """
        plan = build_acl_plan(router, name_or_number, acl_type, entries)
        binding = None
        if binding_interface:
            binding = ACLBinding(
                router=router,
                interface=binding_interface,
                acl_id=str(name_or_number),
                direction=binding_direction,
            )

        bridge_ok = _pick_channel() != ""

        # Static + topological validation
        query_fn = _query_pt_devices if bridge_ok else None
        result = apply_acl_uc(
            plan=plan,
            binding=binding,
            query_pt_topology=query_fn,
            bridge_send=None,        # we don't send via CLI — we build our own JS
            dry_run=True,            # validate without sending
        )

        if not result["valid"]:
            return json.dumps({
                "summary": f"❌ ACL '{plan.name_or_number}' has {len(result['errors'])} error(s).",
                "valid": False,
                "errors": result["errors"],
                "warnings": result["warnings"],
                "sent": False,
                "dry_run": dry_run,
                "backend": "objects",
            }, indent=2, ensure_ascii=False)

        # Convert CLI lines to statements (without the "access-list NAME " prefix)
        cli_lines = generate_acl_cli(plan)
        prefix = f"access-list {plan.name_or_number} "
        statements = [ln[len(prefix):] for ln in cli_lines if ln.startswith(prefix)]

        # Build the JS for AclProcess.addAcl + addStatement
        name_js = json.dumps(str(plan.name_or_number))
        router_js = json.dumps(router)
        stmts_js = "[" + ",".join(json.dumps(s) for s in statements) + "]"

        js_lines = [
            f"var d=ipc.network().getDevice({router_js});",
            'if(!d){reportResult(JSON.stringify({success:false,error:"router not found"}));return;}',
            'var ap=d.getProcess("AclProcess");',
            'if(!ap){reportResult(JSON.stringify({success:false,error:"AclProcess not available"}));return;}',
        ]
        if replace_existing:
            js_lines.append(f"try{{ap.removeAcl({name_js});}}catch(e){{}}")
        js_lines.extend([
            f"ap.addAcl({name_js});",
            f"var acl=ap.getAcl({name_js});",
            'if(!acl){reportResult(JSON.stringify({success:false,error:"addAcl failed"}));return;}',
            f"var stmts={stmts_js};",
            'var added=0;for(var i=0;i<stmts.length;i++){if(acl.addStatement(stmts[i]))added++;}',
        ])

        bound = "none"
        if binding:
            iface_js = json.dumps(binding.interface)
            setter = "setAclInID" if binding.direction == "in" else "setAclOutID"
            js_lines.extend([
                f"var p=d.getPort({iface_js});",
                f'if(p){{p.{setter}({name_js});}}',
            ])
            bound = f"{binding.interface} {binding.direction}"

        js_lines.append(
            'reportResult(JSON.stringify({success:true,added:added,cmdCount:acl.getCommandCount()}));'
        )

        js = "(function(){" + "".join(js_lines) + "})()"

        payload = {
            "summary": "",
            "valid": True,
            "errors": [],
            "warnings": result["warnings"],
            "cli_lines": cli_lines,
            "statements": statements,
            "js_payload": js,
            "binding": bound,
            "sent": False,
            "dry_run": dry_run,
            "backend": "objects",
        }

        if dry_run:
            payload["summary"] = (
                f"[dry_run] ACL '{plan.name_or_number}' ready: "
                f"{len(statements)} statement(s) + binding={bound}. JS NOT sent."
            )
            return json.dumps(payload, indent=2, ensure_ascii=False)

        if not bridge_ok:
            payload["summary"] = "⚠ Bridge not connected — payload generated but NOT sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        response = _bridge_send_and_wait(js, timeout=10.0)
        if response is None:
            payload["summary"] = "No answer from PT."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        try:
            r = json.loads(response)
            if r.get("success"):
                payload["sent"] = True
                payload["added"] = r.get("added")
                payload["cmd_count"] = r.get("cmdCount")
                payload["summary"] = (
                    f"📤 ACL '{plan.name_or_number}' applied on '{router}' through AclProcess "
                    f"({r.get('added')}/{len(statements)} statements). Binding={bound}."
                )
            else:
                payload["summary"] = f"PT error: {r.get('error', 'unknown')}"
        except Exception:
            payload["summary"] = f"Unexpected reply: {response}"

        return json.dumps(payload, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_remove_acl_object(
        router: str,
        name_or_number: str,
        binding_interface: str = "",
        binding_direction: str = "in",
        dry_run: bool = False,
    ) -> str:
        """
        Removes an ACL using the object API (AclProcess.removeAcl + Port.setAclInID="").

        Alternative to pt_remove_acl (CLI). If binding_interface is given, it
        first clears the port's AclInID/AclOutID and then removes the ACL.

        Parameters:
        - router: device name in PT
        - name_or_number: identifier of the ACL to remove
        - binding_interface: optional, interface where the binding was
        - binding_direction: "in" or "out" (only with binding_interface)
        - dry_run: if True, returns the payload without sending it
        """
        bridge_ok = _pick_channel() != ""

        name_js = json.dumps(str(name_or_number))
        router_js = json.dumps(router)

        js_lines = [
            f"var d=ipc.network().getDevice({router_js});",
            'if(!d){reportResult(JSON.stringify({success:false,error:"router not found"}));return;}',
            'var ap=d.getProcess("AclProcess");',
            'if(!ap){reportResult(JSON.stringify({success:false,error:"AclProcess not available"}));return;}',
        ]

        bound_label = "none"
        if binding_interface:
            iface_js = json.dumps(binding_interface)
            setter = "setAclInID" if binding_direction == "in" else "setAclOutID"
            js_lines.extend([
                f"var p=d.getPort({iface_js});",
                f'if(p){{p.{setter}("");}}',
            ])
            bound_label = f"{binding_interface} {binding_direction}"

        js_lines.extend([
            f"var removed=ap.removeAcl({name_js});",
            'reportResult(JSON.stringify({success:true,removed:removed}));',
        ])

        js = "(function(){" + "".join(js_lines) + "})()"

        payload = {
            "summary": "",
            "router": router,
            "acl_id": str(name_or_number),
            "binding": bound_label,
            "js_payload": js,
            "sent": False,
            "dry_run": dry_run,
            "backend": "objects",
        }

        if dry_run:
            payload["summary"] = (
                f"[dry_run] payload generated to remove ACL '{name_or_number}' "
                f"on '{router}' (binding={bound_label}). NOT sent."
            )
            return json.dumps(payload, indent=2, ensure_ascii=False)

        if not bridge_ok:
            payload["summary"] = "⚠ Bridge not connected — payload generated but NOT sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        response = _bridge_send_and_wait(js, timeout=10.0)
        if response is None:
            payload["summary"] = "No answer from PT."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        try:
            r = json.loads(response)
            if r.get("success"):
                payload["sent"] = True
                payload["removed"] = r.get("removed")
                payload["summary"] = (
                    f"📤 ACL '{name_or_number}' removed on '{router}' through AclProcess "
                    f"(removed={r.get('removed')}, binding={bound_label})."
                )
            else:
                payload["summary"] = f"PT error: {r.get('error', 'unknown')}"
        except Exception:
            payload["summary"] = f"Unexpected reply: {response}"

        return json.dumps(payload, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_remove_acl(
        router: str,
        name_or_number: str,
        binding_interface: str = "",
        binding_direction: str = "in",
        dry_run: bool = False,
    ) -> str:
        """
        Removes an ACL applied on a router.

        If binding_interface is given, it first removes the binding from the
        interface (no ip access-group ...) and then deletes the whole ACL
        (no access-list ...).

        Parameters:
        - router: device name in PT
        - name_or_number: identifier of the ACL to remove
        - binding_interface: optional, interface where it was applied
        - binding_direction: "in" or "out" (only with binding_interface)
        - dry_run: if True, returns the payload without sending it
        """
        bridge_ok = _pick_channel() != ""
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = remove_acl_uc(
            router=router,
            name_or_number=name_or_number,
            binding_interface=binding_interface,
            direction=binding_direction,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary = []
        if not result["valid"]:
            summary.append("❌ Rejected: " + "; ".join(e["message"] for e in result["errors"]))
        elif dry_run:
            summary.append(f"dry_run mode — payload generated to remove ACL '{name_or_number}' on '{router}'.")
        elif result["sent"]:
            summary.append(f"📤 ACL '{name_or_number}' removed on '{router}' through the bridge.")
        elif not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")
        else:
            summary.append("⚠ Sending failed.")

        return json.dumps({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "router": result["router"],
            "acl_id": result["acl_id"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)
