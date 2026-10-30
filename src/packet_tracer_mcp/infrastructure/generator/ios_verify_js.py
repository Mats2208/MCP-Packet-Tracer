"""JS that applies IOS config like the extension's configureIosDevice, but reports back.

The extension's `configureIosDevice(name, commands)` (core.js) types each line with
`device.enterCommand(line, "")` and throws the result away, so every CLI tool used to
report success as soon as the payload was queued. `enterCommand` returns
`{first: status, second: output}` (measured on PT 9: 0 ok, 1 ambiguous, 2 invalid,
3 incomplete, 4 not implemented), which is enough to tell which lines IOS rejected.

The installed extension cannot be changed from here, so instead of a new global the
payload is wrapped in an IIFE that declares a LOCAL `configureIosDevice`. The
`configureIosDevice("R1", "...")` calls the use cases already generate resolve to it
by scope, unchanged, and the final `reportResult` carries one record per device.
"""

from __future__ import annotations

import json

# IOS answers to enterCommand's status code.
IOS_STATUS = {0: "ok", 1: "ambiguous", 2: "invalid", 3: "incomplete", 4: "not implemented"}

# Mode-changing lines every payload carries around the config. configureIosDevice
# enters global config before the first line, so `enable` and `configure terminal`
# land in config mode where IOS refuses them; harmless, so they are not reported.
NAVIGATION_LINES = ("enable", "configure terminal", "conf t", "end", "write memory")

# Tail of the IOS output kept per rejected line (the error is at the end).
OUTPUT_TAIL = 300


def verified_configure_js(js_payload: str) -> str:
    """Wrap `configureIosDevice(...)` calls so each line's IOS status is reported.

    `js_payload` is the JS the use cases build (one or more configureIosDevice calls,
    every argument already escaped). Reports `{"devices": [...]}` with, per device:
    `found`, `lines`, `rejected` ([{line, command, status, output}]) and `saved`.
    """
    return (
        "(function(){"
        "var __res=[];"
        f"var __nav={json.dumps(list(NAVIGATION_LINES))};"
        "function __st(r){"
        "if(r&&typeof r==='object'){"
        "if(r.first!==undefined){return Number(r.first);}"
        "if(r.length!==undefined){return Number(r[0]);}}"
        "var x=Number(r);return isNaN(x)?-1:x;}"
        "function __out(r){try{"
        "var o=(r&&typeof r==='object')?(r.second!==undefined?r.second:r[1]):'';"
        f"return String(o===undefined||o===null?'':o).slice(-{OUTPUT_TAIL});"
        "}catch(e){return '';}}"
        "function configureIosDevice(n,c){"
        "var d=ipc.network().getDevice(n);"
        "if(!d){__res.push({device:n,found:false});return false;}"
        "try{skipBootIfIos(d);}catch(e){}"
        "var ls=String(c||'').split('\\n');var bad=[];"
        "d.enterCommand('!','global');"
        "for(var i=0;i<ls.length;i++){"
        "var t=String(ls[i]).replace(/^\\s+|\\s+$/g,'');"
        "var r=d.enterCommand(ls[i],'');var s=__st(r);"
        "if(s!==0&&t!==''&&__nav.indexOf(t.toLowerCase())<0){"
        "bad.push({line:i+1,command:ls[i],status:s,output:__out(r)});}}"
        "var w=d.enterCommand('write memory','enable');"
        "__res.push({device:n,found:true,lines:ls.length,rejected:bad,saved:__st(w)===0});"
        "return true;}"
        + js_payload
        + "reportResult(JSON.stringify({devices:__res}));"
        "})()"
    )
