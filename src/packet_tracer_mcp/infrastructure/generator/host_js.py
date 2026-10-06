"""JS para el panel de un dispositivo: IP Configuration, lectura del panel y módulos.

Todo el JS va en una IIFE (los `return` tempranos no cortan otros comandos del
mismo lote HTTP) y los datos viajan como UN objeto `json.dumps`: ningún campo
se interpola a mano.
"""

from __future__ import annotations

import json

# Getters que se leen de cada puerto. Cada uno va detrás de un typeof Y de su
# propio try/catch: la superficie de Port cambia por modelo, y hay getters que
# existen pero lanzan (en un puerto de router, getIpv6LinkLocal responde
# "IPC Call ERROR: HostPort - getIpv6LinkLocal implementation error"). Con un
# solo try para todo el puerto, ese error escondía el puerto entero.
_PORT_READ_JS = (
    "function __port(d,n){var p=d.getPort(n);if(!p){return null;}"
    "function v(m){try{return typeof p[m]==='function'?p[m]():null;}catch(e){return null;}}"
    "function s(m){var x=v(m);return x===null||x===undefined?null:String(x);}"
    "function b(m){var x=v(m);return x===null||x===undefined?null:!!x;}"
    "return {name:String(n),ip:s('getIpAddress'),mask:s('getSubnetMask'),"
    "dhcp:b('isDhcpClientOn'),mac:s('getMacAddress'),up:b('isPortUp'),power:b('getPower'),"
    "linked:b('getLink'),ipv6:b('isIpv6Enabled'),ipv6_auto:b('isIpv6AddressAutoConfig'),"
    "link_local:s('getIpv6LinkLocal'),firewall:b('isInboundFirewallOn'),"
    "description:s('getDescription')||'',bandwidth_kbps:v('getBandwidth'),"
    "full_duplex:b('isFullDuplex')};}"
)

_DEVICE_JS = (
    "var d=ipc.network().getDevice(c.device);"
    "if(!d){reportResult(JSON.stringify({ok:false,error:'device_not_found'}));return;}"
)


def host_ip_config_js(
    device: str,
    port: str = "",
    *,
    mode: str = "",
    ip: str = "",
    mask: str = "",
    gateway: str | None = None,
    dns: str | None = None,
    ipv6_mode: str = "",
    ipv6_gateway: str = "",
    ipv6_dns: str = "",
) -> str:
    """Lo que hace Desktop > IP Configuration (y Config > Global > Gateway/DNS).

    `port` vacío = la primera interfaz Ethernet (o Wireless0), igual que el
    configurePcIp de la extensión. `gateway`/`dns` None = no tocar.
    """
    cfg = json.dumps({
        "device": device, "port": port, "mode": mode, "ip": ip, "mask": mask,
        "gateway": gateway, "dns": dns, "ipv6_mode": ipv6_mode,
        "ipv6_gateway": ipv6_gateway, "ipv6_dns": ipv6_dns,
    })
    return (
        "(function(){"
        f"var c={cfg};"
        + _DEVICE_JS + _PORT_READ_JS +
        "var names=d.getPorts();var pn=c.port;"
        "if(!pn){for(var i=0;i<names.length;i++){var n=String(names[i]);"
        "if(n.indexOf('Ethernet')>=0||n==='Wireless0'){pn=n;break;}}}"
        "var p=pn?d.getPort(pn):null;"
        "if(!p){reportResult(JSON.stringify({ok:false,error:'port_not_found',ports:String(names.join(','))}));return;}"
        "if(typeof p.setIpSubnetMask!=='function'||typeof p.setDnsServerIp!=='function')"
        "{reportResult(JSON.stringify({ok:false,error:'not_host_port',port:pn}));return;}"
        "var applied=[];"
        "if(c.mode==='dhcp'){"
        "if(typeof d.setDhcpFlag==='function'){d.setDhcpFlag(true);}"
        "if(typeof p.setDhcpClientFlag==='function'){p.setDhcpClientFlag(true);}"
        "var dc=null;try{dc=d.getProcess('DhcpClient');}catch(e){}"
        "if(dc&&typeof dc.dhcpRun==='function'){dc.dhcpRun(pn);}"
        "applied.push('dhcp');"
        "}else if(c.mode==='static'){"
        "if(typeof d.setDhcpFlag==='function'){d.setDhcpFlag(false);}"
        "if(typeof p.setDhcpClientFlag==='function'){p.setDhcpClientFlag(false);}"
        "applied.push('static');}"
        "if(c.ip){p.setIpSubnetMask(c.ip,c.mask);applied.push('ip '+c.ip+' '+c.mask);}"
        "if(c.gateway!==null){"
        "if(typeof d.setDefaultGateway==='function'){d.setDefaultGateway(c.gateway);}"
        "else if(typeof p.setDefaultGateway==='function'){p.setDefaultGateway(c.gateway);}"
        "applied.push('gateway '+c.gateway);}"
        "if(c.dns!==null){p.setDnsServerIp(c.dns);applied.push('dns '+c.dns);}"
        "if(c.ipv6_mode==='auto'&&typeof p.setIpv6Enabled==='function'){p.setIpv6Enabled(true);"
        "if(typeof p.setIpv6AddressAutoConfig==='function'){p.setIpv6AddressAutoConfig(true);}applied.push('ipv6 auto');}"
        "else if(c.ipv6_mode==='off'&&typeof p.setIpv6Enabled==='function'){p.setIpv6Enabled(false);applied.push('ipv6 off');}"
        "if(c.ipv6_gateway&&typeof p.setv6DefaultGateway==='function'){p.setv6DefaultGateway(c.ipv6_gateway);applied.push('ipv6 gateway');}"
        "if(c.ipv6_dns&&typeof p.setv6ServerIp==='function'){p.setv6ServerIp(c.ipv6_dns);applied.push('ipv6 dns');}"
        "reportResult(JSON.stringify({ok:true,port:pn,applied:applied,"
        "dhcp_flag:(typeof d.getDhcpFlag==='function')?!!d.getDhcpFlag():null,state:__port(d,pn)}));"
        "})();"
    )


