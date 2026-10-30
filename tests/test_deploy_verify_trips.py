"""pt_live_deploy verified each device and each link with its own round trip.

A 116-device deploy meant hundreds of trips, plus 20 s + 40 s retries per device on a
timeout. Now one canvas read checks the whole plan (verify_and_reconcile), so the
number of trips does not depend on the topology's size. A timed-out read is still
never a missing device: nothing is re-sent on an unreadable canvas.
"""

from __future__ import annotations

import json

from src.packet_tracer_mcp.adapters.mcp.tools import live
from src.packet_tracer_mcp.domain.models.plans import TopologyPlan
from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.execution.live_checks import (
    deploy_check_js, read_deploy_check, verify_and_reconcile,
)
from tests._fake_ctx import FakeCtx, tools
from tests._pt_js import requires_node, run_pt_js


def _plan(routers: int, pcs: int) -> TopologyPlan:
    plan, validation = plan_from_request(
        TopologyRequest(routers=routers, pcs_per_lan=pcs, dhcp=False, routing="static"))
    assert validation.is_valid
    return plan


def _all_ok(plan):
    return json.dumps({"devices": [1] * len(plan.devices), "links": ["OK"] * len(plan.links)})


class Recorder:
    """A send(js, timeout) that answers from a list and counts the trips."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, js, timeout):
        self.calls.append((js, timeout))
        return self.replies.pop(0) if self.replies else None


# ----------------------------------------------------------------------
# Trips do not grow with the topology
# ----------------------------------------------------------------------

def test_one_trip_verifies_any_size():
    for plan in (_plan(1, 1), _plan(6, 8)):
        send, queued = Recorder([_all_ok(plan)]), []
        out = verify_and_reconcile(send, queued.append, plan, [])
        assert len(send.calls) == 1
        assert queued == []
        assert out["verified"] and out["dev_ok"] == len(plan.devices)
        assert out["link_ok"] == len(plan.links)


def test_reconcile_adds_exactly_one_more_read():
    plan = _plan(4, 6)
    first = {"devices": [1] * len(plan.devices), "links": ["OK"] * len(plan.links)}
    first["devices"][-1] = 0
    first["links"][0] = "NO_LINK"
    commands = [f'lwAddDevice({json.dumps(plan.devices[-1].name)}, 8, "PC-PT", 1, 1);']
    send, queued = Recorder([json.dumps(first), _all_ok(plan)]), []
    out = verify_and_reconcile(send, queued.append, plan, commands)
    assert len(send.calls) == 2
    # The re-sent creation is guarded: it can never duplicate a device.
    assert queued and queued[0].startswith("if(!ipc.network().getDevice(")
    assert out["reconciled_devices"] == [plan.devices[-1].name]
    assert len(out["reconciled_links"]) == 1
    assert out["dev_fail"] == [] and out["link_fail"] == []


def test_tool_trip_count_is_the_same_for_small_and_large_plans():
    counts = []
    for plan in (_plan(1, 1), _plan(5, 10)):
        ctx = FakeCtx(replies=["[]", _all_ok(plan)])
        out = tools(live, ctx)["pt_live_deploy"](plan_json=plan.model_dump_json())
        assert f"Devices: {len(plan.devices)}/{len(plan.devices)} verified" in out
        counts.append(len(ctx.sent))
    assert counts[0] == counts[1] == 2  # name-collision read + one verification read


# ----------------------------------------------------------------------
# A timeout is not "missing"
# ----------------------------------------------------------------------

def test_timeouts_are_retried_with_more_patience_then_left_unverified():
    plan = _plan(2, 2)
    send, queued = Recorder([]), []
    out = verify_and_reconcile(send, queued.append, plan, ['lwAddDevice("R1", 0, "2911", 1, 1);'])
    assert [t for _, t in send.calls] == [5.0, 20.0, 40.0]
    assert out["verified"] is False
    assert queued == []  # nothing re-sent: a late answer is not a missing device


def test_late_answer_is_accepted():
    plan = _plan(1, 1)
    send = Recorder([None, _all_ok(plan)])
    assert read_deploy_check(send, plan) is not None
    assert len(send.calls) == 2


def test_tool_reports_unverified_instead_of_failed_devices():
    plan = _plan(1, 1)
    ctx = FakeCtx(replies=["[]"])
    out = tools(live, ctx)["pt_live_deploy"](plan_json=plan.model_dump_json())
    assert "Could not read the canvas" in out
    assert "FAILED devices" not in out
    assert not any("if(!ipc.network().getDevice(" in q for q in ctx.queued)


def test_a_reply_of_the_wrong_shape_is_unverified():
    plan = _plan(1, 1)
    send = Recorder([json.dumps({"devices": [1], "links": []})])
    assert read_deploy_check(send, plan) is None


# ----------------------------------------------------------------------
# The JS itself, under Node against a fake PT
# ----------------------------------------------------------------------

FAKE_PT = """
function Port(linked) { this.getLink = function () { return linked ? {} : null; }; }
var __devs = {
  'R"1': {getPort: function (n) { return n === 'Gig0/0' ? new Port(true) : (n === 'Gig0/1' ? new Port(false) : null); }},
};
var ipc = {network: function () { return {getDevice: function (n) { return __devs[n] || null; }}; }};
"""


@requires_node
def test_js_checks_devices_and_links_in_one_report(tmp_path):
    plan = TopologyPlan.model_validate({
        "name": "t",
        "devices": [{"name": 'R"1', "model": "2911", "category": "router"},
                    {"name": "PC9", "model": "PC-PT", "category": "pc"}],
        "links": [
            {"device_a": 'R"1', "port_a": "Gig0/0", "device_b": "PC9", "port_b": "Fa0"},
            {"device_a": 'R"1', "port_a": "Gig0/1", "device_b": "PC9", "port_b": "Fa0"},
            {"device_a": 'R"1', "port_a": "Gig9/9", "device_b": "PC9", "port_b": "Fa0"},
            {"device_a": "PC9", "port_a": "Fa0", "device_b": 'R"1', "port_b": "Gig0/0"},
        ],
    })
    js = deploy_check_js(plan)
    assert "\n" not in js and json.dumps('R"1')[1:-1] in js
    [report] = run_pt_js(js, FAKE_PT, tmp_path)
    assert json.loads(report) == {
        "devices": [1, 0],
        "links": ["OK", "NO_LINK", "PORT_MISSING", "DEV_MISSING"],
    }
