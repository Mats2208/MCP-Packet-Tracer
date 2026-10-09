"""JS for the Desktop apps and the Server-PT services.

Methods taken from PT 9.0.1's IPC reference (help/default/IpcAPI) and tested
against PT 9.0.1:
- HttpClient.go(url) is asynchronous; the page arrives in getLastPageContent().
- DhcpServerMain.getDhcpServerProcessByPortName(port) returns the per-interface
  process, with editable pools (DhcpPool.set*) and addNewPool(...). That is what
  issue #23 was missing ("DhcpServerMain does not expose pools").
- DnsServer.addARecordToNameServerDb / addCNAMEToNameServerDb + setEnable.
- HttpServer.setPageContents(url, html) / getPage(url).

Same rules as host_js: an IIFE and a single `json.dumps` object with the data.
"""

from __future__ import annotations

import json


def _head(cfg: dict) -> str:
    return (
        "(function(){"
        f"var c={json.dumps(cfg)};"
        "var d=ipc.network().getDevice(c.device);"
        "if(!d){reportResult(JSON.stringify({ok:false,error:'device_not_found'}));return;}"
    )


_TAIL = "})();"


def _proc(name: str, var: str = "s") -> str:
    """Get a process by name, or stop with error 'no_<name>'."""
    return (
        f"var {var}=null;try{{{var}=d.getProcess({json.dumps(name)});}}catch(e){{}}"
        f"if(!{var}){{reportResult(JSON.stringify({{ok:false,error:{json.dumps('no_' + name)}}}));return;}}"
    )


# ---------------------------------------------------------------------------
# Web Browser
# ---------------------------------------------------------------------------

def web_go_js(device: str, url: str) -> str:
    return (
        _head({"device": device, "url": url}) + _proc("HttpClient") +
        "var before=String(s.getLastPageContent()||'');"
        "var ok=s.go(c.url);"
        "reportResult(JSON.stringify({ok:true,started:!!ok,before_len:before.length,before_tail:before.slice(-200)}));"
        + _TAIL
    )


def web_read_js(device: str) -> str:
    return (
        _head({"device": device}) + _proc("HttpClient") +
        "reportResult(JSON.stringify({ok:true,content:String(s.getLastPageContent()||'')}));"
        + _TAIL
    )


# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------

_DHCP_READ = (
    "function __pools(dp){var r=[];for(var i=0;i<dp.getPoolCount();i++){var p=dp.getPoolAt(i);"
    "r.push({name:String(p.getDhcpPoolName()),network:String(p.getNetworkAddress()),"
    "mask:String(p.getSubnetMask()),start:String(p.getStartIp()),end:String(p.getEndIp()),"
    "gateway:String(p.getDefaultRouter()),dns:String(p.getDnsServerIp()),max_users:p.getMaxUsers(),"
    "tftp:String(p.getTftpAddress()),wlc:String(p.getWlcAddress())});}return r;}"
)


