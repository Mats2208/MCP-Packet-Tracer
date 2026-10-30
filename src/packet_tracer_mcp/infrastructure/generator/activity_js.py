"""JS for activity files (ActivityFile, TreeNode) on PT's script engine.

Measured on PT 9.0.1 (2026-10-30):
- getActiveFile() answers ActivityFile's methods when isActivityFile() is true.
- With a password, isPasswordConfirmed() is false and almost every call throws
  "Activity file requires password"; getInstructionCount/getInstruction stay open.
- TreeNode: getChildNodeBy(name) gives null for some names ("VLAN 10"), hence the
  scan by index. getCheckType: 2 correct, 0 wrong. getCompPointPair gives
  {first: component, second: points}. setCheck(true) must run on the whole chain
  from the root.
- setCheck applies to getComparatorTree(); getAssessedComparatorTree() is fixed
  when the file loads, so marks count after a save and reopen.

Every generator is an IIFE reporting one JSON object: {ok:false,error,detail} on
failure, with error = 'not_activity' | 'locked' | 'pt_error'. Values go through
json.dumps; nothing else is interpolated.
"""

from __future__ import annotations

import json

from .host_js import _GATEWAY_JS
from .walk_js import WALK_JS

# Rows and nodes a tree walk may touch before it stops: a device's running config
# alone is 100+ nodes and each one is an IPC round.
MAX_LEAVES = 2000
MAX_NODES = 20000
MAX_CHILDREN = 300

_FAIL = (
    "catch(e){var m=String(e);out({ok:false,"
    "error:/requires password/i.test(m)?'locked':'pt_error',detail:m});}"
)
_HEAD = (
    "function out(o){reportResult(JSON.stringify(o));}"
    "try{var aw=ipc.appWindow();var f=aw.getActiveFile();"
)
_NEED_ACTIVITY = "if(!f||!f.isActivityFile()){out({ok:false,error:'not_activity'});return;}"

# Direct child by name: by id first (measured to work), by index otherwise.
_KID = (
    "function kid(n,name){var c=null;try{c=n.getChildNodeBy(name);}catch(e){}"
    "if(c&&String(c.getNodeName())===name){return c;}"
    "var k=n.getChildCount();for(var i=0;i<k;i++){var x=n.getChildNodeAt(i);"
    "if(x&&String(x.getNodeName())===name){return x;}}return c;}"
    # The nodes from `r` down `path`; a leading "Network" is optional.
    "function chain(r,path){var seg=path.slice();var cur=r;var ch=[r];"
    "if(seg.length&&seg[0]===String(r.getNodeName())){seg.shift();}"
    "for(var i=0;i<seg.length;i++){var k=kid(cur,seg[i]);"
    "if(!k&&ch.length===1){var nw=kid(cur,'Network');"
    "if(nw){ch.push(nw);cur=nw;k=kid(cur,seg[i]);}}"
    "if(!k){return {nodes:ch,at:i,node:cur};}ch.push(k);cur=k;}"
    "return {nodes:ch,at:-1,node:cur};}"
    "function names(n,max){var o=[];var k=n.getChildCount();"
    "for(var i=0;i<k&&i<max;i++){o.push(String(n.getChildNodeAt(i).getNodeName()));}return o;}"
)


def _activity(body: str) -> str:
    return "(function(){" + _HEAD + _NEED_ACTIVITY + body + "}" + _FAIL + "})();"


def wizard_state_js() -> str:
    """{wizard, activity, file, devices}: what is open, for any kind of file."""
    return (
        "(function(){" + _HEAD +
        "var a=false;try{a=!!(f&&f.isActivityFile());}catch(e){}"
        "var s='';try{s=f?String(f.getSavedFilename()):'';}catch(e){}"
        "var d=-1;try{d=ipc.network().getDeviceCount();}catch(e){}"
        "var w=false;try{w=!!aw.isActivityWizardOpen();}catch(e){}"
        "out({ok:true,wizard:w,activity:a,file:s,devices:d});"
        "}" + _FAIL + "})();"
    )


def create_activity_js() -> str:
    """A new file with the Wizard open on it. Its canvas is the answer network."""
    return (
        "(function(){" + _HEAD +
        "var n=aw.fileNew(false);aw.fileActivityWizard();"
        "out({ok:true,created:String(n),wizard:!!aw.isActivityWizardOpen()});"
        "}" + _FAIL + "})();"
    )


def confirm_password_js(password: str) -> str:
    return _activity(
        f"var r=!!f.confirmPassword({json.dumps(password)});"
        "out({ok:true,confirmed:r&&!!f.isPasswordConfirmed()});"
    )


