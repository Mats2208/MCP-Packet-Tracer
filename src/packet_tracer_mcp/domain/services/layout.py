"""Topology-aware layout that fits the Packet Tracer canvas.

The old layout put every router on one row, every switch on the next and every host on
the last, whatever the topology: 3 routers with 3 PCs looked like 2 routers with 2 PCs,
and a triangle's closing link ran behind the middle router. This module reads the graph
instead:

1. The **core** (routers; with no router, the busiest switch) gets a shape from its own
   links: a pair or a chain is a horizontal line, a ring is a regular polygon, a hub with
   spokes is a star, anything else a circle.
2. Each core device gets an **outward direction**: away from the centre of the shape. In
   a line the ends point left and right and the middle ones alternate down and up, so
   2 routers with 2 PCs each draw as PCs | R1 —— R2 | PCs.
3. Everything hanging from a core device (switch → hosts, AP, secondary switch, cloud)
   is a **branch** drawn as a small tree along that direction: the switch first, its
   hosts side by side across the direction, wrapped into rows when there are many.
   Several branches of one router sit in parallel, each with the room it needs.
4. The drawing is **fitted to the canvas** (the visible area of PT's logical view, which
   depends on the screen: 1512x599 on a 14" MacBook, about 1900x700 on 1920x1080): scaled
   to fill it without spreading a small lab across a huge screen, and centred.

Everything works on names, categories and links, so the same function lays out a plan
before the deploy and a live canvas read back from PT.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Visible logical-view area used when the real one cannot be read (no bridge): the
# smallest common case, a 14" MacBook with PT maximised (measured 1512x599).
DEFAULT_CANVAS = (1500, 600)

# A device icon is about 50 px wide; with its two label lines (model, name) it is about
# 65 px tall. PT anchors a device at the top-left corner of its icon.
ICON_W = 50
ICON_H = 40
LABEL_H = 25
MARGIN = 45

# Distances of the unscaled drawing (the fit scales them).
CORE_GAP = 280        # between neighbouring core devices
FIRST_HOP = 125       # core device → first device of a branch
NEXT_HOP = 115        # one tree level further
SPREAD_X = 85         # side by side, horizontally
SPREAD_Y = 90         # side by side, vertically (the labels take room)
ROW_MAX = 6           # hosts in one row before wrapping

# Never shrink below this (labels overlap) and never blow a small lab up past this.
SCALE_MIN = 0.7
SCALE_MAX = 1.5
MAX_STRETCH = 2.2     # one axis at most this many times the other

_COMPASS = [math.radians(a) for a in (-90, 0, 180, 90, -45, -135, 45, 135)]

_HOSTS = {"pc", "laptop", "server", "printer", "phone", "ipphone", "tablet", "smartphone",
          "iot", "host", "end_device"}


@dataclass
class LayoutResult:
    positions: dict[str, tuple[int, int]]       # name → (x, y), top-left like PT
    canvas: tuple[int, int]
    scale: float
    fits: bool                                   # False: the drawing is larger than the canvas
    size: tuple[int, int]                        # width, height of the drawing (px)
    shape: str                                   # core shape: single, line, ring, star, circle
    notes: list[str] = field(default_factory=list)


def _natural(name: str) -> list:
    """Sort key that puts PC9 before PC10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _is_host(cat: str) -> bool:
    return cat.lower() in _HOSTS


def _core_shape(core: list[str], adj: dict[str, set[str]]) -> tuple[str, list[str]]:
    """(shape, ordered core). Shapes: single, line, ring, star, circle."""
    k = len(core)
    cset = set(core)
    cdeg = {c: len(adj[c] & cset) for c in core}
    if k == 1:
        return "single", core
    edges = sum(cdeg.values()) // 2
    # Connected?
    seen, stack = {core[0]}, [core[0]]
    while stack:
        for n in adj[stack.pop()] & cset:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    connected = len(seen) == k
    if connected and edges == k - 1 and max(cdeg.values()) <= 2:
        start = next((c for c in core if cdeg[c] == 1), core[0])
        order, prev, cur = [start], None, start
        while True:
            nxt = [n for n in adj[cur] & cset if n != prev and n not in order]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            order.append(cur)
        # Keep the plan's natural order (R1 on the left) when the path allows it.
        if core.index(order[0]) > core.index(order[-1]):
            order.reverse()
        return "line", order
    if connected and k >= 3 and edges == k and all(d == 2 for d in cdeg.values()):
        start = core[0]
        order, prev, cur = [start], None, start
        while len(order) < k:
            nxt = sorted((n for n in adj[cur] & cset if n != prev and n not in order),
                         key=core.index)
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            order.append(cur)
        return "ring", order
    hub = max(core, key=lambda c: (cdeg[c], -core.index(c)))
    if connected and k >= 3 and cdeg[hub] == k - 1 and edges == k - 1:
        return "star", [hub] + [c for c in core if c != hub]
    return "circle", core


