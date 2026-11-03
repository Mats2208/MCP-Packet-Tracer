"""Topology-aware layout fitted to the visible canvas (domain/services/layout.py).

Before it, every plan was three rows (routers, switches, hosts) at fixed pixels: 3 routers
with 3 PCs looked like 2 with 2, the servers row sat at y=640 below a 599 px tall view
(14" MacBook, measured), and a triangle's closing link hid behind the middle router.
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.layout import (
    DEFAULT_CANVAS, ICON_H, ICON_W, LABEL_H, layout,
)
from src.packet_tracer_mcp.domain.services.orchestrator import fit_plan_to_canvas, plan_from_request
from src.packet_tracer_mcp.infrastructure.execution.canvas_view import (
    canvas_info_js, category_of, layout_inputs, move_devices_js, parse_canvas_info,
    read_canvas_size,
)

MAC = (1512, 599)       # measured: getMainViewAreaWidth/Height on a 14" MacBook
FULL_HD = (1900, 700)


def _plan(canvas=None, **kw):
    if canvas:
        kw["canvas_width"], kw["canvas_height"] = canvas
    plan, _ = plan_from_request(TopologyRequest(**kw))
    return plan, {d.name: d for d in plan.devices}


def _boxes_overlap(devs):
    boxes = [(d.name, d.x, d.y, d.x + ICON_W, d.y + ICON_H + LABEL_H) for d in devs]
    return [(a[0], b[0]) for i, a in enumerate(boxes) for b in boxes[i + 1:]
            if a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]]


# --- the shape follows the topology ---------------------------------------------

def test_two_routers_put_each_lan_on_its_outer_side():
    # PCs | SW1 - R1 ---- R2 - SW2 | PCs, the drawing the user asked for.
    _, d = _plan(MAC, routers=2, pcs_per_lan=2)
    assert d["R1"].y == d["R2"].y and d["R1"].x < d["R2"].x
    assert d["SW1"].x < d["R1"].x and d["SW2"].x > d["R2"].x
    assert d["PC1"].x == d["PC2"].x < d["SW1"].x       # a column, outside the switch
    assert d["PC3"].x == d["PC4"].x > d["SW2"].x
    assert d["PC1"].y < d["SW1"].y < d["PC2"].y          # PC1 on top, reading order


def test_three_routers_do_not_look_like_two():
    _, d = _plan(MAC, routers=3, pcs_per_lan=3)
    assert len({d[r].y for r in ("R1", "R2", "R3")}) == 1
    # The middle LAN hangs below the chain, the end ones point outward.
    assert d["SW2"].y > d["R2"].y and d["SW2"].x == d["R2"].x
    assert d["SW1"].x < d["R1"].x and d["SW3"].x > d["R3"].x


def test_middle_lans_of_a_long_chain_alternate_down_and_up():
    _, d = _plan(MAC, routers=4, pcs_per_lan=2)
    assert d["SW2"].y > d["R2"].y and d["SW3"].y < d["R3"].y


def test_hosts_keep_natural_order_past_nine():
    # Sorted as text, PC10 came before PC9 on the canvas.
    devices = [("R1", "router"), ("SW1", "switch")] + [(f"PC{i}", "pc") for i in (9, 10, 11, 12)]
    links = [("R1", "SW1")] + [("SW1", f"PC{i}") for i in (9, 10, 11, 12)]
    pos = layout(devices, links, MAC).positions
    order = sorted((f"PC{i}" for i in (9, 10, 11, 12)), key=lambda n: (pos[n][0], pos[n][1]))
    assert order == ["PC9", "PC10", "PC11", "PC12"]


def test_wan_cloud_takes_a_free_side_not_a_lan_slot():
    _, d = _plan(MAC, routers=2, pcs_per_lan=3, has_wan=True)
    cloud = next(v for v in d.values() if v.category == "cloud")
    assert cloud.y < d["R2"].y                           # up, the free side
    assert abs(cloud.x - d["R2"].x) < 5


def test_star_puts_the_hub_inside_its_spokes():
    names = ["HUB", "S1", "S2", "S3"]
    devices = [(n, "router") for n in names]
    links = [("HUB", s) for s in names[1:]]
    pos = layout(devices, links, MAC).positions
    xs, ys = [pos[s][0] for s in names[1:]], [pos[s][1] for s in names[1:]]
    assert min(xs) < pos["HUB"][0] < max(xs) and min(ys) <= pos["HUB"][1] <= max(ys)


def test_switch_only_lab_uses_the_busiest_switch_as_core():
    devices = [("SW1", "switch"), ("SW2", "switch")] + [(f"PC{i}", "pc") for i in range(1, 5)]
    links = [("SW1", "SW2"), ("SW1", "PC1"), ("SW1", "PC2"), ("SW1", "PC3"), ("SW2", "PC4")]
    r = layout(devices, links, MAC)
    assert r.shape == "single" and set(r.positions) == {n for n, _ in devices}


def test_unconnected_devices_are_placed_and_reported():
    r = layout([("R1", "router"), ("X", "pc")], [], MAC)
    assert "X" in r.positions and any("not connected" in n for n in r.notes)


# --- it fits the canvas -----------------------------------------------------------

CASES = [
    dict(routers=2, pcs_per_lan=2),
    dict(routers=3, pcs_per_lan=3, servers=2),
    dict(routers=4, pcs_per_lan=3, has_wan=True),
    dict(template="three_router_triangle", routers=3, pcs_per_lan=2),
    dict(template="single_lan", routers=1, switches_per_router=2, pcs_per_lan=4),
    dict(routers=2, pcs_per_lan=2, laptops_per_lan=2, wireless_laptops=True),
]


@pytest.mark.parametrize("canvas", [MAC, FULL_HD, (2540, 1050)])
@pytest.mark.parametrize("kw", CASES)
def test_common_labs_fit_the_view_without_overlap(kw, canvas):
    plan, _ = _plan(canvas, **kw)
    W, H = canvas
    out = [(d.name, d.x, d.y) for d in plan.devices
           if d.x < 0 or d.y < 0 or d.x + ICON_W > W or d.y + ICON_H + LABEL_H > H]
    assert not out, f"outside {W}x{H}: {out}"
    assert not _boxes_overlap(plan.devices)
    assert not [w for w in plan.warnings if w.startswith("Layout: The topology needs")]


def test_old_servers_row_below_a_mac_view_is_gone():
    plan, _ = _plan(MAC, routers=2, pcs_per_lan=3, servers=1)
    assert max(d.y for d in plan.devices) + ICON_H + LABEL_H <= MAC[1]


def test_a_lab_too_big_for_the_view_says_so_and_stays_on_canvas():
    plan, _ = _plan(MAC, routers=8, pcs_per_lan=10)
    assert any("zoom out" in w for w in plan.warnings)
    assert min(d.x for d in plan.devices) >= 0 and min(d.y for d in plan.devices) >= 0


def test_a_small_lab_is_not_blown_up_on_a_big_screen():
    plan, d = _plan((2540, 1050), routers=2, pcs_per_lan=2)
    span = max(v.x for v in d.values()) - min(v.x for v in d.values())
    assert span < 2540 * 0.75


def test_default_canvas_without_bridge():
    plan, _ = _plan(None, routers=2, pcs_per_lan=2)
    W, H = DEFAULT_CANVAS
    assert all(0 <= d.x and d.x + ICON_W <= W and 0 <= d.y <= H for d in plan.devices)


def test_fit_plan_to_canvas_lays_the_plan_out_again():
    plan, _ = _plan(None, routers=2, pcs_per_lan=2)
    before = {d.name: (d.x, d.y) for d in plan.devices}
    req = TopologyRequest(routers=2, pcs_per_lan=2)
    fit_plan_to_canvas(plan, req, (2540, 1050))
    after = {d.name: (d.x, d.y) for d in plan.devices}
    assert before != after and max(x for x, _ in after.values()) > max(x for x, _ in before.values())


# --- live canvas helpers ------------------------------------------------------------

def test_canvas_info_reply_is_parsed_and_garbage_is_not():
    raw = json.dumps({"ok": True, "view": [1512, 599], "window": [1512, 921], "zoom": 0,
                      "devices": 0, "bbox": None})
    assert parse_canvas_info(raw)["view"] == [1512, 599]
    assert read_canvas_size(lambda js, t: raw) == (1512, 599)
    for bad in (None, "", "nope", json.dumps({"ok": False}), json.dumps({"ok": True, "view": [0, 5]})):
        assert parse_canvas_info(bad) is None
    assert read_canvas_size(lambda js, t: None) is None


def test_canvas_info_js_skips_the_phantom_power_device():
    js = canvas_info_js()
    assert "getMainViewAreaWidth" in js and '"Power Distribution Device"' in js
    assert "__eachDevice" in js


def test_layout_inputs_drop_the_phantom_and_keep_links_between_known_devices():
    snap = {"devices": [
        {"name": "R1", "model": "2911"}, {"name": "PC1", "model": "PC-PT"},
        {"name": "Power Distribution Device0", "model": "Power Distribution Device"}],
        "links": [{"a": {"device": "R1"}, "b": {"device": "PC1"}},
                  {"a": {"device": "R1"}, "b": {"device": "Ghost"}},
                  {"wireless": True, "a": {"device": "R1"}}]}
    devices, links = layout_inputs(snap)
    assert devices == [("R1", "router"), ("PC1", "pc")]
    assert links == [("R1", "PC1")]


def test_category_of_unknown_models_falls_back_on_the_name():
    assert category_of("2911") == "router"
    assert category_of("Some-Cloud-X") == "cloud"
    assert category_of("") == "host"


def test_move_js_escapes_device_names():
    js = move_devices_js({'R1"); evil(); ("': (10, 20)})
    cfg = js[js.index("var c=") + 6: js.index(";var net")]
    assert json.loads(cfg)["p"] == {'R1"); evil(); ("': [10, 20]}
