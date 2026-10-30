"""JS that reads a device's converged state through the Script Engine API.

Routing table, OSPF neighbours, ARP, CDP, STP, ACLs, NAT, MAC table and switch
port membership, read from the device's processes instead of parsing `show`
output. Measured live on PT 9 (2026-10-30): the structs these getters return
(RoutingTable, OspfNeighbor, ArpTable, MacTable...) arrive as plain JS objects,
so they are sent back as they are and normalized in
`domain/services/convergence.py`.

Rules every reader follows:
- process names keep the `Process` suffix: getProcess("Ospf"), "Arp", "Cdp",
  "Nat" and "Stp" throw "invalid string position"; the suffixed names work.
- every PT call sits inside a try/catch (an uncaught error opens a modal that
  freezes the bridge) and a getter that only Cisco's reference lists, not a live
  probe, also sits behind a typeof.
- no for...in over a PT object (it crashes PT); d.getPorts() is an Array of names.

Each function returns ONE line of JS: an IIFE whose only input is a `json.dumps`
config object, so no device name is ever interpolated by hand.
"""

from __future__ import annotations

import json

# Shared helpers. `__proc` turns a missing or throwing getProcess into null;
# `__call` is a guarded getter for methods that are documented but not probed live.
_HELPERS_JS = (
    "function __proc(d,n){try{return typeof d.getProcess==='function'?d.getProcess(n):null;}"
    "catch(e){return null;}}"
    "function __call(o,m,a){try{if(o&&typeof o[m]==='function'){"
    "return a===undefined?o[m]():o[m](a);}}catch(e){}return null;}"
    "function __str(x){return x===null||x===undefined?null:String(x);}"
    "function __ports(d){try{var p=d.getPorts();return p?p:[];}catch(e){return [];}}"
)


def _sweep_js(reader_js: str, cfg: dict) -> str:
    """Runs `__read(d, rec, c)` on one device (`cfg["device"]`) or on all of them.

    Reports {found, devices: [{name, model, supported, ...}]}. `found` is false
    only when a named device does not exist; a device whose process is missing
    comes back with supported=false so the caller can tell the two apart.
    """
    return (
        "(function(){try{"
        f"var c={json.dumps(cfg)};"
        + _HELPERS_JS + reader_js +
        "var net=ipc.network();var names=[];"
        "if(c.device){names=[c.device];}else{var n=net.getDeviceCount();"
        "for(var i=0;i<n;i++){try{var dd=net.getDeviceAt(i);if(dd){names.push(String(dd.getName()));}}"
        "catch(e){}}}"
        "var out=[];"
        "for(var k=0;k<names.length;k++){var d=null;try{d=net.getDevice(names[k]);}catch(e){}"
        "if(!d){if(c.device){reportResult(JSON.stringify({found:false}));return;}continue;}"
        "var rec={name:String(names[k]),model:'',supported:false};"
        "try{rec.model=String(d.getModel());}catch(e){}"
        "try{__read(d,rec,c);}catch(e){rec.error=String(e);}"
        "out.push(rec);}"
        "reportResult(JSON.stringify({found:true,devices:out}));"
        "}catch(e){reportResult('ERROR:'+e);}})();"
    )


# RoutingProcess.getRoutingTable() -> {routingNetworks:[{network, subnetMask,
# routingEntries:[{..., typeOfProtocol}]}]} (measured).
_ROUTES_JS = (
    "function __read(d,rec,c){var rp=__proc(d,'RoutingProcess');"
    "if(!rp||typeof rp.getRoutingTable!=='function'){return;}"
    "rec.supported=true;rec.table=rp.getRoutingTable();}"
)

