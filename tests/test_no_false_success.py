"""pt_full_build / pt_live_deploy deployed invalid plans; pt_save_project overwrote files."""

from __future__ import annotations

import json

from src.packet_tracer_mcp.adapters.mcp.tools import live
from src.packet_tracer_mcp.adapters.mcp.tools.live import save_target_exists
from src.packet_tracer_mcp.domain.models.errors import ErrorCode, PlanError, ValidationResult
from tests._fake_ctx import FakeCtx, tools


# ----------------------------------------------------------------------
# pt_full_build / pt_live_deploy: no deploy of an invalid plan
# ----------------------------------------------------------------------

BAD_PLAN = json.dumps({"name": "t", "devices": [
    {"name": "X1", "model": "NOT-A-MODEL", "category": "router"}], "links": []})


class TestNoInvalidDeploy:
    def test_live_deploy_refuses(self):
        ctx = FakeCtx(replies=["[]"])
        out = tools(live, ctx)["pt_live_deploy"](plan_json=BAD_PLAN)
        assert out.startswith("Not deployed") and "ERROR [" in out
        assert ctx.queued == []

    def test_full_build_refuses(self, monkeypatch):
        real = live.plan_from_request

        def failing(request):
            plan, _ = real(request)
            return plan, ValidationResult(errors=[PlanError(
                code=ErrorCode.VALIDATION_ERROR, message="broken on purpose")])

        monkeypatch.setattr(live, "plan_from_request", failing)
        ctx = FakeCtx()
        out = tools(live, ctx)["pt_full_build"]()
        assert "NOT DEPLOYED" in out and "DEPLOYMENT TO PACKET TRACER" not in out
        assert ctx.queued == [] and ctx.sent == []

    def test_full_build_still_deploys_a_valid_plan(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ctx = FakeCtx()
        out = tools(live, ctx)["pt_full_build"](routers=1, pcs_per_lan=1)
        assert "DEPLOYMENT TO PACKET TRACER" in out and ctx.queued


# ----------------------------------------------------------------------
# pt_save_project: no silent overwrite
# ----------------------------------------------------------------------

class TestSaveProject:
    def test_existing_local_file_is_refused(self, tmp_path):
        (tmp_path / "lab.pkt").write_bytes(b"x")
        ctx = FakeCtx()
        out = tools(live, ctx)["pt_save_project"](filename="lab", directory=str(tmp_path))
        assert out.startswith("Not saved") and "overwrite=True" in out
        assert ctx.sent == []

    def test_overwrite_sends(self, tmp_path):
        (tmp_path / "lab.pkt").write_bytes(b"x")
        ctx = FakeCtx(replies=["OK:C:/x/lab.pkt|10"])
        out = tools(live, ctx)["pt_save_project"](filename="lab", directory=str(tmp_path),
                                                  overwrite=True)
        assert out.startswith("Project saved")
        assert "if(!true&&fm.fileExists(full))" in ctx.sent[0]

    def test_js_checks_pt_default_folder(self):
        ctx = FakeCtx(replies=["EXISTS:C:/Users/u/Cisco/lab.pkt"])
        out = tools(live, ctx)["pt_save_project"](filename="lab")
        js = ctx.sent[0]
        assert "if(!false&&fm.fileExists(full)){reportResult('EXISTS:'+full);}" in js
        assert js.index("fileExists(full)") < js.index("fileSaveAsNoPrompt")
        assert out.startswith("Not saved") and "overwrite=True" in out

    def test_save_target_exists(self, tmp_path):
        assert save_target_exists(str(tmp_path), "nope.pkt") is False
        (tmp_path / "a.pkt").write_bytes(b"")
        assert save_target_exists(str(tmp_path), "a.pkt") is True
