"""Tests para el Estimator."""

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.estimator import estimate_from_request


class TestEstimator:
    def test_basic_estimate(self):
        req = TopologyRequest(routers=2, pcs_per_lan=3)
        est = estimate_from_request(req)
        assert est["devices"]["routers"] == 2
        assert est["devices"]["pcs"] == 6
        assert est["devices"]["total"] > 0
        assert est["links"]["total"] > 0

    def test_wan_adds_cloud(self):
        req = TopologyRequest(routers=2, has_wan=True)
        est = estimate_from_request(req)
        assert est["devices"]["clouds"] == 1

    def test_complexity_simple(self):
        req = TopologyRequest(routers=1, pcs_per_lan=2)
        est = estimate_from_request(req)
        assert est["complexity"] in ("simple", "moderate")

    def test_complexity_increases(self):
        req_small = TopologyRequest(routers=1, pcs_per_lan=1)
        req_big = TopologyRequest(routers=10, pcs_per_lan=5, has_wan=True, routing="ospf")
        est_small = estimate_from_request(req_small)
        est_big = estimate_from_request(req_big)
        complexity_levels = ["simple", "moderate", "complex", "very complex"]
        assert complexity_levels.index(est_big["complexity"]) >= complexity_levels.index(est_small["complexity"])
