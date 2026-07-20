"""Tests adversariales: un caso por vulnerabilidad, falla si el fix se revierte.

Antes de esto la suite era enteramente happy-path: ningún test metía una comilla,
un salto de línea ni un '..' en ningún campo.
"""

import json

import pytest

from src.packet_tracer_mcp.shared.utils import (
    js_escape,
    safe_name_component,
    resolve_within,
)
from src.packet_tracer_mcp.domain.models.plans import TopologyPlan, DevicePlan
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_ptbuilder_script,
)
from src.packet_tracer_mcp.infrastructure.execution.manual_executor import ManualExecutor
from src.packet_tracer_mcp.application.use_cases.apply_hardening import (
    build_hardening_config,
    apply_hardening_uc,
)

HOSTILE_NAMES = [
    "R'1",
    'R"1',
    "R\\1",
    "R\n1",
    "R\r1",
    "R\u20281",
    "'); alert(1); ('",
    '"); alert(1); ("',
]


# --- Escapado JS -----------------------------------------------------------


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_js_escape_leaves_no_literal_terminator(name):
    """Ni comillas sin escapar ni fines de línea, que JS no permite en literales."""
    out = js_escape(name)
    # Toda comilla del resultado debe venir precedida de un backslash.
    for i, ch in enumerate(out):
        if ch in "\"'":
            assert i > 0 and out[i - 1] == "\\", f"comilla sin escapar en {out!r}"
    for terminator in ("\n", "\r", "\u2028", "\u2029"):
        assert terminator not in out


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_generated_ptbuilder_script_keeps_names_as_data(name):
    """Un nombre hostil debe salir como string, no como código.

    Antes, lwAddDevice() se construía con un f-string crudo: una comilla doble
    cerraba el literal y el resto se ejecutaba en el Script Engine de PT.
    """
    plan = TopologyPlan(
        name="t",
        devices=[DevicePlan(name=name, model="2911", category="router")],
        links=[],
    )
    line = generate_ptbuilder_script(plan)
    assert line.startswith("lwAddDevice(")

    # El primer argumento tiene que ser un literal JSON que round-trippee al
    # nombre original: si round-trippea, no se escapó del literal.
    arg = line[len("lwAddDevice("):].split(", ", 1)[0]
    assert json.loads(arg) == name


# --- Rutas -----------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    ["../../evil", "..\\..\\evil", "..", ".", "/etc/passwd", "C:/Windows/Temp"],
)
def test_project_names_cannot_escape_the_base(hostile):
    assert "/" not in safe_name_component(hostile)
    assert "\\" not in safe_name_component(hostile)
    assert safe_name_component(hostile) not in ("..", ".")


def test_resolve_within_rejects_escapes(tmp_path):
    with pytest.raises(ValueError):
        resolve_within(tmp_path, "..", "evil")
    inside = resolve_within(tmp_path, "demo")
    assert inside.parent == tmp_path.resolve()


def test_export_writes_nothing_outside_the_output_dir(tmp_path):
    """La propiedad que importa: no se creó nada fuera de la base."""
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"

    plan = TopologyPlan(
        name="t",
        devices=[DevicePlan(name="R1", model="2911", category="router")],
        links=[],
    )
    ManualExecutor(output_dir=base).execute(plan, project_name="../outside/pwned")

    assert list(outside.iterdir()) == [], "se escribió fuera del directorio base"
    assert base.exists()


def test_device_names_cannot_escape_via_config_filename(tmp_path):
    """El nombre del dispositivo se interpolaba crudo en `{name}_config.txt`."""
    from pathlib import Path

    base = (tmp_path / "base").resolve()
    plan = TopologyPlan(
        name="t",
        devices=[DevicePlan(name="../../pwned", model="2911", category="router")],
        links=[],
    )
    result = ManualExecutor(output_dir=base).execute(plan, project_name="p")

    for path in result["files"].values():
        assert Path(path).resolve().is_relative_to(base), f"{path} quedó fuera de {base}"


# --- Inyección de comandos IOS --------------------------------------------


def test_banner_delimiter_is_rejected():
    """'#' delimita `banner motd`; en el texto corta el banner y IOS ejecuta el resto."""
    cfg = build_hardening_config(device="R1", banner_motd="Hola # enable secret pwned")
    result = apply_hardening_uc(cfg, bridge_send=lambda js: True)
    assert not result["valid"]
    assert not result["sent"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("hostname", "R1\nusername hacker privilege 15 secret x"),
        ("enable_secret", "s3cr3t\nno service password-encryption"),
        ("banner_motd", "hola\nlogging host 10.0.0.1"),
    ],
)
def test_newlines_in_hardening_fields_are_rejected(field, value):
    """Cada \\n se convertía en un comando IOS extra en el dispositivo."""
    cfg = build_hardening_config(device="R1", **{field: value})
    result = apply_hardening_uc(cfg, bridge_send=lambda js: True)
    assert not result["valid"], f"{field} aceptó un salto de línea"
    assert not result["sent"]


def test_legitimate_hardening_still_works():
    """El contrapeso: los fixes no pueden romper el caso normal."""
    cfg = build_hardening_config(
        device="R1", hostname="R1", banner_motd="Acceso restringido",
        enable_secret="cisco123",
    )
    sent = []
    result = apply_hardening_uc(cfg, bridge_send=lambda js: sent.append(js) or True)
    assert result["valid"]
    assert result["sent"]
    assert len(sent) == 1
