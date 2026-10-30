"""JS for Simulation mode, Add Simple PDU and Fast Forward Time.

Module level so the simulation tools and pt_send_pdu share one copy, and tests
can check the JS without a bridge. Measured against PT 9.0 (2026-10-30):
- `ipc.appWindow().getUserCreatedPDU().addSimplePdu(src, dst)` answers 0 when
  the PDU is added and 30 when the destination does not exist. Other codes
  were not measured and are returned as they come.
- `ipc.appWindow().getRealtimeToolbar().fastForwardTime()` takes ~30 ms and
  lets STP converge at once (a link cabled moments ago answers a ping right
  after it). `resetNetwork` sits next to it and must never be exposed: it
  wipes the network's dynamic state.
- Never call `colorPDU`, `editPDU` or `scenarioInfoBtn_clicked`: they open
  modal dialogs that freeze the bridge.
"""

from __future__ import annotations

import json


def simulation_mode_js(on: bool) -> str:
    """Enter (on=True) or leave Simulation mode; reports before/after."""
    want = "true" if on else "false"
    return (
        "try {"
        "  var __s = ipc.simulation();"
        "  var __before = !!__s.isSimulationMode();"
        f"  __s.setSimulationMode({want});"
        "  reportResult(JSON.stringify({"
        "    before: __before, after: !!__s.isSimulationMode(),"
        "    frames: __s.getFrameInstanceCount(), sim_time: __s.getCurrentSimTime()"
        "  }));"
        "} catch (__e) { reportResult('ERROR:' + __e); }"
    )


def simulation_step_js(action: str, steps: int) -> str:
    """Step forward/back `steps` times, or reset. `action` is already validated."""
    call = {"forward": "__s.forward();", "back": "__s.backward();",
            "reset": "__s.resetSimulation();"}[action]
    loop = call if action == "reset" else f"for (var __i = 0; __i < {int(steps)}; __i++) {{ {call} }}"
    return (
        "try {"
        "  var __s = ipc.simulation();"
        "  if (!__s.isSimulationMode()) {"
        "    reportResult(JSON.stringify({ simulation_mode: false }));"
        "  } else {"
        "    var __b = __s.getFrameInstanceCount();"
        f"   {loop}"
        "    reportResult(JSON.stringify({"
        "      simulation_mode: true, frames_before: __b,"
        "      frames_after: __s.getFrameInstanceCount(),"
        "      sim_time: __s.getCurrentSimTime(),"
        "      current_index: __s.getCurrentFrameInstanceIndex()"
        "    }));"
        "  }"
        "} catch (__e) { reportResult('ERROR:' + __e); }"
    )