def server_dhcp_js(
    device: str,
    *,
    port: str = "",
    pool: str = "",
    gateway: str = "",
    dns: str = "",
    start_ip: str = "",
    mask: str = "",
    max_users: int = 0,
    tftp: str = "",
    wlc: str = "",
    enable: bool | None = None,
    remove_pool: str = "",
    exclude: list[tuple[str, str]] | None = None,
) -> str:
    """Services > DHCP: create or edit a pool, switch it on, exclude ranges, delete pools."""
    cfg = {
        "device": device, "port": port, "pool": pool, "gateway": gateway, "dns": dns,
        "start": start_ip, "mask": mask, "max": int(max_users or 0), "tftp": tftp, "wlc": wlc,
        "enable": enable, "remove": remove_pool, "exclude": [list(x) for x in (exclude or [])],
    }
    return (
        _head(cfg) + _proc("DhcpServerMain", "dm") + _DHCP_READ +
        "var names=d.getPorts();var pn=c.port||String(names[0]);"
        "var dp=null;try{dp=dm.getDhcpServerProcessByPortName(pn);}catch(e){}"
        "if(!dp){reportResult(JSON.stringify({ok:false,error:'port_not_found',ports:String(names.join(','))}));return;}"
        "var done=[];"
        "if(c.remove){if(dp.getPool(c.remove)){dp.removePool(c.remove);done.push('pool '+c.remove+' deleted');}"
        "else{done.push('pool '+c.remove+' did not exist');}}"
        "if(c.pool){var p=dp.getPool(c.pool);"
        "if(!p){dp.addNewPool(c.pool,c.gateway||'0.0.0.0',c.dns||'0.0.0.0',c.start||'0.0.0.0',"
        "c.mask||'0.0.0.0',c.max||256,c.tftp||'0.0.0.0',c.wlc||'0.0.0.0');p=dp.getPool(c.pool);"
        "done.push('pool '+c.pool+' created');}"
        "else{if(c.start&&c.mask){p.setNetworkMask(c.start,c.mask);}"
        "if(c.start){p.setStartIp(c.start);}"
        "if(c.gateway){p.setDefaultRouter(c.gateway);}"
        "if(c.dns){p.setDnsServerIp(c.dns);}"
        "if(c.max){p.setMaxUsers(c.max);}"
        "done.push('pool '+c.pool+' updated');}}"
        "for(var k=0;k<c.exclude.length;k++){dp.addExcludedAddress(c.exclude[k][0],c.exclude[k][1]);"
        "done.push('excluded '+c.exclude[k][0]+'-'+c.exclude[k][1]);}"
        "if(c.enable!==null){dp.setEnable(!!c.enable);done.push(c.enable?'service On':'service Off');}"
        "reportResult(JSON.stringify({ok:true,port:pn,done:done,enabled:!!dp.isEnable(),pools:__pools(dp),"
        "excluded:dp.getExcludedAddressCount()}));"
        + _TAIL
    )


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

def server_dns_js(
    device: str,
    *,
    records: list[dict] | None = None,
    remove: list[dict] | None = None,
    enable: bool | None = None,
) -> str:
    """Services > DNS. records/remove: [{"name": "www.x.com", "type": "A"|"CNAME", "value": "..."}]."""
    cfg = {"device": device, "records": records or [], "remove": remove or [], "enable": enable}
    return (
        _head(cfg) + _proc("DnsServer") +
        "var done=[];"
        "for(var i=0;i<c.remove.length;i++){var r=c.remove[i];var ok=false;"
        "if(String(r.type).toUpperCase()==='CNAME'){ok=s.removeCNAMEFromNameServerDb(r.name,r.value);}"
        "else{ok=s.removeARecordFromNameServerDb(r.name,r.value);}"
        "done.push((ok?'removed ':'not present ')+r.name);}"
        "for(var j=0;j<c.records.length;j++){var a=c.records[j];var ok2=false;"
        "if(String(a.type).toUpperCase()==='CNAME'){ok2=s.addCNAMEToNameServerDb(a.name,a.value);}"
        "else{ok2=s.addARecordToNameServerDb(a.name,a.value);}"
        "done.push((ok2?'added ':'rejected ')+a.type+' '+a.name+' -> '+a.value);}"
        "if(c.enable!==null){s.setEnable(!!c.enable);done.push(c.enable?'service On':'service Off');}"
        # getIpAddOfDomain/isDomainNameExisted read another table (they give
        # 0.0.0.0/false for records nslookup does resolve): look up the stored record.
        "var check={};for(var k=0;k<c.records.length;k++){var q=c.records[k];var rr=null;"
        "try{rr=String(q.type).toUpperCase()==='CNAME'?s.getCNameRecordWithHostname(q.name,q.value)"
        ":s.getARecordWithAddress(q.name,q.value);}catch(e){}check[q.name]=!!rr;}"
        "reportResult(JSON.stringify({ok:true,done:done,enabled:!!s.isEnabled(),"
        "records:s.getSizeOfNameServerDb(),stored:check}));"
        + _TAIL
    )


# ---------------------------------------------------------------------------
# HTTP / HTTPS
# ---------------------------------------------------------------------------