# OspfMainProcess -> OspfProcess -> OspfArea -> getNeighborTable(portName) per port
# (measured; the table can be null for a port outside the area).
# getProcessId and getAreaId are documented, not probed: behind __call.
_OSPF_JS = (
    "function __read(d,rec,c){var om=__proc(d,'OspfMainProcess');if(!om){return;}"
    "rec.supported=true;rec.processes=[];var ports=__ports(d);var pc=0;"
    "try{pc=om.getOspfProcessCount();}catch(e){}"
    "for(var i=0;i<pc;i++){try{var op=om.getOspfProcessAt(i);if(!op){continue;}"
    "var pr={process_id:__call(op,'getProcessId'),router_id:__str(__call(op,'getRouterId')),areas:[]};"
    "var ac=0;try{ac=op.getAreaCount();}catch(e){}"
    "for(var j=0;j<ac;j++){try{var a=op.getAreaAt(j);if(!a){continue;}"
    "var ar={area_id:__str(__call(a,'getAreaId')),neighbors:[],adjacent:0};"
    "for(var p=0;p<ports.length;p++){try{var nt=a.getNeighborTable(ports[p]);if(!nt){continue;}"
    "var nc=nt.getNeighborCount();"
    "for(var k=0;k<nc;k++){try{var nb=nt.getNeighborAt(k);"
    "if(nb){ar.neighbors.push({local_port:String(ports[p]),neighbor:nb});}}catch(e){}}"
    "try{ar.adjacent+=nt.getAdjNeighborCount();}catch(e){}"
    "}catch(e){}}"
    "pr.areas.push(ar);}catch(e){}}"
    "rec.processes.push(pr);}catch(e){}}}"
)

# ArpProcess.getArpTable() -> {entries:[{ipAddress, macAddress, portName}]} (measured).
_ARP_JS = (
    "function __read(d,rec,c){var ap=__proc(d,'ArpProcess');"
    "if(!ap||typeof ap.getArpTable!=='function'){return;}"
    "rec.supported=true;rec.table=ap.getArpTable();}"
)

# CdpProcess.getNeighborTable() -> CdpNeighborTable: getNeighborTableCount /
# getCdpNeighborAt(i) (measured on an empty table; the CdpNeighbor struct's fields
# come from Cisco's reference).
_CDP_JS = (
    "function __read(d,rec,c){var cp=__proc(d,'CdpProcess');"
    "if(!cp||typeof cp.getNeighborTable!=='function'){return;}"
    "rec.supported=true;rec.neighbors=[];var nt=cp.getNeighborTable();if(!nt){return;}"
    "var n=0;try{n=nt.getNeighborTableCount();}catch(e){}"
    "for(var i=0;i<n;i++){try{var nb=nt.getCdpNeighborAt(i);if(nb){rec.neighbors.push(nb);}}"
    "catch(e){}}}"
)

# StpMainProcess.getStpProcess(vlan): isRootBridge, getRootBridgeId, getPortCount
# (measured). getSwitchId/getSwitchPriority/getRootPathCost/getRootPort are documented,
# not probed: behind __call. getPortState/getPortRole do NOT exist in PT 9 (TypeError).
# Without explicit VLANs, the switch's own VLAN database decides which ones to read.
_STP_JS = (
    "function __read(d,rec,c){var sm=__proc(d,'StpMainProcess');"
    "if(!sm||typeof sm.getStpProcess!=='function'){return;}"
    "rec.supported=true;rec.instances=[];var vl=(c.vlans||[]).slice();"
    "if(!vl.length){var vm=__proc(d,'VlanManager');if(vm){var vn=0;try{vn=vm.getVlanCount();}catch(e){}"
    "for(var i=0;i<vn;i++){try{var v=vm.getVlanAt(i);if(v){var num=v.getVlanNumber();"
    "if(num<1002||num>1005){vl.push(num);}}}catch(e){}}}"
    "if(!vl.length){vl=[1];}}"
    "for(var j=0;j<vl.length;j++){try{var s=sm.getStpProcess(vl[j]);if(!s){continue;}"
    "var rp=__call(s,'getRootPort');"
    "rec.instances.push({vlan:vl[j],is_root:!!__call(s,'isRootBridge'),"
    "root_id:__str(__call(s,'getRootBridgeId')),bridge_id:__str(__call(s,'getSwitchId')),"
    "priority:__call(s,'getSwitchPriority'),root_path_cost:__call(s,'getRootPathCost'),"
    "root_port:rp?__str(__call(rp,'getName')):null,port_count:__call(s,'getPortCount')});"
    "}catch(e){}}}"
)

