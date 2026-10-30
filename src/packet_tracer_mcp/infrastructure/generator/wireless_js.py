"""JS for an access point's wireless settings (Config > Port 1 of AccessPoint-PT).

Measured on PT 9.0 (2026-10-30): `d.getProcess("WirelessServerProcess")` is
null on anything that is not a wireless AP, and on an AccessPoint-PT it carries
WirelessCommon's methods too: getSsid/setSsid, isSsidBrdCastEnabled/
setSsidBrdCastEnabled, isMacFilterEnabled/setMacFilterEnabled,
isAccessAllowed/setAllowAccess, addToMacFilterAddrList/removeAllMacEntries,
getMacAddressCount/getMacAddressAt, getAuthenType/getEncryptType/
getStandardChannel and resetAllAssociations. Changing the SSID and resetting the
associations drops the clients (they stop answering); the original SSID plus a
reset brings them back. A dropped client's port still reads isPortUp()/getLink()
true, so neither is proof of association.

WPA/WEP are not set here: a client needs a matching profile, and
WirelessClientProcess.addProfile throws on PT 9.0.1 (see rough-edges.md).

Same rules as host_js: an IIFE and a single `json.dumps` object with the data.
"""

from __future__ import annotations

import json


def wireless_ap_js(
    device: str,
    *,
    ssid: str = "",
    broadcast: bool | None = None,
    mac_filter: bool | None = None,
    allow_listed: bool | None = None,
    mac_list: list[str] | None = None,
    reset_associations: bool = True,
    dry_run: bool = False,
) -> str:
    """Apply the requested settings (None/"" = leave alone) and read them back.

    `mac_list` replaces the whole filter list. Associations are reset only when
    something was applied, so clients re-associate against the new settings.
    Each setter has its own try/catch: one that throws is reported in `failed`
    without hiding what the others did.
    """
    cfg = json.dumps({
        "device": device, "ssid": ssid, "broadcast": broadcast, "mac_filter": mac_filter,
        "allow_listed": allow_listed, "mac_list": mac_list, "reset": reset_associations,
        "dry_run": dry_run,
    })
    return (
        "(function(){try{"
        f"var c={cfg};"
        "var d=ipc.network().getDevice(c.device);"
        "if(!d){reportResult(JSON.stringify({ok:false,error:'device_not_found'}));return;}"
        "var w=null;try{w=d.getProcess('WirelessServerProcess');}catch(e){}"
        "if(!w){reportResult(JSON.stringify({ok:false,error:'no_WirelessServerProcess'}));return;}"
        "function v(m){try{return typeof w[m]==='function'?w[m]():null;}catch(e){return null;}}"
        "function bo(m){var x=v(m);return x===null||x===undefined?null:!!x;}"
        "function rd(){var macs=[];var n=v('getMacAddressCount')||0;"
        "for(var i=0;i<n;i++){try{macs.push(String(w.getMacAddressAt(i)));}catch(e){}}"
        "var s=v('getSsid');"
        "return {ssid:s===null||s===undefined?null:String(s),broadcast:bo('isSsidBrdCastEnabled'),"
        "mac_filter:bo('isMacFilterEnabled'),allow_listed:bo('isAccessAllowed'),mac_list:macs,"
        "auth_type:v('getAuthenType'),encrypt_type:v('getEncryptType'),channel:v('getStandardChannel')};}"
        "var applied=[];var failed=[];"
        "function st(label,f){try{f();applied.push(label);}catch(e){failed.push(label+': '+e);}}"
        "var before=rd();"
        "if(!c.dry_run){"
        "if(c.ssid){st('ssid',function(){w.setSsid(c.ssid);});}"
        "if(c.broadcast!==null){st('broadcast',function(){w.setSsidBrdCastEnabled(!!c.broadcast);});}"
        "if(c.mac_filter!==null){st('mac_filter',function(){w.setMacFilterEnabled(!!c.mac_filter);});}"
        "if(c.allow_listed!==null){st('allow_listed',function(){w.setAllowAccess(!!c.allow_listed);});}"
        "if(c.mac_list!==null){st('mac_list',function(){w.removeAllMacEntries();"
        "for(var k=0;k<c.mac_list.length;k++){w.addToMacFilterAddrList(c.mac_list[k]);}});}"
        "if(c.reset&&applied.length){st('reset_associations',function(){w.resetAllAssociations();});}}"
        "reportResult(JSON.stringify({ok:true,dry_run:!!c.dry_run,applied:applied,failed:failed,"
        "before:before,after:c.dry_run?before:rd()}));"
        "}catch(e){reportResult(JSON.stringify({ok:false,error:'pt_error',detail:String(e)}));}})();"
    )