def server_http_js(
    device: str,
    *,
    pages: dict[str, str] | None = None,
    enable: bool | None = None,
    https_enable: bool | None = None,
) -> str:
    """Services > HTTP: switch HTTP/HTTPS on/off and replace page contents."""
    cfg = {"device": device, "pages": pages or {}, "enable": enable, "https": https_enable}
    return (
        _head(cfg) + _proc("HttpServer") +
        "var done=[];var sizes={};"
        "for(var k in c.pages){if(Object.prototype.hasOwnProperty.call(c.pages,k)){"
        "s.setPageContents(k,c.pages[k]);sizes[k]=String(s.getPage(k)).length;done.push('page '+k);}}"
        "if(c.enable!==null){s.setEnable(!!c.enable);done.push(c.enable?'HTTP On':'HTTP Off');}"
        "var hs=null;try{hs=d.getProcess('HttpsServer');}catch(e){}"
        "if(c.https!==null&&hs&&typeof hs.setEnable==='function'){hs.setEnable(!!c.https);"
        "done.push(c.https?'HTTPS On':'HTTPS Off');}"
        "reportResult(JSON.stringify({ok:true,done:done,http:!!s.isEnabled(),"
        "https:(hs&&typeof hs.isEnabled==='function')?!!hs.isEnabled():null,"
        "port:s.getPortNumber(),page_sizes:sizes}));"
        + _TAIL
    )


# ---------------------------------------------------------------------------
# TFTP / FTP / SYSLOG / EMAIL
# ---------------------------------------------------------------------------

SIMPLE_SERVICES = {
    # name → (process, enable setter, enable getter)
    "tftp": ("TftpServer", "setEnabled", "isEnabled"),
    "ftp": ("FtpServer", "setEnabled", "isEnabled"),
    "syslog": ("SyslogServer", "setEnable", "isEnabled"),
    "email": ("EmailServer", "", ""),
}


def server_service_js(
    device: str,
    service: str,
    *,
    enable: bool | None = None,
    users: list[dict] | None = None,
) -> str:
    """Toggle TFTP/FTP/SYSLOG, and FTP/EMAIL accounts.

    users: [{"username": "...", "password": "...", "permissions": "RWDNL"}] (permissions: FTP only).
    """
    proc, setter, getter = SIMPLE_SERVICES[service]
    cfg = {"device": device, "service": service, "enable": enable, "users": users or [],
           "setter": setter, "getter": getter}
    return (
        _head(cfg) + _proc(proc) +
        "var done=[];"
        "if(c.enable!==null&&c.setter&&typeof s[c.setter]==='function'){s[c.setter](!!c.enable);"
        "done.push(c.enable?'service On':'service Off');}"
        "if(c.users.length&&c.service==='ftp'){var m=s.getFtpUserAccountManager();"
        "for(var i=0;i<c.users.length;i++){var u=c.users[i];"
        "if(m.isExistingUser(u.username)){m.removeFtpUser(u.username);}"
        "m.addFtpUser(u.username,u.password,u.permissions||'RWDNL');done.push('ftp user '+u.username);}}"
        "if(c.users.length&&c.service==='email'){for(var j=0;j<c.users.length;j++){var e=c.users[j];"
        "var ok=s.addUser(e.username,e.password);if(!ok){s.changePassword(e.username,e.password);}"
        "done.push('account '+e.username);}}"
        "var info={ok:true,service:c.service,done:done,"
        "enabled:(c.getter&&typeof s[c.getter]==='function')?!!s[c.getter]():null};"
        "if(c.service==='ftp'){var mm=s.getFtpUserAccountManager();info.users=[];"
        "for(var k=0;k<mm.getUsersCount();k++){info.users.push(String(mm.getUsernameAt(k)));}}"
        "if(c.service==='email'){info.accounts=String(s.getAllEmailAcctAsStrings());}"
        "if(c.service==='syslog'){try{info.entries=s.getAllEntries().length;}catch(e){info.entries=null;}}"
        "reportResult(JSON.stringify(info));"
        + _TAIL
    )


# ---------------------------------------------------------------------------
# Email client
# ---------------------------------------------------------------------------

