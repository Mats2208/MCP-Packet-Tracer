"""PT device-type ids per model, measured live on PT 9.0.1 (2026-10-25).

lw.addDevice(type, model) creates nothing when `type` is wrong. The category map had
27 models off (3560/3650/IE-2000 are 16, Laptop-PT 18, the ASAs 27, Thing 39...), so
they only appeared thanks to PTBuilder's global addDevice fallback.
"""

from __future__ import annotations

import pytest

from src.packet_tracer_mcp.domain.models.plans import DevicePlan, TopologyPlan
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import generate_ptbuilder_script
from src.packet_tracer_mcp.shared.constants import pt_device_type


@pytest.mark.parametrize("model,category,expected", [
    ("2911", "router", 0), ("2960-24TT", "switch", 1), ("3560-24PS", "multilayer_switch", 16),
    ("Laptop-PT", "laptop", 18), ("Printer-PT", "printer", 10), ("5506-X", "firewall", 27),
    ("LAP-PT", "accesspoint", 44), ("Thing", "iot", 39), ("WLC-2504", "wlc", 41),
])
def test_measured_ids(model, category, expected):
    assert pt_device_type(model, category) == expected


def test_generator_uses_the_model_id():
    plan = TopologyPlan(devices=[DevicePlan(name="L1", model="Laptop-PT", category="laptop")])
    assert 'lwAddDevice("L1", 18, "Laptop-PT"' in generate_ptbuilder_script(plan)


def test_iot_nm_modules_are_type_23():
    """Measured live: PT-IOT-NM-1CFE on MCU-PT and PT-IOT-NM-1W on SBC-PT install with
    type 23 and are refused with 22 (2026-10-25)."""
    from src.packet_tracer_mcp.infrastructure.catalog.modules import ALL_MODULES
    for name in ("PT-IOT-NM-1CE", "PT-IOT-NM-1CFE", "PT-IOT-NM-1CGE", "PT-IOT-NM-1W"):
        assert ALL_MODULES[name].module_type == 23, name
