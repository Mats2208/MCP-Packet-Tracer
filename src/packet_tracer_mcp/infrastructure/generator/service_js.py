"""JS for the Desktop apps and the Server-PT services.

Methods taken from PT 9.0.1's IPC reference (help/default/IpcAPI) and tested
against PT 9.0.1:
- HttpClient.go(url) is asynchronous; the page arrives in getLastPageContent().
- DhcpServerMain.getDhcpServerProcessByPortName(port) returns the per-interface
  process, with addPool(name) and editable pools (DhcpPool.set*). That is what
  issue #23 was missing ("DhcpServerMain does not expose pools").
- DnsServer.addARecordToNameServerDb / addCNAMEToNameServerDb + setEnable.
- HttpServer.setPageContents(url, html) / getPage(url). setPageContents only
  replaces a page that already exists: for a new one PT 9.0.0 throws
  "File not exist: <page>" (not checked on 9.0.1). No API in use here creates one.
  On PT 9.0.0.810, turning HTTP/HTTPS on or writing pages while both are off
  hangs the script engine and crashes PT: server_http_js refuses that call.

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
    # Network address of `ip` under `mask` (plain JS, no PT call).
    "function __net(ip,m){var a=String(ip).split('.'),b=String(m).split('.'),r=[];"
    "for(var i=0;i<4;i++){r.push((parseInt(a[i],10)&parseInt(b[i],10))>>>0);}return r.join('.');}"
)


def server_dhcp_js(
    device: str,
    *,
    port: str = "",
    pool: str = "",
    network: str = "",
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
    drop_factory_pool: bool = False,
) -> str:
    """Services > DHCP: create or edit a pool, switch it on, exclude ranges, delete pools.

    The ONE backend for both Server-PT DHCP tools (pt_server_dhcp and
    pt_configure_dhcp_server). Only the measured API is used (see
    skill/reference/script-engine-api.md, "DHCP server"): pools are created with
    `addPool(name)` (a repeated name does not duplicate) and filled with the
    setters, network + two-argument `setNetworkMask` first and `setStartIp`
    before `setMaxUsers` (it recomputes the end from the start). `addNewPool`
    (eight unnamed arguments) is not used, so TFTP and WLC, which have no
    setter, always come back in `not_applied`.

    `pool` is only created or edited when at least one pool field (network,
    gateway, dns, start_ip, mask, max_users, tftp, wlc) is given: a bare name, a
    `remove_pool`, an `exclude` or an `enable` never create a pool (PT would
    get one full of 0.0.0.0). Exclusions belong to the port's DHCP process,
    not to a pool, so they never touch one either. A mask without `network`
    re-fits the pool's network (from `start_ip`, else the current one) to it.
    `drop_factory_pool` deletes the factory "serverPool" next to another pool
    when it was never configured (no gateway, start at the network address):
    it would hand out .1, the gateway.

    Reports {ok, port, done, not_applied, enabled, pools, excluded, created,
    dropped_factory_pool, error, server_ip}, or {ok: false, error} with
    device_not_found / no_DhcpServerMain / port_not_found.
    """
    has_fields = any((network, gateway, dns, start_ip, mask, max_users, tftp, wlc))
    cfg = {
        "device": device, "port": port, "pool": pool if has_fields else "",
        "network": network, "gateway": gateway, "dns": dns,
        "start": start_ip, "mask": mask, "max": int(max_users or 0), "tftp": tftp, "wlc": wlc,
        "enable": enable, "remove": remove_pool, "exclude": [list(x) for x in (exclude or [])],
        "drop_factory": bool(drop_factory_pool), "factory": "serverPool",
    }
    return (
        _head(cfg) + _proc("DhcpServerMain", "dm") + _DHCP_READ +
        "var names=d.getPorts();var pn=c.port||String(names[0]);"
        "var dp=null;try{dp=dm.getDhcpServerProcessByPortName(pn);}catch(e){}"
        "if(!dp){reportResult(JSON.stringify({ok:false,error:'port_not_found',ports:String(names.join(','))}));return;}"
        "var done=[];var skipped=[];var created=false;var dropped=false;var fail='';"
        "if(c.remove){if(dp.getPool(c.remove)){dp.removePool(c.remove);done.push('pool '+c.remove+' deleted');}"
        "else{done.push('pool '+c.remove+' did not exist');}}"
        "if(c.pool){var p=dp.getPool(c.pool);"
        "if(!p){dp.addPool(c.pool);p=dp.getPool(c.pool);created=!!p;}"
        "if(!p){fail='PT did not create the pool';}else{"
        "var nw=c.network||(c.mask?__net(c.start||String(p.getNetworkAddress()),c.mask):'');"
        "if(nw&&c.mask){p.setNetworkAddress(nw);p.setNetworkMask(nw,c.mask);}"
        "else if(nw){p.setNetworkAddress(nw);}"
        "if(c.gateway){p.setDefaultRouter(c.gateway);}"
        "if(c.dns){p.setDnsServerIp(c.dns);}"
        "if(c.start){p.setStartIp(c.start);}"
        "if(c.max){p.setMaxUsers(c.max);}"
        # PT's DhcpPool has no setter for TFTP or WLC.
        "if(c.tftp){skipped.push('tftp');}if(c.wlc){skipped.push('wlc');}"
        "done.push('pool '+c.pool+(created?' created':' updated'));}}"
        "for(var k=0;k<c.exclude.length;k++){dp.addExcludedAddress(c.exclude[k][0],c.exclude[k][1]);"
        "done.push('excluded '+c.exclude[k][0]+'-'+c.exclude[k][1]);}"
        "if(c.enable!==null){dp.setEnable(!!c.enable);done.push(c.enable?'service On':'service Off');}"
        "if(c.drop_factory&&c.pool&&c.pool!==c.factory&&!fail){var f=dp.getPool(c.factory);"
        "if(f&&String(f.getDefaultRouter())==='0.0.0.0'&&(String(f.getStartIp())===String(f.getNetworkAddress())"
        "||String(f.getStartIp())==='0.0.0.0')){dp.removePool(c.factory);dropped=true;"
        "done.push('pool '+c.factory+' deleted (never configured)');}}"
        "var sip='';try{sip=String(d.getPort(pn).getIpAddress());}catch(e){}"
        "reportResult(JSON.stringify({ok:true,port:pn,done:done,not_applied:skipped,"
        "enabled:!!dp.isEnable(),pools:__pools(dp),excluded:dp.getExcludedAddressCount(),"
        "created:created,dropped_factory_pool:dropped,error:fail,server_ip:sip}));"
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
    """Services > HTTP: switch HTTP/HTTPS on/off and replace page contents.

    Only pages that already exist can be replaced: for a new one PT 9.0.0's
    setPageContents throws "File not exist: <page>". Each page is tried on its
    own; a refused page goes to `failed` and the rest still applies.

    From "HTTP off AND HTTPS off", PT 9.0.0.810 hung and then crashed when one
    call turned HTTP/HTTPS on and wrote a page (which statement does it is not
    isolated). So when both read back as off and the call would turn anything
    on or write a page, nothing is sent to PT: the reply lists the requested
    changes in `blocked`. Calls that only turn things off still run. If the
    HTTPS state cannot be read, the call runs as before.
    """
    cfg = {"device": device, "pages": pages or {}, "enable": enable, "https": https_enable}
    return (
        _head(cfg) + _proc("HttpServer") +
        "var done=[];var sizes={};var failed=[];"
        "var hs=null;try{hs=d.getProcess('HttpsServer');}catch(e){}"
        # Read both states before any mutating call. null/undefined = unknown,
        # and an unknown state never blocks.
        "var h0=null;try{h0=s.isEnabled();}catch(e){}"
        "var hs0=null;if(hs&&typeof hs.isEnabled==='function'){try{hs0=hs.isEnabled();}catch(e){}}"
        "function __off(v){return v!==null&&v!==undefined&&!v;}"
        "var asked=[];var turnsOn=c.enable===true||c.https===true;"
        "for(var p in c.pages){if(Object.prototype.hasOwnProperty.call(c.pages,p)){asked.push('page '+p);turnsOn=true;}}"
        "if(c.enable!==null){asked.push(c.enable?'HTTP On':'HTTP Off');}"
        "if(c.https!==null){asked.push(c.https?'HTTPS On':'HTTPS Off');}"
        "if(__off(h0)&&__off(hs0)&&turnsOn){reportResult(JSON.stringify({ok:true,blocked:asked,"
        "done:[],http:false,https:false,port:s.getPortNumber(),page_sizes:{},failed:[]}));return;}"
        "for(var k in c.pages){if(Object.prototype.hasOwnProperty.call(c.pages,k)){"
        "try{s.setPageContents(k,c.pages[k]);}catch(e){failed.push({page:k,error:String(e)});continue;}"
        "sizes[k]=String(s.getPage(k)).length;done.push('page '+k);}}"
        "if(c.enable!==null){s.setEnable(!!c.enable);done.push(c.enable?'HTTP On':'HTTP Off');}"
        "if(c.https!==null&&hs&&typeof hs.setEnable==='function'){hs.setEnable(!!c.https);"
        "done.push(c.https?'HTTPS On':'HTTPS Off');}"
        "reportResult(JSON.stringify({ok:true,done:done,http:!!s.isEnabled(),"
        "https:(hs&&typeof hs.isEnabled==='function')?!!hs.isEnabled():null,"
        "port:s.getPortNumber(),page_sizes:sizes,failed:failed}));"
        + _TAIL
    )


# ---------------------------------------------------------------------------
# TFTP / FTP / SYSLOG / EMAIL
# ---------------------------------------------------------------------------

# The newest SYSLOG messages pt_server_service returns (`entries` is always the total).
SYSLOG_MAX_ENTRIES = 200

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
    domain: str | None = None,
    smtp: bool | None = None,
    pop3: bool | None = None,
) -> str:
    """Toggle TFTP/FTP/SYSLOG, FTP/EMAIL accounts, and EMAIL's domain and SMTP/POP3.

    users: [{"username": "...", "password": "...", "permissions": "RWDNL"}] (permissions: FTP only).
    EMAIL's switches are not on EmailServer: measured on PT 9.0, getProcess("SmtpServer")
    has get/setServerDomainName and isEnabled/setEnable, getProcess("Pop3Server")
    isEnabled/setEnable. None = leave alone; all three are read back.
    """
    proc, setter, getter = SIMPLE_SERVICES[service]
    cfg = {"device": device, "service": service, "enable": enable, "users": users or [],
           "setter": setter, "getter": getter, "domain": domain, "smtp": smtp, "pop3": pop3}
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
        "var sm=null,p3=null;"
        "if(c.service==='email'){try{sm=d.getProcess('SmtpServer');}catch(e){}"
        "try{p3=d.getProcess('Pop3Server');}catch(e){}"
        "if(c.smtp!==null&&sm){sm.setEnable(!!c.smtp);done.push(c.smtp?'SMTP On':'SMTP Off');}"
        "if(c.pop3!==null&&p3){p3.setEnable(!!c.pop3);done.push(c.pop3?'POP3 On':'POP3 Off');}"
        "if(c.domain!==null&&sm){sm.setServerDomainName(c.domain);done.push('domain '+c.domain);}}"
        "var info={ok:true,service:c.service,done:done,"
        "enabled:(c.getter&&typeof s[c.getter]==='function')?!!s[c.getter]():null};"
        "if(c.service==='email'){info.smtp=sm?!!sm.isEnabled():null;info.pop3=p3?!!p3.isEnabled():null;"
        "info.domain=sm?String(sm.getServerDomainName()):null;"
        "if(info.smtp!==null&&info.pop3!==null){info.enabled=info.smtp&&info.pop3;}}"
        "if(c.service==='ftp'){var mm=s.getFtpUserAccountManager();info.users=[];"
        "for(var k=0;k<mm.getUsersCount();k++){info.users.push(String(mm.getUsernameAt(k)));}}"
        "if(c.service==='email'){info.accounts=String(s.getAllEmailAcctAsStrings());}"
        # getAllEntries() is an Array (measured on PT 9); its entries' fields were
        # never seen, so each one is sent as PT serializes it (JSON, else String),
        # the newest SYSLOG_MAX_ENTRIES only.
        "if(c.service==='syslog'){info.entries=null;info.messages=[];try{var all=s.getAllEntries();"
        "info.entries=all.length;"
        f"for(var q=Math.max(0,all.length-{SYSLOG_MAX_ENTRIES});q<all.length;q++){{var t=null;"
        "try{t=JSON.stringify(all[q]);}catch(e){}"
        "if(t===undefined||t===null||t==='{}'){try{t=String(all[q]);}catch(e){t=null;}}"
        "info.messages.push(t);}}catch(e){}}"
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