def port_state_js(device: str, port: str) -> str:
    """Relee un puerto (para esperar el lease de DHCP)."""
    cfg = json.dumps({"device": device, "port": port})
    return (
        "(function(){"
        f"var c={cfg};"
        + _DEVICE_JS + _PORT_READ_JS +
        "reportResult(JSON.stringify({ok:true,state:__port(d,c.port)}));"
        "})();"
    )


# Procesos de servicio que se reportan si existen. "Aaa" queda afuera a
# propósito: en PT 9.0.1 getProcess("Aaa") lanza "invalid string position".
_SERVICE_PROCS = (
    "DhcpServerMain", "DnsServer", "HttpServer", "HttpsServer", "TftpServer",
    "FtpServer", "SyslogServer", "EmailServer", "NtpServer",
)


def read_device_panel_js(device: str) -> str:
    """Todo lo que muestran Physical/Config/Desktop/Services, de una vez y de solo lectura."""
    cfg = json.dumps({"device": device, "procs": list(_SERVICE_PROCS)})
    return (
        "(function(){"
        f"var c={cfg};"
        + _DEVICE_JS + _PORT_READ_JS +
        "function has(m){return typeof d[m]==='function';}"
        "var out={ok:true,name:String(d.getName()),model:String(d.getModel()),type:d.getType(),"
        "power:has('getPower')?!!d.getPower():null,"
        "host:has('getCommandPrompt'),"
        "desktop:has('isDesktopAvailable')?!!d.isDesktopAvailable():null,"
        "dhcp_flag:has('getDhcpFlag')?!!d.getDhcpFlag():null,"
        "hostname:has('getHostName')?String(d.getHostName()):null,"
        "ports:[],services:{}};"
        "var names=d.getPorts();"
        "for(var i=0;i<names.length;i++){try{var s=__port(d,names[i]);if(s){out.ports.push(s);}}catch(e){}}"
        "for(var j=0;j<c.procs.length;j++){var pr=null;try{pr=d.getProcess(c.procs[j]);}catch(e){}"
        "if(!pr){continue;}var en=null;"
        "if(typeof pr.isEnabled==='function'){en=!!pr.isEnabled();}"
        "else if(typeof pr.isEnable==='function'){en=!!pr.isEnable();}"
        "out.services[c.procs[j]]=en;}"
        "var dm=null;try{dm=d.getProcess('DhcpServerMain');}catch(e){}"
        "if(dm&&names.length){var sp=null;try{sp=dm.getDhcpServerProcessByPortName(String(names[0]));}catch(e){}"
        "if(sp){out.services.DhcpServerMain=!!sp.isEnable();out.dhcp_pools=sp.getPoolCount();}}"
        "reportResult(JSON.stringify(out));"
        "})();"
    )


def add_module_js(device: str, slot: str, module: str) -> str:
    """Instala un módulo con el helper `addModule` de la extensión y REPORTA el resultado.

    El pt_add_module original hacía `return JSON.stringify(...)` sin llamar a
    reportResult: por HTTP siempre terminaba en timeout aunque el módulo se
    hubiera instalado, y por archivo devolvía "" (de ahí el "timeout pero
    funcionó" que documentaba la skill).
    """
    return (
        "(function(){"
        f"var ok=addModule({json.dumps(device)},{json.dumps(slot)},{json.dumps(module)});"
        "reportResult(JSON.stringify({success:ok===true,returned:String(ok)}));"
        "})();"
    )


def remove_module_js(device: str, slot: str) -> str:
    """Quita el módulo de un slot (Physical tab) con el mismo power-cycle que addModule."""
    cfg = json.dumps({"device": device, "slot": slot})
    return (
        "(function(){"
        f"var c={cfg};"
        + _DEVICE_JS +
        "if(typeof d.removeModule!=='function'){reportResult(JSON.stringify({ok:false,error:'unsupported'}));return;}"
        "var before=String(d.getPorts().join(','));"
        "var hp=typeof d.getPower==='function'&&typeof d.setPower==='function';"
        "var was=hp?d.getPower():false;if(hp&&was){d.setPower(false);}"
        "var ok=false;try{ok=d.removeModule(c.slot);}catch(e){ok=false;}"
        "if(hp&&was){d.setPower(true);if(typeof d.skipBoot==='function'){d.skipBoot();}}"
        "reportResult(JSON.stringify({ok:true,removed:ok===true,before:before,"
        "after:String(d.getPorts().join(','))}));"
        "})();"
    )
