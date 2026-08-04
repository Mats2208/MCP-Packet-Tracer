"""Regresiones de la segunda pasada de auditoría (probada contra PT 9.0.1).

Un test por defecto encontrado manejando el MCP contra Packet Tracer real.
Cada uno falla si el fix se revierte.
"""

from pathlib import Path

import pytest

from src.packet_tracer_mcp.infrastructure.catalog.modules import (
    resolve_module,
    ports_for_slot,
)
from src.packet_tracer_mcp.infrastructure.catalog.devices import category_of_model
from src.packet_tracer_mcp.infrastructure.catalog.cables import infer_cable
from src.packet_tracer_mcp.domain.services.auto_fixer import fix_plan
from src.packet_tracer_mcp.domain.models.plans import TopologyPlan, DevicePlan, LinkPlan


# --- Puertos de un módulo según el slot -----------------------------------


class TestPortsForSlot:
    """Un HWIC-2T en "0/1" da Serial0/1/x, no Serial0/0/x.

    `ModuleSpec.ports_added` los lista para el primer slot de la familia. Al
    devolverlos crudos, un batch de dos HWIC-2T informaba los MISMOS puertos
    para ambos, y cablear al nombre reportado fallaba.
    """

    def test_hwic_in_first_slot_is_unchanged(self):
        spec = resolve_module("HWIC-2T")
        assert ports_for_slot(spec, "0/0") == ["Serial0/0/0", "Serial0/0/1"]

    @pytest.mark.parametrize(
        "slot,expected",
        [
            ("0/1", ["Serial0/1/0", "Serial0/1/1"]),
            ("0/2", ["Serial0/2/0", "Serial0/2/1"]),
            ("0/3", ["Serial0/3/0", "Serial0/3/1"]),
        ],
    )
    def test_hwic_carries_the_slot_into_the_port_name(self, slot, expected):
        assert ports_for_slot(resolve_module("HWIC-2T"), slot) == expected

    def test_two_modules_on_one_router_do_not_collide(self):
        """El caso real: 4 puertos seriales con dos HWIC-2T."""
        spec = resolve_module("HWIC-2T")
        combined = ports_for_slot(spec, "0/0") + ports_for_slot(spec, "0/1")
        assert len(set(combined)) == 4, f"puertos duplicados: {combined}"

    def test_nim_slot_is_rewritten_too(self):
        spec = resolve_module("NIM-2T")
        assert ports_for_slot(spec, "0/2") == ["Serial0/2/0", "Serial0/2/1"]

    def test_nm_slot_without_subslot_is_left_alone(self):
        """Un NM usa slot "1" y sus puertos ya vienen bien del catálogo."""
        spec = resolve_module("NM-4A/S")
        assert ports_for_slot(spec, "1") == list(spec.ports_added)

    def test_garbage_slot_falls_back_to_the_catalog(self):
        spec = resolve_module("HWIC-2T")
        assert ports_for_slot(spec, "no/es-un-slot") == list(spec.ports_added)


# --- Inferencia de cable por modelo ---------------------------------------


class TestCableInferenceUsesModel:
    """PT clasifica por comportamiento, no por rol de red.

    `getClassName()` devuelve "Router" para un 3560 (switch multicapa) y
    "CiscoDevice" para un 2960, así que la categoría "switch" no llegaba nunca
    a CABLE_RULES y todo router↔switch se cableaba cruzado. La categoría sale
    del MODELO, que sí discrimina.
    """

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("1941", "router"),
            ("2911", "router"),
            ("3560-24PS", "switch"),   # el que PT llama "Router"
            ("2960-24TT", "switch"),   # el que PT llama "CiscoDevice"
            ("PC-PT", "pc"),
            ("Server-PT", "server"),
        ],
    )
    def test_model_maps_to_catalog_category(self, model, expected):
        assert category_of_model(model) == expected

    def test_unknown_model_is_empty_not_a_guess(self):
        assert category_of_model("NoExisteEsteModelo") == ""

    @pytest.mark.parametrize("switch", ["3560-24PS", "2960-24TT"])
    def test_router_to_switch_is_straight(self, switch):
        """El bug: daba 'cross' para el 3560 por venir como "Router"."""
        cable = infer_cable(category_of_model("1941"), category_of_model(switch))
        assert cable == "straight"

    def test_router_to_router_is_still_cross(self):
        cable = infer_cable(category_of_model("2911"), category_of_model("1941"))
        assert cable == "cross"

    def test_switch_to_pc_is_straight(self):
        cable = infer_cable(category_of_model("3560-24PS"), category_of_model("PC-PT"))
        assert cable == "straight"


# --- fix_plan mueve la IP con el puerto ------------------------------------


class TestRenameRejectsDuplicates:
    """Renombrar a un nombre ocupado dejaba DOS dispositivos iguales.

    PT lo permite sin chistar, y a partir de ahí `getDevice(nombre)` solo
    resuelve a uno: el otro sigue en el canvas pero es inalcanzable por nombre,
    así que toda tool que lo referencie trabaja en silencio sobre el
    equivocado. Reproducido contra PT 9.0.1 renombrando un 2960 a "R1", que
    era un 2911: `getDevices("")` pasó a listar "R1" dos veces.

    `pt_add_device` sí validaba; `pt_rename_device` no.
    """

    def _src(self) -> str:
        return Path(
            "src/packet_tracer_mcp/adapters/mcp/tool_registry.py"
        ).read_text(encoding="utf-8")

    def test_rename_checks_the_target_name_is_free(self):
        block = self._src().split("def pt_rename_device", 1)[1][:2000]
        assert 'getDevice("{safe_new}")' in block, "no se comprueba si el nombre ya existe"
        assert "ERROR:DUPLICATE" in block

    def test_duplicate_is_reported_with_a_reason(self):
        block = self._src().split("def pt_rename_device", 1)[1][:3000]
        assert "ya existe un dispositivo llamado" in block


def test_fix_plan_moves_the_ip_to_the_corrected_port():
    """Corregir el puerto de un enlace tiene que arrastrar su direccionamiento.

    Antes el enlace pasaba a la interfaz nueva y la IP se quedaba en la vieja
    —que ya no usaba ningún enlace—, así que el plan salía "arreglado" pero
    internamente inconsistente.
    """
    plan = TopologyPlan(
        name="t",
        devices=[
            DevicePlan(
                name="R1", model="2911", category="router", role="core_router",
                x=0, y=0,
                interfaces={"GigabitEthernet0/9": "192.168.0.1/24"},
                interfaces_v6={"GigabitEthernet0/9": "2001:db8::1/64"},
            ),
            DevicePlan(
                name="SW1", model="2960-24TT", category="switch",
                role="access_switch", x=0, y=0,
            ),
        ],
        links=[
            LinkPlan(
                device_a="R1", port_a="GigabitEthernet0/9",
                device_b="SW1", port_b="GigabitEthernet0/1", cable="straight",
            )
        ],
    )

    fixed, _ = fix_plan(plan)
    r1 = fixed.device_by_name("R1")
    new_port = fixed.links[0].port_a

    assert new_port != "GigabitEthernet0/9", "no se corrigió el puerto del enlace"
    assert "GigabitEthernet0/9" not in r1.interfaces, "la IP quedó en el puerto viejo"
    assert r1.interfaces[new_port] == "192.168.0.1/24"
    assert r1.interfaces_v6[new_port] == "2001:db8::1/64"
