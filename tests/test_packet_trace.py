"""Tests del trace de simulación (pt_read_packet_trace y compañía).

La forma de los datos está verificada contra PT 9.0.0.0810: un `ping` desde un
PC-PT a su gateway genera dos frames — el ICMP queda en buffer (traffic type 0)
mientras sale primero el ARP broadcast (type 5) — y cada uno trae su log de
decisiones por capa OSI.
"""

from tests._registry_src import registry_source
from pathlib import Path

import pytest

from src.packet_tracer_mcp.domain.services.packet_trace import (
    FAILURE_STATUSES,
    MEASURED_TRAFFIC_TYPES,
    TRAFFIC_TYPES,
    frame_status,
    normalize_frame,
    summarize_trace,
    traffic_type_label,
)

# Frame real capturado de PT: el ICMP que queda esperando la resolución ARP.
ICMP_BUFFERED = {
    "device": "PC1",
    "destination": "192.168.0.1",
    "traffic_type": "ICMP",
    "buffered": True,
    "decisions": [
        {"layer": 3, "inbound": False,
         "description": "The Ping process starts the next ping request."},
        {"layer": 2, "inbound": False,
         "description": "The next-hop IP address is not in the ARP table. The ARP "
                        "process tries to send an ARP request for that IP address "
                        "and buffers this packet."},
    ],
}


def _frame(**overrides) -> dict:
    base = {"device": "PC1", "destination": "192.168.0.1", "traffic_type": "ICMP",
            "decisions": []}
    base.update(overrides)
    return base


class TestTrafficTypeLabel:
    @pytest.mark.parametrize("raw,label", [(0, "ICMP"), (5, "ARP"), (11, "STP"), (12, "OSPF")])
    def test_measured_types(self, raw, label):
        """Observed against a live PT 9 (STP and OSPF used to read as type11/type12)."""
        assert traffic_type_label(raw) == label
        assert TRAFFIC_TYPES[raw] == label
        assert raw in MEASURED_TRAFFIC_TYPES

    @pytest.mark.parametrize("raw,label", [(7, "DHCP"), (19, "DNS"), (17, "HTTP"),
                                           (9, "EIGRP"), (49, "LLDP"), (1000, "Custom")])
    def test_documented_types(self, raw, label):
        """From Cisco's eTrafficType_* enum, which matched every measured value."""
        assert traffic_type_label(raw) == label

    def test_value_outside_the_enum_is_not_invented(self):
        assert traffic_type_label(54) == "type54"
        assert traffic_type_label(None) == "typeNone"


class TestFrameStatus:
    def test_pending_when_nothing_is_set(self):
        assert frame_status(_frame()) == "pending"

    @pytest.mark.parametrize("flag,status", [
        ("dropped", "dropped"),
        ("collided_on_link", "collided_on_link"),
        ("collided_at_device", "collided_at_device"),
        ("not_forwarded", "not_forwarded"),
        ("unexpected", "unexpected"),
        ("buffered", "buffered"),
        ("in_transit", "in_transit"),
        ("accepted", "accepted"),
        ("sent", "sent"),
    ])
    def test_each_flag_maps_to_its_status(self, flag, status):
        assert frame_status(_frame(**{flag: True})) == status

    def test_failure_wins_over_success(self):
        """Un frame descartado importa más que uno 'enviado' en el mismo tick."""
        assert frame_status(_frame(sent=True, dropped=True)) == "dropped"

    def test_collision_wins_over_buffered(self):
        assert frame_status(_frame(buffered=True, collided_on_link=True)) == "collided_on_link"

    def test_failure_statuses_are_the_ones_that_block(self):
        assert "sent" not in FAILURE_STATUSES
        assert "buffered" not in FAILURE_STATUSES
        assert "dropped" in FAILURE_STATUSES


class TestSummarizeTrace:
    def test_empty_trace(self):
        result = summarize_trace([])
        assert result["frames"] == 0
        assert result["clean"]
        assert result["failures"] == []

    def test_real_ping_pair_is_clean(self):
        """Un ARP + un ICMP en buffer es el arranque normal de un ping, no un fallo."""
        result = summarize_trace([
            dict(ICMP_BUFFERED),
            _frame(traffic_type="ARP", destination="Broadcast", sent=True),
        ])
        assert result["clean"]
        assert result["frames"] == 2
        assert result["by_status"] == {"buffered": 1, "sent": 1}

    def test_status_is_written_back_onto_each_frame(self):
        frames = [_frame(sent=True)]
        summarize_trace(frames)
        assert frames[0]["status"] == "sent"

    def test_counts_group_by_device(self):
        result = summarize_trace([
            _frame(device="PC1", sent=True),
            _frame(device="SW1", sent=True),
            _frame(device="SW1", dropped=True),
        ])
        assert result["by_device"] == {"PC1": 1, "SW1": 2}

    def test_failure_carries_the_last_decision_as_the_reason(self):
        """La última decisión es la que explica el desenlace."""
        result = summarize_trace([_frame(dropped=True, device="R1", decisions=[
            {"layer": 3, "inbound": True, "description": "The device receives the frame."},
            {"layer": 3, "inbound": False,
             "description": "The routing table has no matching route. The device drops the packet."},
        ])])
        assert not result["clean"]
        failure = result["failures"][0]
        assert failure["device"] == "R1"
        assert failure["status"] == "dropped"
        assert "no matching route" in failure["reason"]

    def test_failure_without_decisions_does_not_crash(self):
        result = summarize_trace([_frame(dropped=True, decisions=[])])
        assert result["failures"][0]["reason"] == ""

    def test_missing_device_is_tolerated(self):
        result = summarize_trace([{"dropped": True}])
        assert result["by_device"] == {"?": 1}