_STATUS_READS = (
    ("items", "getAssessmentItemsCount"), ("correct", "getCorrectAssessmentItemsCount"),
    ("points", "getAssessmentScoreCount"), ("correct_points", "getCorrectAssessmentScoreCount"),
    ("percent", "getPercentageComplete"), ("percent_score", "getPercentageCompleteScore"),
    ("time_ms", "getTimeElapsed"), ("timer_type", "getTimerType"),
    ("countdown_ms", "getCountDownTime"), ("countdown_left_ms", "getCountDownTimeLeft"),
    ("completed_feedback", "getCompletedFeedback"),
    ("incomplete_feedback", "getIncompleteFeedback"), ("dynamic_feedback", "getDyFeedbackString"),
)


def status_js() -> str:
    reads = "".join(f"try{{s.{key}=f.{method}();}}catch(e){{note(e);}}"
                    for key, method in _STATUS_READS)
    return (
        "(function(){" + _HEAD +
        "var s={ok:true,activity:false,locked:false};"
        "try{s.wizard=!!aw.isActivityWizardOpen();}catch(e){}"
        "try{s.file=f?String(f.getSavedFilename()):'';}catch(e){}"
        "if(!f||!f.isActivityFile()){out(s);return;}"
        "s.activity=true;"
        "function note(e){if(/requires password/i.test(String(e))){s.locked=true;}}"
        "try{s.password_confirmed=!!f.isPasswordConfirmed();}catch(e){note(e);}"
        + reads +
        "s.instructions=[];try{var c=f.getInstructionCount();"
        "for(var i=0;i<c;i++){s.instructions.push(String(f.getInstruction(i)));}}catch(e){note(e);}"
        "out(s);}" + _FAIL + "})();"
    )


def assessed_tree_js() -> str:
    """Every leaf of the assessed tree: {path, check, value, points, empty}.

    `empty`: no value, and the same node has children in the full tree. That is a
    parent left without its marked leaves, not an item.
    """
    return _activity(
        _KID + f"var MAXL={MAX_LEAVES},MAXN={MAX_NODES};var rows=[];var seen=0;var cut=false;"
        "function walk(n,p){if(cut){return;}if(++seen>MAXN||rows.length>=MAXL){cut=true;return;}"
        "var k=n.getChildCount();"
        "if(k===0){var pts=0;try{var q=n.getCompPointPair();pts=q?q.second:0;}catch(e){}"
        "var v=String(n.getNodeValue());var e=false;"
        "if(v===''){try{var c=chain(full,p);e=c.at<0&&c.node.getChildCount()>0;}catch(x){}}"
        "rows.push({path:p,check:n.getCheckType(),value:v,points:pts,empty:e});return;}"
        "for(var i=0;i<k;i++){var c=n.getChildNodeAt(i);walk(c,p.concat([String(c.getNodeName())]));}}"
        "var full=f.getComparatorTree();var r=f.getAssessedComparatorTree();"
        "if(r){var k0=r.getChildCount();for(var j=0;j<k0;j++){var c0=r.getChildNodeAt(j);"
        "walk(c0,[String(c0.getNodeName())]);}}"
        "out({ok:true,leaves:rows,truncated:cut,items:f.getAssessmentItemsCount(),"
        "correct:f.getCorrectAssessmentItemsCount(),percent:f.getPercentageComplete()});"
    )


def tree_children_js(path: list[str]) -> str:
    """The children of the comparator-tree node at `path`."""
    return _activity(
        _KID + f"var want={json.dumps(path)};var MAXC={MAX_CHILDREN};"
        "var r=f.getComparatorTree();var c=chain(r,want);"
        "if(c.at>=0){out({ok:true,found:false,missing:want[c.at],"
        "children:names(c.node,MAXC)});return;}"
        "var n=c.node;var k=n.getChildCount();var o=[];"
        "for(var i=0;i<k&&i<MAXC;i++){var x=n.getChildNodeAt(i);"
        "o.push({name:String(x.getNodeName()),value:String(x.getNodeValue()),"
        "check:x.getCheckType(),children:x.getChildCount()});}"
        "out({ok:true,found:true,count:k,children:o});"
    )


def mark_items_js(items: list[list[str]], clear_others: bool) -> str:
    """Check each path's whole chain; a path with a missing step changes nothing."""
    return _activity(
        _KID + f"var items={json.dumps(items)};var clear={json.dumps(bool(clear_others))};"
        "var r=f.getComparatorTree();"
        "function wipe(n){if(n.getCheckType()!==0){n.setCheck(false);}"
        "var k=n.getChildCount();for(var i=0;i<k;i++){wipe(n.getChildNodeAt(i));}}"
        "if(clear){wipe(r);}"
        "var done=[];var miss=[];"
        "for(var i=0;i<items.length;i++){var c=chain(r,items[i]);"
        "if(c.at>=0){miss.push({path:items[i],missing:items[i][c.at],"
        "children:names(c.node,30)});continue;}"
        "for(var j=0;j<c.nodes.length;j++){c.nodes[j].setCheck(true);}done.push(items[i]);}"
        "var n=-1;try{n=r.getCheckLeafCount();}catch(e){}"
        "out({ok:true,marked:done,missing:miss,checked_leaves:n});"
    )


