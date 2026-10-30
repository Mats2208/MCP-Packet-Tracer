"""JS that installs or removes expansion modules around ONE power-cycle.

PT refuses most module changes while a device is on, so the device is powered off,
changed and powered back on. Power-cycling a router or switch reloads it from its
startup-config: everything that was only in the running-config used to be lost
without a word. So before powering an IOS device off, the JS saves it with
`enterCommand("write memory", "enable")` (returns `{first: status, second: output}`,
0 = ok, measured on PT 9) and reports per device whether it was restarted and saved.

Hot-swap: `Module.getDescriptor().isHotSwappable()` was measured on PT 9 (false on a
2911's modules), reached through `getRootModule().getSlotCount()` / `getModuleAt(i)`.
A module that is going to be REMOVED is installed, so it can be looked up by its
`getSlotPath()` (Cisco's reference: "0/0" or "0/1") and, only when PT says it is
hot-swappable, the power-cycle is skipped. Anything unknown (slot path not found, a
getter missing or throwing) keeps the power-cycle. A module that is going to be ADDED
does not exist yet: there is no measured way to ask, so adding always power-cycles.

This is the one place the power-cycle is written; pt_add_module,
pt_install_modules_batch and pt_remove_module all use it.
"""

from __future__ import annotations

import json

# PT device types that run IOS and keep a running-config: router, switch,
# multilayer switch (getType() ids measured on PT 9, see shared/constants.py).
IOS_DEVICE_TYPES = (0, 1, 16)

# How deep the slot lookup walks (chassis → slot → sub-slot is 3 levels).
_SLOT_DEPTH = 3

_HELPERS_JS = (
    "function st(r){if(r&&typeof r==='object'){if(r.first!==undefined){return Number(r.first);}"
    "if(r.length!==undefined){return Number(r[0]);}}var x=Number(r);return isNaN(x)?-1:x;}"
    "function ports(d){try{return String(d.getPorts().join(','));}catch(x){return '';}}"
    "function find(m,slot,depth){if(!m||depth>" + str(_SLOT_DEPTH) + "){return null;}"
    "var n=0;try{n=m.getSlotCount();}catch(x){return null;}"
    "for(var i=0;i<n;i++){var y=null;try{y=m.getModuleAt(i);}catch(x){y=null;}if(!y){continue;}"
    "var p=null;try{p=typeof y.getSlotPath==='function'?String(y.getSlotPath()):null;}catch(x){p=null;}"
    "if(p===slot){return y;}var z=find(y,slot,depth+1);if(z){return z;}}return null;}"
    "function hot(d,slot){if(c.action!=='remove'){return null;}"
    "try{var y=find(d.getRootModule(),slot,0);if(!y){return null;}"
    "var h=y.getDescriptor().isHotSwappable();return h===true?true:(h===false?false:null);}"
    "catch(x){return null;}}"
)