# AclProcess.getAclCount/getAclAt + Acl.getCommandCount/getCommandAt (measured);
# Acl.getAclId/isExtended documented. Port.getAclInID/getAclOutID already read by
# pt_inspect_ports.
_ACL_JS = (
    "function __read(d,rec,c){var ap=__proc(d,'AclProcess');"
    "if(!ap||typeof ap.getAclCount!=='function'){return;}"
    "rec.supported=true;rec.acls=[];rec.bindings=[];var n=0;try{n=ap.getAclCount();}catch(e){}"
    "for(var i=0;i<n;i++){try{var a=ap.getAclAt(i);if(!a){continue;}var cmds=[];var cc=0;"
    "try{cc=a.getCommandCount();}catch(e){}"
    "for(var j=0;j<cc;j++){try{cmds.push(String(a.getCommandAt(j)));}catch(e){}}"
    "rec.acls.push({id:__str(__call(a,'getAclId')),extended:__call(a,'isExtended'),commands:cmds});"
    "}catch(e){}}"
    "var ports=__ports(d);for(var p=0;p<ports.length;p++){try{var po=d.getPort(ports[p]);if(!po){continue;}"
    "var ain=__str(__call(po,'getAclInID'))||'';var aout=__str(__call(po,'getAclOutID'))||'';"
    "if(ain||aout){rec.bindings.push({port:String(ports[p]),acl_in:ain,acl_out:aout});}}catch(e){}}}"
)

# NatProcess.getNatTable() -> {entries:[...]} and getInSrcListCount (measured; the
# entry fields were never seen, the table was empty). The other counters are
# documented. Port.getNatMode() is "0" without NAT (measured).
_NAT_JS = (
    "function __read(d,rec,c){var np=__proc(d,'NatProcess');"
    "if(!np||typeof np.getNatTable!=='function'){return;}"
    "rec.supported=true;rec.table=np.getNatTable();"
    "rec.counters={inside_source_lists:__call(np,'getInSrcListCount'),"
    "outside_source_lists:__call(np,'getOutSrcListCount'),"
    "inside_source_static:__call(np,'getInSrcStaticCount'),"
    "outside_source_static:__call(np,'getOutSrcStaticCount'),pools:__call(np,'getNatPoolCount')};"
    "rec.ports=[];var ports=__ports(d);for(var p=0;p<ports.length;p++){try{var po=d.getPort(ports[p]);"
    "if(!po){continue;}var m=__call(po,'getNatMode');"
    "if(m!==null){rec.ports.push({port:String(ports[p]),nat_mode_raw:m});}}catch(e){}}}"
)

# VlanManager.getVlanAt(i).getMacTable() -> {entries:[{isDynamic, macAddress, port}]}
# (measured). c.vlan = 0 reads every VLAN.
_MAC_JS = (
    "function __read(d,rec,c){var vm=__proc(d,'VlanManager');if(!vm){return;}"
    "rec.supported=true;rec.vlans=[];var n=0;try{n=vm.getVlanCount();}catch(e){}"
    "for(var i=0;i<n;i++){try{var v=vm.getVlanAt(i);if(!v){continue;}var num=v.getVlanNumber();"
    "if(c.vlan&&num!==c.vlan){continue;}"
    "rec.vlans.push({number:num,name:__str(__call(v,'getName')),table:__call(v,'getMacTable')});"
    "}catch(e){}}}"
)

# The VLAN database (as pt_read_vlans always read it) plus each switch port's
# membership: SwitchPort.isAccessPort/getAccessVlan/getNativeVlanId and
# getPortSecurity (null when not configured) are measured; the PortSecurity getters
# isEnabled/getMaxMacNumber/getViolationCount are documented.
_VLANS_JS = (
    "function __read(d,rec,c){var vm=__proc(d,'VlanManager');if(!vm){return;}"
    "rec.supported=true;rec.vlans=[];rec.ports=[];var n=0;try{n=vm.getVlanCount();}catch(e){}"
    "for(var i=0;i<n;i++){try{var v=vm.getVlanAt(i);if(!v){continue;}"
    "rec.vlans.push({number:v.getVlanNumber(),name:__str(v.getName()),is_default:!!__call(v,'isDefault')});"
    "}catch(e){}}"
    "rec.max_vlans=__call(vm,'getMaxVlans');rec.vlan_interfaces=__call(vm,'getVlanIntCount');"
    "var ports=__ports(d);for(var p=0;p<ports.length;p++){try{var po=d.getPort(ports[p]);"
    "if(!po||typeof po.isAccessPort!=='function'){continue;}var ps=__call(po,'getPortSecurity');"
    "rec.ports.push({name:String(ports[p]),access:!!po.isAccessPort(),"
    "access_vlan:__call(po,'getAccessVlan'),native_vlan:__call(po,'getNativeVlanId'),"
    "up:!!__call(po,'isPortUp'),"
    "port_security:ps?{enabled:!!__call(ps,'isEnabled'),max_mac:__call(ps,'getMaxMacNumber'),"
    "violations:__call(ps,'getViolationCount')}:null});}catch(e){}}}"
)


