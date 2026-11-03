"""Canvas tools: screenshot, notes, clear annotations, view size, auto layout."""

from __future__ import annotations

import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....shared.utils import safe_name_component, resolve_within, workdir_subdir
from ....domain.services.canvas import CanvasImageError, decode_pt_image, normalize_format
from ....shared.utils import reply_json
from ....domain.services.layout import ICON_H, ICON_W, LABEL_H, MARGIN, layout
from ....infrastructure.execution.canvas_view import (
    canvas_info_js, layout_inputs, move_devices_js, parse_canvas_info, read_canvas_size,
)
from ....infrastructure.generator.topology_js import topology_snapshot_js


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge

    # ------------------------------------------------------------------
    # CANVAS — capture and annotations
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_screenshot(
        filename: str = "topology",
        fmt: str = "PNG",
        output_dir: str = "projects",
    ) -> str:
        """
        Captures PT's logical canvas and saves it as an image.

        Returns the file's PATH, not the image: a capture weighs tens of
        thousands of bytes and dumping it in the reply would fill the context without
        anyone being able to see it.

        PNG compresses a diagram much better than JPG (measured: 33 KB versus
        105 KB for the same canvas), so it is the default.

        Parameters:
        - filename: file name, without extension. It is sanitised.
        - fmt: PNG (default) | JPG | JPEG | BMP.
        - output_dir: destination folder, relative to the project root.

        Example: pt_screenshot(filename="lab-ospf")
        """
        err = _check_bridge()
        if err:
            return err

        try:
            image_fmt = normalize_format(fmt)
        except CanvasImageError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

        js = (
            "try {"
            "  var __lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            f"  reportResult(String(__lw.getWorkspaceImage({json.dumps(image_fmt)})));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        # Generous on purpose: the image travels as text and is hundreds of KB.
        raw = _bridge_send_and_wait(js, timeout=45.0)
        if raw is None:
            return (
                "No answer from PT while capturing. On very large canvases the "
                "image can exceed the bridge's limit; try fmt='PNG'."
            )
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"

        try:
            blob = decode_pt_image(raw, image_fmt)
        except CanvasImageError as exc:
            return f"Could not decode the image: {exc}"

        safe = safe_name_component(filename, fallback="topology")
        ext = "jpg" if image_fmt in ("JPG", "JPEG") else image_fmt.lower()
        try:
            base = workdir_subdir(output_dir, fallback="projects")
            base.mkdir(parents=True, exist_ok=True)
            target = resolve_within(base, f"{safe}.{ext}")
            target.write_bytes(blob)
        except (OSError, ValueError) as exc:
            return f"Could not write the image: {exc}"

        return reply_json({
            "path": str(target),
            "format": image_fmt,
            "bytes": len(blob),
            "summary": f"✅ Capture saved to {target} ({len(blob):,} bytes).",
        })

    @mcp.tool()
    def pt_add_note(x: int, y: int, text: str) -> str:
        """
        Writes a text note on PT's canvas.

        Use it to document the topology on the diagram itself: label a
        subnet, mark an OSPF area, name a trunk link. Returns the note's
        id, which can be used to delete it later.

        The coordinates are the same logical-canvas ones that pt_add_device
        and pt_move_device use: routers ~y=100, switches ~y=250, hosts ~y=400.

        The font size is NOT configurable: PT fixes it and uses that parameter
        for the stacking order, which the tool computes by itself.

        Parameters:
        - x, y: position on the canvas.
        - text: the note's content.

        Example: pt_add_note(x=300, y=100, text="LAN 192.168.0.0/24")
        """
        err = _check_bridge()
        if err:
            return err
        if not text.strip():
            return json.dumps({"error": "The note is empty."}, ensure_ascii=False)

        # addNote's third argument is the Z-ORDER, not the font size:
        # PT exposes getIncNoteZOrder() precisely to get the next one. Verified
        # by passing 12 and 14 — the notes come out the same size.
        js = (
            "try {"
            "  var __lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            "  var __z = (typeof __lw.getIncNoteZOrder === 'function')"
            "    ? __lw.getIncNoteZOrder() : 0;"
            f"  reportResult(String(__lw.addNote({int(x)}, {int(y)}, __z, "
            f"{json.dumps(text)})));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        return reply_json({
            "id": raw.strip(),
            "summary": f"✅ Note added at ({x},{y}).",
        })

    @mcp.tool()
    def pt_clear_annotations(kind: str = "all") -> str:
        """
        Removes the canvas annotations: text notes and drawings.

        It does NOT touch devices or links, only the graphic elements.

        PT leaves orphan note ids that it never releases — with no text and that it
        refuses to delete. They are not a failure: the canvas is visually clean
        anyway, and they are reported separately as `stale_ids`.

        Parameters:
        - kind: "all" (default) removes notes and drawings; "notes" only the notes.

        Example: pt_clear_annotations()
        """
        err = _check_bridge()
        if err:
            return err

        what = kind.strip().lower()
        if what not in ("all", "notes"):
            return json.dumps(
                {"error": f"Invalid kind: '{kind}'. Use 'all' or 'notes'."},
                ensure_ascii=False,
            )
        # getCanvasItemIds does NOT include the notes: they are separate sets. Sweeping
        # only one left notes on screen and on top reported remaining=0, which
        # is worse than not deleting — the user believes it is clean.
        getters = ["getCanvasNoteIds"] if what == "notes" else [
            "getCanvasNoteIds", "getCanvasItemIds",
        ]
        js_getters = ", ".join(json.dumps(g) for g in getters)
        js = (
            "try {"
            "  var __lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            f"  var __gs = [{js_getters}];"
            "  var __n = 0;"
            "  for (var __k = 0; __k < __gs.length; __k++) {"
            "    var __ids = null;"
            "    try { __ids = __lw[__gs[__k]](); } catch (__ge) { continue; }"
            "    if (!__ids) continue;"
            "    for (var __i = 0; __i < __ids.length; __i++) {"
            "      try { if (__lw.removeCanvasItem(__ids[__i])) __n++; } catch (__re) {}"
            "    }"
            "  }"
            # PT leaves orphan note IDs: no text, and removeCanvasItem
            # returning false. Counting them as "remaining" would suggest the
            # cleanup failed when the canvas ended up empty, so they are kept apart.
            "  var __left = 0, __stale = 0;"
            "  for (var __m = 0; __m < __gs.length; __m++) {"
            "    var __rest = null;"
            "    try { __rest = __lw[__gs[__m]]() || []; } catch (__le) { continue; }"
            "    for (var __q = 0; __q < __rest.length; __q++) {"
            "      var __has = true;"
            "      try {"
            "        if (typeof __lw.getCanvasNoteText === 'function') {"
            "          __has = String(__lw.getCanvasNoteText(__rest[__q]) || '') !== '';"
            "        }"
            "      } catch (__te) {}"
            "      if (__has) { __left++; } else { __stale++; }"
            "    }"
            "  }"
            "  reportResult(JSON.stringify({ removed: __n, remaining: __left,"
            "    stale_ids: __stale }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"
        data["kind"] = what
        stale = data.get("stale_ids", 0)
        # The orphan ids are not a failure: PT never releases them and the canvas
        # is visually clean anyway. They are mentioned without alarm.
        nota = f" ({stale} orphan id(s) that PT does not release)." if stale else "."
        data["summary"] = (
            f"✅ {data['removed']} annotation(s) removed{nota}"
            if data.get("removed")
            else f"There were no annotations to remove{nota}"
        )
        return reply_json(data)

    # ------------------------------------------------------------------
    # CANVAS — size and layout
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_canvas_info() -> str:
        """
        Size of PT's visible logical view and where the devices are, so you know which
        x/y range to use before pt_add_device / pt_move_device / pt_add_note.

        Returns view [width, height] in canvas px (depends on the screen and the window:
        1512x599 on a 14" MacBook, about 1900x700 on a maximised 1920x1080), zoom (0 =
        100 %), the device count and their bbox [x0, y0, x1, y1]. x/y of a device is the
        top-left corner of its icon (about 50x40 px, labels below): keep x within
        [40, width-90] and y within [40, height-90] so everything is visible.
        """
        err = _check_bridge()
        if err:
            return err
        data = parse_canvas_info(_bridge_send_and_wait(canvas_info_js(), timeout=8.0))
        if data is None:
            return _TIMEOUT_MSG
        w, h = data["view"]
        data["usable_x"] = [MARGIN, max(MARGIN, w - MARGIN - ICON_W)]
        data["usable_y"] = [MARGIN, max(MARGIN, h - MARGIN - ICON_H - LABEL_H)]
        zoom = data.get("zoom")
        data["summary"] = (
            f"Visible view {w}x{h} px at zoom {zoom} (0 = 100 %). Place icons with x in "
            f"{data['usable_x']} and y in {data['usable_y']}."
            + (f" Devices: {data['devices']}, bbox {data['bbox']}." if data.get("bbox") else "")
            + (" PT is zoomed: the visible area in canvas px differs." if zoom not in (0, None) else "")
        )
        return reply_json(data)

    @mcp.tool()
    def pt_auto_layout(apply: bool = True) -> str:
        """
        Rearrange the devices on the canvas by topology and fit them to the visible view.

        Reads the live devices and links: routers form the core (2 or a chain: a row with
        the end LANs pointing outward, middle LANs alternating down/up; a ring: a polygon;
        a hub: a star), each LAN hangs off its router away from the core (switch, then its
        hosts side by side), a WAN cloud takes the free side. Moves everything in one call
        and reads each position back.

        Use it after building by hand, after adding devices, or when a lab looks crowded.
        pt_full_build / pt_plan_topology already plan with this layout.

        Parameters:
        - apply: False only returns the new positions without moving anything.
        """
        err = _check_bridge()
        if err:
            return err
        raw = _bridge_send_and_wait(topology_snapshot_js(), timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        try:
            snapshot = json.loads(raw)
        except (TypeError, ValueError):
            return f"Unreadable reply from PT: {raw[:200]}"
        if not snapshot.get("ok"):
            return f"PT error: {raw[:200]}"
        devices, links = layout_inputs(snapshot)
        if not devices:
            return "The canvas has no devices to arrange."
        canvas = read_canvas_size(lambda js, t: _bridge_send_and_wait(js, timeout=t))
        result = layout(devices, links, canvas)
        data: dict = {
            "canvas": list(result.canvas), "canvas_read": canvas is not None,
            "shape": result.shape, "fits": result.fits, "size": list(result.size),
            "notes": result.notes,
            "positions": {n: list(p) for n, p in result.positions.items()},
        }
        if not apply:
            data["summary"] = f"Layout computed for {len(devices)} device(s); nothing moved (apply=False)."
            return reply_json(data)
        moved_raw = _bridge_send_and_wait(move_devices_js(result.positions), timeout=20.0)
        if moved_raw is None:
            return _TIMEOUT_MSG
        try:
            moved = json.loads(moved_raw)
        except (TypeError, ValueError):
            return f"Unreadable reply from PT: {moved_raw[:200]}"
        data.update({k: moved.get(k) for k in ("moved", "missing", "mismatched")})
        data["verified"] = bool(moved.get("ok")) and moved.get("moved") == len(result.positions)
        data["summary"] = (
            f"✅ {moved.get('moved')}/{len(result.positions)} device(s) moved ({result.shape} core) "
            f"to fit {result.canvas[0]}x{result.canvas[1]}."
            if data["verified"]
            else f"⚠ Moved {moved.get('moved')}/{len(result.positions)}; missing {moved.get('missing')}, "
                 f"mismatched {moved.get('mismatched')}."
        ) + ("" if result.fits else " " + " ".join(result.notes[-1:]))
        return reply_json(data)