def email_client_js(
    device: str,
    *,
    action: str,
    name: str = "",
    address: str = "",
    username: str = "",
    password: str = "",
    smtp_server: str = "",
    pop3_server: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
) -> str:
    """Desktop > Email: configure the account, send (SMTP) or fetch mail (POP3)."""
    cfg = {"device": device, "action": action, "name": name, "address": address,
           "username": username, "password": password, "smtp": smtp_server, "pop3": pop3_server,
           "to": to, "subject": subject, "body": body}
    return (
        _head(cfg) + _proc("EmailClient") +
        "var u=s.getEmailUser();var done=[];"
        "if(c.action==='configure'){"
        "if(c.name){u.setName(c.name);}if(c.address){u.setMailId(c.address);}"
        "if(c.username){u.setUser(c.username);}if(c.password){u.setPassword(c.password);}"
        "if(c.smtp){u.setSmtpServer(c.smtp);}if(c.pop3){u.setPop3Server(c.pop3);}done.push('account configured');}"
        "else if(c.action==='send'){var sent=s.getSmtpClient().sendMail(u.getMailId(),c.to,c.subject,c.body,"
        "u.getPassword(),u.getSmtpServer());done.push(sent?'sent to '+c.to:'SMTP refused to send');}"
        "else if(c.action==='receive'){s.getPop3Client().getMailIpc();done.push('POP3 mail request sent');}"
        "reportResult(JSON.stringify({ok:true,done:done,account:{name:String(u.getName()),"
        "address:String(u.getMailId()),username:String(u.getUser()),smtp:String(u.getSmtpServer()),"
        "pop3:String(u.getPop3Server())}}));"
        + _TAIL
    )


# ---------------------------------------------------------------------------
# Host firewall, Terminal
# ---------------------------------------------------------------------------

def host_firewall_js(device: str, *, port: str = "", ipv4: bool | None = None,
                     ipv6: bool | None = None) -> str:
    cfg = {"device": device, "port": port, "v4": ipv4, "v6": ipv6}
    return (
        _head(cfg) +
        "var names=d.getPorts();var pn=c.port;"
        "if(!pn){for(var i=0;i<names.length;i++){var n=String(names[i]);"
        "if(n.indexOf('Ethernet')>=0||n==='Wireless0'){pn=n;break;}}}"
        "var p=pn?d.getPort(pn):null;"
        "if(!p||typeof p.setInboundFirewallService!=='function')"
        "{reportResult(JSON.stringify({ok:false,error:'not_host_port',ports:String(names.join(','))}));return;}"
        "if(c.v4!==null){p.setInboundFirewallService(!!c.v4);}"
        "if(c.v6!==null&&typeof p.setInboundIpv6FirewallService==='function'){p.setInboundIpv6FirewallService(!!c.v6);}"
        "reportResult(JSON.stringify({ok:true,port:pn,ipv4:!!p.isInboundFirewallOn(),"
        "ipv6:(typeof p.isInboundIpv6FirewallOn==='function')?!!p.isInboundIpv6FirewallOn():null}));"
        + _TAIL
    )


# PC Wireless has NO tool: in PT 9.0.1 WirelessClientProcess.addProfile,
# setCurrentProfile and setCurrentProfileStringIPs throw "invalid vector subscript"
# with every combination of arguments (tested 2026-10-07). Open it by hand with
# pt_ui_open(host, app="pc_wireless").


def console_peer_js(device: str) -> str:
    """Which device does the console cable leaving this host's RS 232 reach?

    Uses getLinkAt/getPort1/getPort2/getOwnerDevice, the same calls
    pt_export_topology already uses to list links.
    """
    return (
        _head({"device": device}) +
        "var rs=(typeof d.getRs232Port==='function')?d.getRs232Port():null;"
        "if(!rs){reportResult(JSON.stringify({ok:false,error:'no_rs232'}));return;}"
        "var rsn=String(rs.getName());var net=ipc.network();var peer=null;var peerPort=null;"
        "for(var i=0;i<net.getLinkCount();i++){var l=net.getLinkAt(i);try{"
        "var p1=l.getPort1();var p2=l.getPort2();"
        "var a=String(p1.getOwnerDevice().getName()),b=String(p2.getOwnerDevice().getName());"
        "if(a===c.device&&String(p1.getName())===rsn){peer=b;peerPort=String(p2.getName());}"
        "else if(b===c.device&&String(p2.getName())===rsn){peer=a;peerPort=String(p1.getName());}"
        "}catch(e){}}"
        "reportResult(JSON.stringify({ok:true,rs232:rsn,peer:peer,peer_port:peerPort}));"
        + _TAIL
    )