def _place_core(shape: str, order: list[str], turn: float = 0.0
                ) -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
    """(positions, outward unit direction) of the core devices, centred on (0, 0).

    `turn` (radians) rotates a ring, star or circle; a line and a single device ignore it.
    """
    pos: dict[str, tuple[float, float]] = {}
    out: dict[str, tuple[float, float]] = {}
    k = len(order)
    if shape == "single":
        pos[order[0]] = (0.0, 0.0)
        out[order[0]] = (0.0, 1.0)
        return pos, out
    if shape == "line":
        for i, c in enumerate(order):
            pos[c] = ((i - (k - 1) / 2) * CORE_GAP, 0.0)
            if i == 0:
                out[c] = (-1.0, 0.0)
            elif i == k - 1:
                out[c] = (1.0, 0.0)
            else:
                out[c] = (0.0, 1.0) if i % 2 == 1 else (0.0, -1.0)
        return pos, out
    ring_items = order[1:] if shape == "star" else order
    n = len(ring_items)
    # Edge length CORE_GAP on a regular polygon; a star's spokes sit at that radius.
    radius = CORE_GAP if shape == "star" else CORE_GAP / (2 * math.sin(math.pi / n))
    for i, c in enumerate(ring_items):
        # Start at the top so a triangle stands on its base.
        a = -math.pi / 2 + 2 * math.pi * i / n + turn
        if shape != "star" and n % 2 == 0:
            a += math.pi / n           # an even polygon sits flat
        pos[c] = (radius * math.cos(a), radius * math.sin(a))
        out[c] = (math.cos(a), math.sin(a))
    if shape == "star":
        hub = order[0]
        pos[hub] = (0.0, 0.0)
        # The hub's own LANs go into the widest gap between its spokes.
        angles = sorted(math.atan2(*reversed(out[c])) for c in ring_items)
        gaps = [((angles[(i + 1) % n] - angles[i]) % (2 * math.pi) or 2 * math.pi, angles[i])
                for i in range(n)]
        width, start = max(gaps)
        mid = start + width / 2
        out[hub] = (math.cos(mid), math.sin(mid))
    return pos, out


@dataclass
class _Node:
    name: str
    cat: str
    children: list["_Node"] = field(default_factory=list)


def _branch_tree(root: str, adj: dict[str, set[str]], cats: dict[str, str],
                 taken: set[str]) -> _Node:
    node = _Node(root, cats[root])
    taken.add(root)
    for n in sorted(adj[root], key=lambda m: (_is_host(cats[m]), _natural(m))):
        if n not in taken:
            node.children.append(_branch_tree(n, adj, cats, taken))
    return node


def _width(node: _Node) -> float:
    """Units across the branch direction (1 unit = one device side by side)."""
    leaves = [c for c in node.children if not c.children]
    inner = [c for c in node.children if c.children]
    leaf_w = min(len(leaves), ROW_MAX) if leaves else 0
    return max(1.0, leaf_w + sum(_width(c) for c in inner))


def _snap(d: tuple[float, float], step_deg: float) -> tuple[float, float]:
    """`d` rounded to the nearest multiple of `step_deg` degrees."""
    step = math.radians(step_deg)
    a = round(math.atan2(d[1], d[0]) / step) * step
    x, y = math.cos(a), math.sin(a)
    return (0.0 if abs(x) < 1e-9 else x, 0.0 if abs(y) < 1e-9 else y)


def _across(d: tuple[float, float]) -> tuple[float, float]:
    """Unit vector across an axis-aligned `d`, pointing right or down (reading order)."""
    v = (-d[1], d[0])
    if v[0] < 0 or (v[0] == 0 and v[1] < 0):
        v = (-v[0], -v[1])
    return v


