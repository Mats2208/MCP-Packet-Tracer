"""Canvas tools: screenshot, notes, clear annotations."""

from __future__ import annotations

import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....shared.utils import safe_name_component, resolve_within
from ....domain.services.canvas import CanvasImageError, decode_pt_image, normalize_format


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
            base = Path(safe_name_component(output_dir, fallback="projects"))
            base.mkdir(parents=True, exist_ok=True)
            target = resolve_within(base, f"{safe}.{ext}")
            target.write_bytes(blob)
        except (OSError, ValueError) as exc:
            return f"Could not write the image: {exc}"

        return json.dumps({
            "path": str(target),
            "format": image_fmt,
            "bytes": len(blob),
            "summary": f"✅ Capture saved to {target} ({len(blob):,} bytes).",
        }, indent=2, ensure_ascii=False)

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
        return json.dumps({
            "id": raw.strip(),
            "summary": f"✅ Note added at ({x},{y}).",
        }, indent=2, ensure_ascii=False)

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
        return json.dumps(data, indent=2, ensure_ascii=False)
