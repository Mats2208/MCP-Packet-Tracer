"""JS for the live topology tools: snapshot (devices + links with cable type) and
the canvas edits (move, rename, delete link) that read their own result back.

Facts measured on PT 9 (2026-10-30) this module relies on:
- Link.getClassName() answers "Cable" for a wired link ("Antenna" is the
  wireless signal the export already handled); Link.getConnectionType() answers
  the cable enum as a string ("8100" straight, "8101" cross). The Python side
  maps it with `catalog.cables.cable_type_name`.
- Device.moveToLocation(x, y) answers true and getXCoordinate()/getYCoordinate()
  then read back exactly (x, y).

Same rules as the other generators: an IIFE, one `json.dumps` config object (no
name is ever interpolated by hand), every PT call inside a try/catch.
"""

from __future__ import annotations

import json

from .walk_js import WALK_JS

# One link as {cls, cable_raw, a:{device, port}, b:{device, port}} (b is missing
# on the wireless "Antenna" signal, which only has the AP's port).
_LINK_JS = (
    "function __end(p){return {device:String(p.getOwnerDevice().getName()),port:String(p.getName())};}"
    "function __link(l){var r={cls:'',cable_raw:''};"
    "try{r.cls=String(l.getClassName());}catch(e){}"
    "try{r.cable_raw=String(l.getConnectionType());}catch(e){}"
    "try{if(r.cls==='Antenna'){r.wireless=true;r.a=__end(l.getPort());}"
    "else{r.a=__end(l.getPort1());r.b=__end(l.getPort2());}}catch(e){r.error=String(e);}"
    "return r;}"
)


def _wrap(cfg: dict, body: str) -> str:
    return (
        "(function(){try{"
        f"var c={json.dumps(cfg)};var net=ipc.network();"
        + body +
        "}catch(e){reportResult('ERROR:'+e);}})();"
    )


_NOT_FOUND = "if(!d){reportResult(JSON.stringify({ok:false,error:'device_not_found'}));return;}"


def topology_snapshot_js() -> str:
    """Every device (model, x/y, ports with IP and link flag) and every link with its cable."""
    return _wrap({}, (
        _LINK_JS + WALK_JS +
        "var n=net.getDeviceCount();var lc=net.getLinkCount();var devs=[];"
        "__eachDevice(function(d){"
        "var ports=[];"
        "__eachPort(d,function(p){"
        "var e={name:String(p.getName()),ip:'',mask:'',linked:false};"
        "try{var ip=p.getIpAddress();if(ip&&String(ip)!=='0.0.0.0'){e.ip=String(ip);"
        "e.mask=String(p.getSubnetMask());}}catch(x){}"
        "try{e.linked=p.getLink()!=null;}catch(x){}ports.push(e);});"
        "var px=null,py=null;try{px=d.getXCoordinate();py=d.getYCoordinate();}catch(x){}"
        "devs.push({name:String(d.getName()),model:String(d.getModel()),x:px,y:py,ports:ports});"
        "});"
        "var links=[];for(var k=0;k<lc;k++){try{links.push(__link(net.getLinkAt(k)));}catch(x){}}"
        "reportResult(JSON.stringify({ok:true,device_count:n,link_count:lc,devices:devs,links:links}));"
    ))


def move_device_js(device: str, x: int, y: int) -> str:
    """moveToLocation, then read the coordinates back: verified = exactly (x, y)."""
    return _wrap({"device": device, "x": int(x), "y": int(y)}, (
        "var d=net.getDevice(c.device);" + _NOT_FOUND +
        "var r=null;try{r=d.moveToLocation(c.x,c.y);}"
        "catch(e){reportResult(JSON.stringify({ok:false,error:String(e)}));return;}"
        "var rx=null,ry=null;try{rx=d.getXCoordinate();ry=d.getYCoordinate();}catch(e){}"
        "reportResult(JSON.stringify({ok:true,returned:r===undefined?null:r,x:rx,y:ry,"
        "verified:rx!==null&&Number(rx)===c.x&&Number(ry)===c.y}));"
    ))


def rename_device_js(old_name: str, new_name: str) -> str:
    """setName after refusing a taken name; verified = new name resolves, old one doesn't.

    PT lets a duplicate name through, and then getDevice() can only return one of
    the two, so the target name must be free first ('duplicate').
    """
    return _wrap({"old": old_name, "new": new_name}, (
        "var d=net.getDevice(c.old);" + _NOT_FOUND +
        "if(net.getDevice(c.new)){reportResult(JSON.stringify({ok:false,error:'duplicate'}));return;}"
        "d.setName(c.new);"
        "var nw=null,od=null,nm=null;try{nw=net.getDevice(c.new);}catch(e){}"
        "try{od=net.getDevice(c.old);}catch(e){}try{nm=nw?String(nw.getName()):null;}catch(e){}"
        "reportResult(JSON.stringify({ok:true,new_present:!!nw,old_absent:!od,name:nm,"
        "verified:!!nw&&!od&&nm===c.new}));"
    ))


def delete_link_js(device: str, port: str) -> str:
    """deleteLink on one port, then check BOTH ends are free.

    The far end is read from the link before deleting it; verified needs both
    ports to report getLink() == null afterwards.
    """
    return _wrap({"device": device, "port": port}, (
        "var d=net.getDevice(c.device);" + _NOT_FOUND +
        "var p=d.getPort(c.port);"
        "if(!p){reportResult(JSON.stringify({ok:false,error:'port_not_found'}));return;}"
        "var l=p.getLink();"
        "if(l==null){reportResult(JSON.stringify({ok:false,error:'no_link'}));return;}"
        "var peer=null;try{var p1=l.getPort1(),p2=l.getPort2();"
        "var o=(String(p1.getOwnerDevice().getName())===c.device&&String(p1.getName())===c.port)?p2:p1;"
        "peer={device:String(o.getOwnerDevice().getName()),port:String(o.getName())};}catch(e){}"
        "var cable='';try{cable=String(l.getConnectionType());}catch(e){}"
        "p.deleteLink();"
        "function free(dn,pn){try{var dd=net.getDevice(dn);var pp=dd?dd.getPort(pn):null;"
        "return pp?pp.getLink()==null:null;}catch(e){return null;}}"
        "var here=free(c.device,c.port);var there=peer?free(peer.device,peer.port):null;"
        "reportResult(JSON.stringify({ok:true,peer:peer,cable_raw:cable,local_free:here,"
        "peer_free:there,verified:here===true&&there===true}));"
    ))


# For a port reader (pt_inspect_ports): `__plink(deviceName, port)` -> the port's
# link as {cable_raw, peer_device, peer_port}, or null when it has none. Every
# call is guarded: a missing getter or a throwing one only blanks that field.
PORT_LINK_JS = (
    "function __plink(dn,p){var l=null;try{l=p.getLink();}catch(e){}if(!l){return null;}"
    "var r={cable_raw:'',peer_device:null,peer_port:null};"
    "try{r.cable_raw=String(l.getConnectionType());}catch(e){}"
    "try{var p1=l.getPort1(),p2=l.getPort2();"
    "var o=(String(p1.getOwnerDevice().getName())===dn&&String(p1.getName())===String(p.getName()))?p2:p1;"
    "r.peer_device=String(o.getOwnerDevice().getName());r.peer_port=String(o.getName());}catch(e){}"
    "return r;}"
)