def module_change_js(action: str, entries: list[dict]) -> str:
    """Add or remove modules (`entries`: [{device, slot, module?}]) with one power-cycle
    per device, saving IOS running-configs first.

    Reports `{ok, action, results: [{device, slot, module, ok, hot_swappable, error?}],
    devices: [{device, found, was_on, power_cycled, ios, saved, save_status,
    ports_before, ports_after}]}` BEFORE powering back on: the power-on is what takes
    time, and waiting for it is what used to end in timeouts.
    """
    if action not in ("add", "remove"):
        raise ValueError(f"unknown module action: {action!r}")
    ops = [
        {"device": str(e["device"]), "slot": str(e["slot"]), "module": str(e.get("module") or "")}
        for e in entries
    ]
    cfg = json.dumps({"action": action, "ops": ops, "ios": list(IOS_DEVICE_TYPES)})
    return (
        "(function(){"
        f"var c={cfg};var net=ipc.network();"
        + _HELPERS_JS +
        # Group the operations per device; a device is power-cycled unless EVERY
        # module change on it is known to be hot-swappable.
        "var devs=[];var by={};"
        "for(var i=0;i<c.ops.length;i++){var o=c.ops[i];var e=by[o.device];"
        "if(!e){var d=null;try{d=net.getDevice(o.device);}catch(x){d=null;}"
        "e={device:o.device,d:d,found:!!d,hot:true};by[o.device]=e;devs.push(e);}"
        "o.hot=e.d?hot(e.d,o.slot):null;if(o.hot!==true){e.hot=false;}}"
        # Save, then power off.
        "for(var k=0;k<devs.length;k++){var e=devs[k];var d=e.d;"
        "e.was_on=null;e.power_cycled=false;e.ios=false;e.saved=null;e.save_status=null;"
        "if(!d){continue;}"
        "var hp=typeof d.getPower==='function'&&typeof d.setPower==='function';"
        "e.was_on=hp?!!d.getPower():null;e.power_cycled=!!(hp&&e.was_on&&!e.hot);"
        "try{e.ios=c.ios.indexOf(Number(d.getType()))>=0&&typeof d.enterCommand==='function';}catch(x){e.ios=false;}"
        "if(e.power_cycled&&e.ios){try{var w=d.enterCommand('write memory','enable');"
        "e.save_status=st(w);e.saved=e.save_status===0;}catch(x){e.saved=false;}}"
        "e.ports_before=ports(d);"
        "if(e.power_cycled){try{d.setPower(false);}catch(x){}}}"
        # The module changes. addModule/removeModule answer false (without throwing)
        # for a slot the model does not have, so the return value is what counts.
        "var res=[];"
        "for(var j=0;j<c.ops.length;j++){var o=c.ops[j];var e=by[o.device];"
        "var r={device:o.device,slot:o.slot,module:o.module||null,hot_swappable:o.hot,ok:false};"
        "if(!e.d){r.error='device_not_found';}"
        "else if(c.action==='add'){try{r.ok=e.d.addModule(o.slot,allModuleTypes[o.module],o.module)===true;}catch(x){r.ok=false;}}"
        "else if(typeof e.d.removeModule!=='function'){r.error='unsupported';}"
        "else{try{r.ok=e.d.removeModule(o.slot)===true;}catch(x){r.ok=false;}}"
        "res.push(r);}"
        "var out=[];"
        "for(var k=0;k<devs.length;k++){var e=devs[k];"
        "out.push({device:e.device,found:e.found,was_on:e.was_on,power_cycled:e.power_cycled,"
        "ios:e.ios,saved:e.saved,save_status:e.save_status,"
        "ports_before:e.ports_before||'',ports_after:e.d?ports(e.d):''});}"
        "reportResult(JSON.stringify({ok:true,action:c.action,results:res,devices:out}));"
        # Power back on only what this call powered off.
        "for(var k=0;k<devs.length;k++){var e=devs[k];if(!e.power_cycled){continue;}"
        "try{e.d.setPower(true);if(typeof e.d.skipBoot==='function'){e.d.skipBoot();}}catch(x){}}"
        "})();"
    )


def power_cycle_notes(devices: list[dict]) -> list[str]:
    """One line per device saying whether it was restarted and whether its config was saved."""
    notes: list[str] = []
    for dev in devices or []:
        name = dev.get("device", "?")
        if not dev.get("found", True):
            continue
        if not dev.get("power_cycled"):
            if dev.get("was_on"):
                notes.append(f"{name}: not restarted (PT reports the module as hot-swappable).")
            continue
        if not dev.get("ios"):
            notes.append(f"{name}: powered off and back on to change modules (no IOS config to save).")
        elif dev.get("saved"):
            notes.append(
                f"{name}: RESTARTED to change modules; its running-config was saved first "
                "(write memory), so it comes back with it."
            )
        else:
            status = dev.get("save_status")
            why = f" (write memory answered status {status})" if status is not None else ""
            notes.append(
                f"{name}: RESTARTED to change modules, but saving the running-config FAILED{why}: "
                "it came back with its startup-config, so unsaved changes are lost. Re-apply them."
            )
    return notes


def parse_module_change(raw: str | None) -> dict | None:
    """The JSON module_change_js reports, or None when the reply is not that."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None
    return data
