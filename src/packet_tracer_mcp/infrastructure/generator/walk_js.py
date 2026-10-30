"""The network -> device -> port walk, written once.

Every reader of the live canvas walked `ipc.network()` the same way
(getDeviceCount/getDeviceAt, then getPortCount/getPortAt), each with its own copy
and its own idea of which call could throw. WALK_JS defines two helpers that a
generated script prepends and then calls with a callback:

    __eachDevice(function(d){ ... });       // every device on the canvas
    __eachPort(d, function(p){ ... });      // every port of one device

Each device and each port runs inside its own try/catch: one device or port whose
getters throw is skipped instead of aborting the walk (an uncaught error in PT's
Script Engine opens a modal that freezes the bridge). Inside a callback, `return`
plays the role `continue` had in the old loops. Single line, no `//` comments:
PT strips newlines from pasted code.
"""

from __future__ import annotations

WALK_JS = (
    "function __eachDevice(fn){var __wn=ipc.network();var __wc=0;"
    "try{__wc=__wn.getDeviceCount();}catch(e){}"
    "for(var __wi=0;__wi<__wc;__wi++){var __wd=null;try{__wd=__wn.getDeviceAt(__wi);}catch(e){}"
    "if(__wd){try{fn(__wd);}catch(e){}}}}"
    "function __eachPort(d,fn){var __pc=0;try{__pc=d.getPortCount();}catch(e){}"
    "for(var __pj=0;__pj<__pc;__pj++){var __wp=null;try{__wp=d.getPortAt(__pj);}catch(e){}"
    "if(__wp){try{fn(__wp);}catch(e){}}}}"
)