def _place_children(at: tuple[float, float], children: list[_Node], d: tuple[float, float],
                    hop: float, pos: dict[str, tuple[float, float]]) -> None:
    """Place `children` one `hop` beyond `at` along `d`, side by side across it.

    Devices with children of their own (a switch, an AP with clients) go in parallel,
    each with the room its subtree needs; plain hosts follow, in rows of ROW_MAX. Rows
    run along the screen axes even when `d` is diagonal, so a ring's LANs stay tidy.
    """
    if not children:
        return
    axis = _snap(d, 90)
    v = _across(axis)
    unit = abs(v[0]) * SPREAD_X + abs(v[1]) * SPREAD_Y
    row_step = abs(axis[0]) * SPREAD_X + abs(axis[1]) * SPREAD_Y
    leaves = [c for c in children if not c.children]
    inner = [c for c in children if c.children]
    per_row = min(len(leaves), ROW_MAX)
    total = sum(_width(c) for c in inner) + per_row
    cursor = -total / 2
    for c in inner:
        w = _width(c)
        off = (cursor + w / 2) * unit
        p = (at[0] + d[0] * hop + v[0] * off, at[1] + d[1] * hop + v[1] * off)
        pos[c.name] = p
        _place_children(p, c.children, axis, NEXT_HOP, pos)
        cursor += w
    for i, c in enumerate(leaves):
        row, col = divmod(i, per_row)
        in_row = min(per_row, len(leaves) - row * per_row)
        off = (cursor + (per_row - in_row) / 2 + col + 0.5) * unit
        depth = hop + row * row_step
        pos[c.name] = (at[0] + d[0] * depth + v[0] * off, at[1] + d[1] * depth + v[1] * off)


def _draw(shape: str, order: list[str], turn: float, adj: dict[str, set[str]],
          cats: dict[str, str]) -> dict[str, tuple[float, float]]:
    """Unscaled centres of the core and of everything hanging from it."""
    pos, out = _place_core(shape, order, turn) if order else ({}, {})

    taken = set(order)
    # Branches of each core device go along its outward direction, side by side.
    for c in order:
        d = _snap(out[c], 45)
        heads = sorted((n for n in adj[c] if n not in taken), key=lambda n: (_is_host(cats[n]), _natural(n)))
        clouds = [n for n in heads if cats[n] == "cloud"]
        trees = [_branch_tree(h, adj, cats, taken) for h in heads
                 if h not in taken and cats[h] != "cloud"]
        _place_children(pos[c], trees, d, FIRST_HOP, pos)
        # A WAN cloud gets its own side, the one farthest from the core links and the
        # LANs (ties: up, where the Internet is usually drawn).
        used = [math.atan2(pos[n][1] - pos[c][1], pos[n][0] - pos[c][0])
                for n in adj[c] & set(order)]
        if trees:
            used.append(math.atan2(d[1], d[0]))
        for cl in clouds:
            if cl in taken:
                continue
            best = max(_COMPASS, key=lambda a: (min((abs((a - u + math.pi) % (2 * math.pi) - math.pi)
                                                     for u in used), default=math.pi),
                                                 -abs(a + math.pi / 2)))
            pos[cl] = (pos[c][0] + math.cos(best) * FIRST_HOP, pos[c][1] + math.sin(best) * FIRST_HOP)
            used.append(best)
            tree = _branch_tree(cl, adj, cats, taken)
            _place_children(pos[cl], tree.children, (math.cos(best), math.sin(best)), NEXT_HOP, pos)

    return pos


def _fit_scale(pos: dict[str, tuple[float, float]], W: int, H: int) -> tuple[float, float]:
    """(fit_x, fit_y): how much each axis may grow to fill the canvas."""
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    avail_x = W - 2 * MARGIN - ICON_W
    avail_y = H - 2 * MARGIN - ICON_H - LABEL_H
    return (avail_x / span_x if span_x else SCALE_MAX, avail_y / span_y if span_y else SCALE_MAX)