class TestSimulationReaders:
    """Guards sobre el JS. Son closures en register_tools, así que se verifican
    por texto, igual que TestReconcileWiring en test_live_reconcile.py."""

    def _src(self) -> str:
        return registry_source()

    def test_uses_pts_misspelled_decision_getter(self):
        """PT expone `getFrameDecsionAt`, con el typo. Corregirlo rompe la lectura."""
        assert "getFrameDecsionAt(__j)" in self._src()

    def test_out_port_access_is_guarded_by_count(self):
        """getOutPort(0) lanza cuando el frame está en buffer y no tiene salida."""
        src = self._src()
        assert "__f.getOutPortCount() > 0" in src

    def test_decisions_are_read_as_properties_not_methods(self):
        """FrameDecsion expone description/osiLayer/osiIn como propiedades."""
        src = self._src()
        assert "__d.osiLayer" in src
        assert "__d.description" in src

    def test_step_refuses_to_act_in_realtime(self):
        assert "if (!__s.isSimulationMode())" in self._src()

    def test_step_action_is_validated_before_reaching_js(self):
        """`action` no se interpola: se mapea a una llamada de una lista cerrada."""
        src = self._src()
        assert 'if act not in ("forward", "back", "reset")' in src


class TestExpectedDrops:
    """Seen live (2026-10-25): a 100% ping was summarized as '⚠ did not reach their
    destination' because of a flooded frame and an STP BPDU dropped by a router —
    both normal. Strings are PT 9.0.1's own."""

    FLOOD = ("The frame's destination MAC address does not match the receiving port's MAC "
             "address, the broadcast address, or any multicast address. The device drops the frame.")
    NO_SERVICE = "The device does not have a service that accepts this frame. It drops the frame."

    def _frame(self, reason):
        return {"device": "R1", "dropped": True, "traffic_type": "ICMP",
                "decisions": [{"description": reason}]}

    def test_flooded_frame_and_bpdu_are_not_failures(self):
        out = summarize_trace([self._frame(self.FLOOD), self._frame(self.NO_SERVICE)])
        assert out["clean"] and out["failures"] == []
        assert len(out["expected_drops"]) == 2

    def test_a_real_drop_is_still_a_failure(self):
        out = summarize_trace([self._frame("The routing table does not have a route to the destination IP address. The router drops the packet.")])
        assert not out["clean"] and len(out["failures"]) == 1

    MCAST = ("The destination IP is not the broadcast address and it does not match the "
             "port's IP address. The device drops the packet.")

    def test_multicast_reaching_a_host_is_expected_but_unicast_is_not(self):
        hello = dict(self._frame(self.MCAST), destination="224.0.0.5")
        stray = dict(self._frame(self.MCAST), destination="192.168.1.9")
        out = summarize_trace([hello, stray])
        assert [f["destination"] for f in out["expected_drops"]] == ["224.0.0.5"]
        assert [f["destination"] for f in out["failures"]] == ["192.168.1.9"]


class TestEmptyEndpoints:
    """Live PT 9 (2026-10-30): the ICMP frames of a PDU fired with addSimplePdu came
    back with empty source/destination strings. The reader now also asks the frame
    for its UserTraffic (getTrafficSource) and uses the PDU's endpoints."""

    def _raw(self, **overrides) -> dict:
        frame = {"device": "R2", "source": "", "destination": "", "traffic_type_raw": 0,
                 "pdu_source": "PC1", "pdu_destination": "PC4", "dropped": True,
                 "decisions": [{"description": "The routing table does not have a route."}]}
        frame.update(overrides)
        return frame

    def test_pdu_endpoints_fill_empty_strings(self):
        frame = normalize_frame(self._raw())
        assert (frame["source"], frame["destination"]) == ("PC1", "PC4")
        assert frame["endpoints_from"] == "pdu"
        assert frame["traffic_type"] == "ICMP"
        assert "pdu_source" not in frame and "traffic_type_raw" not in frame

    def test_frame_strings_win_over_the_pdu(self):
        frame = normalize_frame(self._raw(source="192.168.1.10", destination="192.168.4.10"))
        assert frame["destination"] == "192.168.4.10"
        assert "endpoints_from" not in frame

    def test_traffic_without_a_pdu_stays_empty(self):
        frame = normalize_frame(self._raw(pdu_source=None, pdu_destination=None))
        assert frame["destination"] == "" and "endpoints_from" not in frame

    def test_failure_names_the_pdu_destination(self):
        out = summarize_trace([normalize_frame(self._raw())])
        assert out["failures"][0]["destination"] == "PC4"

    def test_reader_asks_for_the_traffic_source_behind_typeof(self):
        src = registry_source()
        assert "typeof __f.getTrafficSource === 'function'" in src
        assert "normalize_frame(frame)" in src