# DHCP leases. Router (2911, measured): getProcess("DhcpServerProcess") ->
# getPoolCount/getPoolAt(i) -> pool.getDhcpPoolName(), pool.getLeaseAt(i) ->
# {ipAddress, leaseTime, macAddress, port}. There is no lease count: getLeaseAt
# throws "invalid vector subscript" past the end, so it is walked until it throws
# (or answers null), capped at DHCP_MAX_LEASES per pool.
# Server-PT: getProcess("DhcpServerMain").getDhcpServerProcessByPortName(port) per
# port (as pt_server_dhcp uses it); whether ITS pools have getLeaseAt was not
# measured, so it sits behind a typeof and the pool says lease_api false otherwise.
DHCP_MAX_LEASES = 1024

_DHCP_LEASES_JS = (
    "function __pool(p,port){var r={name:__str(__call(p,'getDhcpPoolName')),port:port,"
    "lease_api:typeof p.getLeaseAt==='function',leases:[],truncated:false};"
    "if(!r.lease_api){return r;}"
    f"for(var i=0;i<{DHCP_MAX_LEASES};i++){{var l=null;try{{l=p.getLeaseAt(i);}}catch(e){{break;}}"
    "if(!l){break;}r.leases.push(l);}"
    f"r.truncated=r.leases.length>={DHCP_MAX_LEASES};return r;}}"
    "function __walk(dp,port,rec){var n=0;try{n=dp.getPoolCount();}catch(e){return;}"
    "for(var i=0;i<n;i++){try{var p=dp.getPoolAt(i);if(p){rec.pools.push(__pool(p,port));}}catch(e){}}}"
    "function __read(d,rec,c){rec.pools=[];var sp=__proc(d,'DhcpServerProcess');"
    "if(sp&&typeof sp.getPoolCount==='function'){rec.supported=true;rec.source='router';"
    "__walk(sp,null,rec);if(rec.pools.length){return;}}"
    "var dm=__proc(d,'DhcpServerMain');"
    "if(!dm||typeof dm.getDhcpServerProcessByPortName!=='function'){return;}"
    "rec.supported=true;rec.source='server';var ports=__ports(d);"
    "for(var k=0;k<ports.length;k++){var dp=null;"
    "try{dp=dm.getDhcpServerProcessByPortName(String(ports[k]));}catch(e){}"
    "if(dp){__walk(dp,String(ports[k]),rec);}}}"
)


def dhcp_leases_js(device: str = "") -> str:
    """DHCP pools and their leases on `device` (empty = every DHCP server)."""
    return _sweep_js(_DHCP_LEASES_JS, {"device": device})


def routes_js(device: str = "") -> str:
    """Routing table of `device` (empty = every device with a RoutingProcess)."""
    return _sweep_js(_ROUTES_JS, {"device": device})


def ospf_js(device: str = "") -> str:
    """OSPF processes, areas and neighbours of `device` (empty = all)."""
    return _sweep_js(_OSPF_JS, {"device": device})


def arp_js(device: str = "") -> str:
    """ARP table of `device` (empty = all)."""
    return _sweep_js(_ARP_JS, {"device": device})


def cdp_js(device: str = "") -> str:
    """CDP neighbour table of `device` (empty = all)."""
    return _sweep_js(_CDP_JS, {"device": device})


def stp_js(device: str = "", vlans: list[int] | None = None) -> str:
    """Per-VLAN STP instance of `device` (empty = every switch). No vlans = all of them."""
    return _sweep_js(_STP_JS, {"device": device, "vlans": [int(v) for v in vlans or []]})


def acl_js(device: str = "") -> str:
    """ACLs (with their commands) and the ports they are bound to."""
    return _sweep_js(_ACL_JS, {"device": device})


def nat_js(device: str = "") -> str:
    """NAT translation table, NAT counters and per-port NAT mode."""
    return _sweep_js(_NAT_JS, {"device": device})


def mac_table_js(device: str = "", vlan: int = 0) -> str:
    """MAC address table per VLAN (vlan 0 = every VLAN)."""
    return _sweep_js(_MAC_JS, {"device": device, "vlan": int(vlan)})


def vlans_js(device: str) -> str:
    """VLAN database plus access/trunk membership and port security of each port."""
    return _sweep_js(_VLANS_JS, {"device": device})