def packet_trace_js(limit: int, device: str = "", include_decisions: bool = True) -> str:
    """Read the event list: path, outcome and PT's per-layer decision log per frame."""
    want = json.dumps(device)
    dec = "true" if include_decisions else "false"
    return (
        "try {"
        "  var __s = ipc.simulation();"
        f"  var __lim = {int(limit)}; var __want = {want}; var __wd = {dec};"
        "  var __n = __s.getFrameInstanceCount();"
        "  var __out = [];"
        "  for (var __i = 0; __i < __n && __out.length < __lim; __i++) {"
        "    try {"
        "      var __f = __s.getFrameInstanceAt(__i);"
        "      if (!__f) continue;"
        "      var __dev = __f.getDevice();"
        "      var __dn = __dev ? __dev.getName() : '';"
        "      if (__want && __dn !== __want) continue;"
        "      var __prev = __f.getPreviousDevice();"
        "      var __ip = __f.getInPort();"
        "      var __op = null;"
        # getOutPort(0) throws when getOutPortCount() is 0 (frame in a buffer,
        # no egress port chosen yet).
        "      try {"
        "        if (__f.getOutPortCount() > 0) {"
        "          var __o = __f.getOutPort(0); __op = __o ? __o.getName() : null;"
        "        }"
        "      } catch (__oe) {}"
        "      var __dl = [];"
        "      if (__wd) {"
        # There is no getDecisionCount(); the flowchart's node count matches
        # the number of decisions (verified: 6/6 and 3/3 on a real ping).
        "        var __dc = __f.getFlowChartNodeCount();"
        "        for (var __j = 0; __j < __dc; __j++) {"
        "          try {"
        # getFrameDecsionAt: the typo is PT's, not ours.
        "            var __d = __f.getFrameDecsionAt(__j);"
        "            if (!__d) continue;"
        "            __dl.push({ layer: __d.osiLayer, inbound: !!__d.osiIn,"
        "                        description: __d.description });"
        "          } catch (__de) {}"
        "        }"
        "      }"
        # A PDU fired with addSimplePdu came back with empty source/destination
        # strings on its ICMP frames (live PT 9, not explained by this reader:
        # the strings are passed through untouched). getTrafficSource() is the
        # documented frame -> UserTraffic link, so the PDU's endpoints are read
        # from there as a fallback. Not yet verified live; null for traffic
        # that no user PDU started (ARP, routing protocols, console pings).
        "      var __ps = null, __pdst = null;"
        "      try {"
        "        var __ut = (typeof __f.getTrafficSource === 'function') ? __f.getTrafficSource() : null;"
        "        if (__ut) {"
        "          if (typeof __ut.getCanonicalSource === 'function') { __ps = String(__ut.getCanonicalSource()); }"
        "          if (typeof __ut.getCanonicalDestination === 'function') { __pdst = String(__ut.getCanonicalDestination()); }"
        "        }"
        "      } catch (__te) {}"
        "      __out.push({"
        "        index: __i, device: __dn,"
        "        pdu_source: __ps, pdu_destination: __pdst,"
        "        previous_device: __prev ? __prev.getName() : null,"
        "        in_port: __ip ? __ip.getName() : null, out_port: __op,"
        "        source: __f.getSourceString(), destination: __f.getDestinationString(),"
        "        traffic_type_raw: __f.getUserTrafficType(),"
        "        sim_time: __f.getStartSimTime(), transit_time: __f.getTransitTime(),"
        "        sent: !!__f.isFrameSent(), accepted: !!__f.isFrameAccepted(),"
        "        dropped: !!__f.isFrameDropped(), buffered: !!__f.isFrameBuffered(),"
        "        in_transit: !!__f.isFrameOnTransit(),"
        "        collided_at_device: !!__f.isFrameCollidedAtDevice(),"
        "        collided_on_link: !!__f.isFrameCollidedOnLink(),"
        "        not_forwarded: !!__f.isFrameNotForwarded(),"
        "        unexpected: !!__f.isFrameUnexpected(),"
        "        decisions: __dl"
        "      });"
        "    } catch (__pe) {}"
        "  }"
        "  reportResult(JSON.stringify({"
        "    total: __n, simulation_mode: !!__s.isSimulationMode(), frames: __out"
        "  }));"
        "} catch (__e) { reportResult('ERROR:' + __e); }"
    )


def send_pdu_js(source: str, destination: str) -> str:
    """Add Simple PDU (ICMP echo) from `source` to `destination`, both device names.

    Both names are checked first so a missing device is reported by name
    instead of as a bare return code. Reports the raw addSimplePdu code.
    """
    cfg = json.dumps({"src": source, "dst": destination})
    return (
        "(function(){try{"
        f"var c={cfg};var net=ipc.network();"
        "if(!net.getDevice(c.src)){reportResult(JSON.stringify({ok:false,error:'source_not_found'}));return;}"
        "if(!net.getDevice(c.dst)){reportResult(JSON.stringify({ok:false,error:'destination_not_found'}));return;}"
        "var s=ipc.simulation();var sim=!!s.isSimulationMode();var before=s.getFrameInstanceCount();"
        "var code=ipc.appWindow().getUserCreatedPDU().addSimplePdu(c.src,c.dst);"
        "reportResult(JSON.stringify({ok:true,code:code,simulation_mode:sim,"
        "frames_before:before,frames_after:s.getFrameInstanceCount()}));"
        "}catch(e){reportResult(JSON.stringify({ok:false,error:'pt_error',detail:String(e)}));}})();"
    )


# Only in Realtime: it is the Realtime toolbar's button, and what it would do to
# a stepped simulation was not measured.
FAST_FORWARD_JS = (
    "(function(){try{"
    "if(ipc.simulation().isSimulationMode()){"
    "reportResult(JSON.stringify({ok:true,fast_forwarded:false,simulation_mode:true}));return;}"
    "ipc.appWindow().getRealtimeToolbar().fastForwardTime();"
    "reportResult(JSON.stringify({ok:true,fast_forwarded:true,simulation_mode:false}));"
    "}catch(e){reportResult(JSON.stringify({ok:false,error:'pt_error',detail:String(e)}));}})();"
)


def parse_fast_forward(raw: str | None) -> dict:
    """FAST_FORWARD_JS's reply as a dict; a timeout or garbage is {ok: False}."""
    if not raw:
        return {"ok": False, "error": "timeout"}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"ok": False, "error": "unreadable", "detail": raw[:200]}
    return data if isinstance(data, dict) else {"ok": False, "error": "unreadable"}


# addSimplePdu return codes. Only 0 and 30 were measured; any other code is
# reported as is.
PDU_CODES = {0: "added", 30: "destination_not_found"}


def pdu_code_meaning(code) -> str:
    try:
        return PDU_CODES.get(int(code), f"code {code} (not documented)")
    except (TypeError, ValueError):
        return f"code {code!r} (not documented)"