def refresh_score_js() -> str:
    """Set a host's gateway to its own value: an unchanged network that still counts
    as a change, which is what recalculates the score after opening."""
    return (
        "(function(){" + _GATEWAY_JS + WALK_JS + _HEAD +
        "var hit=null;__eachDevice(function(d){if(hit){return;}var g=__gw(d);"
        "if(g===null){return;}"
        "if(typeof d.setDefaultGateway==='function'){d.setDefaultGateway(g);hit=[d,g];return;}"
        "__eachPort(d,function(p){if(!hit&&typeof p.setDefaultGateway==='function'){"
        "p.setDefaultGateway(g);hit=[d,g];}});});"
        "out(hit?{ok:true,device:String(hit[0].getName()),gateway:hit[1]}:{ok:true,device:null});"
        "}" + _FAIL + "})();"
    )


def instruction_source_js(page: int = 0) -> str:
    return _activity(f"out({{ok:true,source:String(f.getInstructionSource({int(page)}))}});")


def save_activity_js(directory: str, name: str, overwrite: bool) -> str:
    """fileSaveAsNoPrompt, then read the disk: an existing file must have changed."""
    return _activity(
        f"var dir={json.dumps(directory)};if(!dir){{dir=String(aw.getDefaultFileSaveLocation());}}"
        "dir=String(dir).replace(/\\\\/g,'/').replace(/\\/+$/,'');"
        f"var full=dir+'/'+{json.dumps(name)};var fm=ipc.systemFileManager();"
        "var had=!!fm.fileExists(full);"
        f"if(had&&!{json.dumps(bool(overwrite))}){{out({{ok:true,saved:false,exists:true,path:full}});return;}}"
        "var t0=null;try{if(had){t0=fm.getFileModificationTime(full);}}catch(e){}"
        "aw.fileSaveAsNoPrompt(full,false);"
        "var ok=!!fm.fileExists(full);var t1=null;try{t1=fm.getFileModificationTime(full);}catch(e){}"
        "if(ok&&t0!==null&&t1!==null&&String(t0)===String(t1)){ok=false;}"
        "out({ok:true,saved:ok,path:full,size:ok?fm.getFileSize(full):0});"
    )


def reset_activity_js() -> str:
    return _activity("f.resetActivity();out({ok:true,percent:f.getPercentageComplete()});")


def clear_hosts_js(names: list[str]) -> str:
    """Zero each host's first Ethernet address and gateway (clear_initial; IOS can't)."""
    return (
        "(function(){try{var c=" + json.dumps(list(names)) + ";var done=[];var miss=[];"
        "for(var i=0;i<c.length;i++){var d=ipc.network().getDevice(c[i]);"
        "if(!d){miss.push(c[i]);continue;}var ps=d.getPorts();"
        "for(var k=0;k<ps.length;k++){if(String(ps[k]).indexOf('Ethernet')>=0){"
        "d.getPort(ps[k]).setIpSubnetMask('0.0.0.0','0.0.0.0');break;}}"
        "d.setDefaultGateway('0.0.0.0');done.push(c[i]);}"
        "reportResult(JSON.stringify({ok:true,cleared:done,missing:miss}));"
        "}catch(e){reportResult(JSON.stringify({ok:false,error:String(e)}));}})();"
    )


def add_pdus_js(pairs: list[list[str]]) -> str:
    """Simple PDUs in the current network: the answer network's become connectivity tests."""
    return (
        "(function(){try{var c=" + json.dumps(pairs) + ";var u=ipc.appWindow().getUserCreatedPDU();"
        "var codes=[];for(var i=0;i<c.length;i++){codes.push(u.addSimplePdu(c[i][0],c[i][1]));}"
        "reportResult(JSON.stringify({ok:true,codes:codes}));"
        "}catch(e){reportResult(JSON.stringify({ok:false,error:String(e)}));}})();"
    )


def connectivity_results_js() -> str:
    """Runs the activity's connectivity tests and reads each verdict."""
    return _activity(
        "f.runConnectivityTests();var n=f.getConnectivityCount();var r=[];"
        "for(var i=0;i<n;i++){r.push(String(f.getLastConnectivityTestResultAt(i)));}"
        "reportResult(JSON.stringify({ok:true,count:n,"
        "correct:f.getLastConnectivityTestCorrectCount(),results:r}));"
    )


def checked_rows_js() -> str:
    """The checked tree in display order: one {name, path, points} per row."""
    return _activity(
        "var t=f.getComparatorTree().getCheckOnlyTree();var out=[];"
        "var walk=function(x,path){var kids=x.getChildCount();var p='';"
        "if(kids===0){try{p=String(x.getCompPointPair().second);}catch(e){}}"
        "out.push({name:String(x.getNodeName()),path:path,leaf:kids===0,points:p});"
        "for(var i=0;i<kids;i++){var k=x.getChildNodeAt(i);"
        "walk(k,path.concat([String(k.getNodeName())]));}};"
        "walk(t,[]);reportResult(JSON.stringify({ok:true,rows:out}));"
    )
