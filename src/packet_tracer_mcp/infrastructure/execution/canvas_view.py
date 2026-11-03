"""The live canvas: how big the visible logical view is, and rearranging what is on it.

Facts measured on PT 9.0.1 (macOS, 2026-11-03) this module relies on:
- `ipc.appWindow().getMainViewAreaWidth()` / `getMainViewAreaHeight()` answer the size of
  the visible logical view in screen points: 1512x599 on a 14" MacBook with PT maximised
  (window 1512x921; the rest is toolbars and the device palette). A screen capture of the
  window matched that area to the pixel.
- `lw.getCurrentZoom()` answers 0 at 100 %. PT zooms out by itself with many devices
  (zoom -5 was seen); the factor per step is not measured, so a non-zero zoom is only
  reported, and the size is taken as is.
- A device's x/y is the top-left corner of its icon; labels hang below it.
- Every topology carries a "Power Distribution Device" at about (3899, 3900), off the
  canvas. It is not part of the drawing and is never moved.

Same rules as the other generators: one `json.dumps` config object, every PT call in a
try/catch, results through reportResult.
"""

from __future__ import annotations

import json
from typing import Callable

from ..catalog.devices import resolve_model
from ..generator.walk_js import WALK_JS

# Hidden device PT adds to every topology (physical view power); never laid out.
_PHANTOM_PREFIX = "Power Distribution Device"


def canvas_info_js() -> str:
    """View size, zoom, window size and the bounding box of the devices."""
    return (
        "(function(){try{" + WALK_JS + "var aw=ipc.appWindow();"
        "var lw=aw.getActiveWorkspace().getLogicalWorkspace();"
        "var r={ok:true,view:[aw.getMainViewAreaWidth(),aw.getMainViewAreaHeight()],"
        "window:[aw.getWidth(),aw.getHeight()],zoom:null,devices:0,bbox:null};"
        "try{r.zoom=lw.getCurrentZoom();}catch(e){}"
        "var x0=null,y0=null,x1=null,y1=null;"
        "__eachDevice(function(d){var n=String(d.getName());"
        f"if(n.indexOf({json.dumps(_PHANTOM_PREFIX)})===0){{return;}}"
        "var x=d.getXCoordinate(),y=d.getYCoordinate();r.devices++;"
        "if(x0===null||x<x0){x0=x;}if(y0===null||y<y0){y0=y;}"
        "if(x1===null||x>x1){x1=x;}if(y1===null||y>y1){y1=y;}});"
        "if(x0!==null){r.bbox=[x0,y0,x1,y1];}"
        "reportResult(JSON.stringify(r));"
        "}catch(e){reportResult(JSON.stringify({ok:false,error:String(e)}));}})();"
    )


def parse_canvas_info(raw: str | None) -> dict | None:
    """The canvas_info_js answer as a dict, or None when it is missing or broken."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    view = data.get("view") or []
    if len(view) != 2 or not all(isinstance(v, (int, float)) and v > 0 for v in view):
        return None
    return data


def read_canvas_size(send: Callable[[str, float], str | None]) -> tuple[int, int] | None:
    """(width, height) of the visible logical view, or None if PT does not answer."""
    data = parse_canvas_info(send(canvas_info_js(), 5.0))
    if not data:
        return None
    w, h = data["view"]
    return int(w), int(h)


def category_of(model: str, name: str = "") -> str:
    """Layout category of a live device, from its model."""
    m = resolve_model(model) if model else None
    if m:
        return m.category
    low = (model or name).lower()
    for key, cat in (("cloud", "cloud"), ("router", "router"), ("switch", "switch"),
                     ("server", "server"), ("laptop", "laptop"), ("access", "accesspoint"),
                     ("printer", "printer"), ("phone", "phone"), ("pc", "pc")):
        if key in low:
            return cat
    return "host"


def layout_inputs(snapshot: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """([(name, category)], [(a, b)]) of a topology_snapshot_js answer, phantom left out."""
    devices = [
        (d["name"], category_of(d.get("model", ""), d["name"]))
        for d in snapshot.get("devices", [])
        if d.get("name") and not str(d["name"]).startswith(_PHANTOM_PREFIX)
    ]
    names = {n for n, _ in devices}
    links = []
    for link in snapshot.get("links", []):
        a, b = (link.get("a") or {}).get("device"), (link.get("b") or {}).get("device")
        if a in names and b in names:
            links.append((a, b))
    return devices, links


def move_devices_js(positions: dict[str, tuple[int, int]]) -> str:
    """Move every device in one call and read each position back.

    Answers {ok, moved, missing, mismatched}: a device is mismatched when PT reads a
    position other than the one asked.
    """
    cfg = {"p": {n: [int(x), int(y)] for n, (x, y) in positions.items()}}
    return (
        "(function(){try{"
        f"var c={json.dumps(cfg)};var net=ipc.network();var r={{ok:true,moved:0,missing:[],mismatched:[]}};"
        "for(var n in c.p){var d=net.getDevice(n);if(!d){r.missing.push(n);continue;}"
        "try{d.moveToLocation(c.p[n][0],c.p[n][1]);"
        "if(d.getXCoordinate()==c.p[n][0]&&d.getYCoordinate()==c.p[n][1]){r.moved++;}"
        "else{r.mismatched.push(n);}}catch(e){r.mismatched.push(n);}}"
        "reportResult(JSON.stringify(r));"
        "}catch(e){reportResult(JSON.stringify({ok:false,error:String(e)}));}})();"
    )