def layout(devices: list[tuple[str, str]], links: list[tuple[str, str]],
           canvas: tuple[int, int] | None = None,
           attach: dict[str, str] | None = None) -> LayoutResult:
    """Positions for `devices` ([(name, category)]) joined by `links` ([(a, b)]).

    `attach` adds links that are not cables, e.g. a WiFi laptop → the AP of its LAN,
    so it is drawn next to that AP. `canvas` is the visible (width, height); None uses
    DEFAULT_CANVAS.
    """
    W, H = canvas or DEFAULT_CANVAS
    cats = {n: (c or "").lower() for n, c in devices}
    names = [n for n, _ in devices]
    adj: dict[str, set[str]] = {n: set() for n in names}
    for a, b in list(links) + list((attach or {}).items()):
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)

    notes: list[str] = []
    core = [n for n in names if cats[n] == "router"]
    if not core:
        candidates = [n for n in names if not _is_host(cats[n])] or names
        if candidates:
            core = [max(candidates, key=lambda n: (len(adj[n]), -names.index(n)))]
    shape, order = _core_shape(core, adj) if core else ("single", [])
    # A ring, star or circle may turn: keep the turn that lets the drawing grow the most
    # in the canvas (a tall star on a wide screen lies on its side). The upright one
    # wins unless another is clearly better.
    turns = [0.0]
    if shape in ("ring", "star", "circle"):
        turns += [math.radians(t) for t in range(15, 360, 15)]
    best_pos, best_fit = None, -1.0
    for t in turns:
        cand = _draw(shape, order, t, adj, cats)
        fx, fy = _fit_scale(cand, W, H) if cand else (1.0, 1.0)
        score = min(min(fx, fy), 1.0) * (1.0 if t == 0.0 else 0.97)
        if score > best_fit:
            best_pos, best_fit = cand, score
    pos = best_pos or {}
    taken = set(pos)

    # Anything still unplaced (not connected to the core) goes on a row under the rest.
    loose = [n for n in names if n not in pos]
    if loose:
        # Connected groups among the loose devices keep together.
        ys = [p[1] for p in pos.values()] or [0.0]
        xs = [p[0] for p in pos.values()] or [0.0]
        base_y = max(ys) + NEXT_HOP + 40
        x0 = (min(xs) + max(xs)) / 2 - (len(loose) - 1) * SPREAD_X / 2
        for i, n in enumerate(loose):
            pos[n] = (x0 + i * SPREAD_X, base_y)
        notes.append(f"{len(loose)} device(s) not connected to the core: placed in a row below "
                     f"({', '.join(loose[:6])}{'…' if len(loose) > 6 else ''}).")

    if not pos:
        return LayoutResult({}, (W, H), 1.0, True, (0, 0), shape, notes)

    # Fit: scale the centres so the icons (plus labels) fill the canvas minus margins.
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    avail_x = W - 2 * MARGIN - ICON_W
    avail_y = H - 2 * MARGIN - ICON_H - LABEL_H
    fit_x = avail_x / span_x if span_x else SCALE_MAX
    fit_y = avail_y / span_y if span_y else SCALE_MAX
    # Each axis scales on its own, so a tall shape (a star, a ring) can widen to a wide
    # canvas; the ratio is capped so the shape stays recognisable.
    sx = max(SCALE_MIN, min(SCALE_MAX, fit_x))
    sy = max(SCALE_MIN, min(SCALE_MAX, fit_y))
    sx = min(sx, sy * MAX_STRETCH)
    sy = min(sy, sx * MAX_STRETCH)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    draw_w = span_x * sx + ICON_W
    draw_h = span_y * sy + ICON_H + LABEL_H
    fits = draw_w <= W and draw_h <= H
    # Centre in the canvas; an axis that does not fit starts at the margin instead, so
    # nothing goes negative (PT grows the canvas right and down, never left or up of 0).
    ox = W / 2 if draw_w <= W else MARGIN + span_x * sx / 2 + ICON_W / 2
    oy = (H - LABEL_H) / 2 if draw_h <= H else MARGIN + span_y * sy / 2 + ICON_H / 2
    positions = {
        n: (int(round(ox + (p[0] - cx) * sx - ICON_W / 2)),
            int(round(oy + (p[1] - cy) * sy - ICON_H / 2)))
        for n, p in pos.items()
    }
    if not fits:
        notes.append(f"The topology needs about {int(draw_w)}x{int(draw_h)} px and the canvas "
                     f"shows {W}x{H}: zoom out in PT (or maximise the window) to see all of it.")
    scale = round(min(sx, sy), 3)
    return LayoutResult(positions, (W, H), scale, fits,
                        (int(draw_w), int(draw_h)), shape, notes)
