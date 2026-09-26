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
    interpret_ping,
)
from src.packet_tracer_mcp.domain.models.plans import TopologyPlan, DevicePlan, DHCPPool
from src.packet_tracer_mcp.domain.rules.device_rules import validate_devices
from src.packet_tracer_mcp.domain.rules.ip_rules import validate_dhcp
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_ptbuilder_script,
)
from src.packet_tracer_mcp.infrastructure.execution.manual_executor import ManualExecutor
from src.packet_tracer_mcp.application.use_cases.apply_hardening import (
    build_hardening_config,
    apply_hardening_uc,
)
from src.packet_tracer_mcp.application.use_cases.apply_vlan import (
    build_vlan_plan,
    apply_vlan_uc,
)
from src.packet_tracer_mcp.application.use_cases.apply_acl import (
    build_acl_plan,
    apply_acl_uc,
    remove_acl_uc,
)
from src.packet_tracer_mcp.application.use_cases.apply_nat import (
    build_nat_config,
    apply_nat_uc,
    remove_nat_uc,
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


@pytest.mark.parametrize(
    "stat,expected",
    [
        # Formato host (PC/Server), verificado contra PT 9.0 en vivo
        ("Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),", True),
        ("Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),", False),
        ("Packets: Sent = 4, Received = 2, Lost = 2 (50% loss),", True),
        # Formato IOS (router/switch)
        ("Success rate is 100 percent (5/5)", True),
        ("Success rate is 0 percent (0/5)", False),
        ("Success rate is 80 percent (4/5)", True),
        # Basura / vacío
        ("", False),
        ("no stats here", False),
    ],
)
def test_interpret_ping(stat, expected):
    """El parser de conectividad, con los dos formatos reales de PT."""
    assert interpret_ping(stat) is expected


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


# --- Inyección vía nombre de VLAN / remark de ACL / nombre de dispositivo --


def test_vlan_name_with_newline_is_rejected():
    """The VLAN name was interpolated raw into `name {v.name}` inside the
    configureIosDevice payload; a \\n became an extra IOS command."""
    plan = build_vlan_plan(
        switch="SW1",
        vlans=[{"vlan_id": 10, "name": "DATA\nusername hacker privilege 15 secret x"}],
    )
    sent = []
    result = apply_vlan_uc(plan, bridge_send=lambda js: sent.append(js) or True)
    assert not result["valid"]
    assert not result["sent"]
    assert not sent


def test_legitimate_vlan_still_works():
    plan = build_vlan_plan(switch="SW1", vlans=[{"vlan_id": 10, "name": "DATA"}])
    sent = []
    result = apply_vlan_uc(plan, bridge_send=lambda js: sent.append(js) or True)
    assert result["valid"]
    assert result["sent"]
    assert len(sent) == 1


def test_acl_remark_with_newline_is_rejected():
    """The remark was interpolated raw into `access-list N remark {entry.remark}`
    inside the configureIosDevice payload; a \\n smuggled in an extra IOS command."""
    plan = build_acl_plan(
        router="R1",
        name_or_number="10",
        acl_type="standard",
        entries_dicts=[{
            "action": "permit",
            "source": "any",
            "remark": "ok\nusername hacker privilege 15 secret x",
        }],
    )
    sent = []
    result = apply_acl_uc(plan, bridge_send=lambda js: sent.append(js) or True)
    assert not result["valid"]
    assert not result["sent"]
    assert not sent


def test_device_name_with_newline_is_rejected():
    """The device name was interpolated raw into `hostname {router.name}`."""
    plan = TopologyPlan(
        name="t",
        devices=[DevicePlan(
            name="R1\nusername hacker privilege 15 secret x",
            model="2911", category="router",
        )],
        links=[],
    )
    errors = validate_devices(plan)
    assert any(e.code.value == "DEVICE_INVALID_NAME" for e in errors)


def test_dhcp_pool_name_with_newline_is_rejected():
    """pool_name was interpolated raw into `ip dhcp pool {pool.pool_name}`."""
    plan = TopologyPlan(
        name="t",
        devices=[DevicePlan(
            name="R1", model="2911", category="router",
            interfaces={"GigabitEthernet0/0": "10.0.0.1/24"},
        )],
        links=[],
        dhcp_pools=[DHCPPool(
            router="R1",
            pool_name="LAN\nusername hacker privilege 15 secret x",
            network="10.0.0.0", mask="255.255.255.0", gateway="10.0.0.1",
        )],
    )
    errors = validate_dhcp(plan)
    assert any(e.code.value == "DHCP_INVALID_POOL_NAME" for e in errors)


# --- Inyección vía nombre de ACL nombrada / pool NAT / rutas de remove -----

HOSTILE_SUFFIX = "\nusername hacker privilege 15 secret x"


def test_named_acl_name_with_newline_is_rejected():
    """Un nombre no numérico se aceptaba sin mirar: `access-list {name} ...`
    llevaba el comando extra al dispositivo."""
    plan = build_acl_plan(
        router="R1",
        name_or_number="BLOCK" + HOSTILE_SUFFIX,
        acl_type="standard",
        entries_dicts=[{"action": "permit", "source": "any"}],
    )
    sent = []
    result = apply_acl_uc(plan, bridge_send=lambda js: sent.append(js) or True)
    assert not result["valid"]
    assert not sent


def test_remove_acl_with_newline_is_not_sent():
    """remove_acl_uc no validaba nada: el nombre iba directo a `no access-list`."""
    sent = []
    result = remove_acl_uc(
        router="R1", name_or_number="BLOCK" + HOSTILE_SUFFIX,
        bridge_send=lambda js: sent.append(js) or True,
    )
    assert not result["valid"]
    assert not result["sent"]
    assert not sent


def _nat(**overrides):
    args = dict(
        router="R1", mode="dynamic",
        inside_interface="GigabitEthernet0/1", outside_interface="GigabitEthernet0/0",
        inside_networks=["192.168.0.0 0.0.0.255"],
        pool_name="PUBLIC", pool_start="200.0.0.1", pool_end="200.0.0.10",
        pool_netmask="255.255.255.0",
    )
    args.update(overrides)
    return build_nat_config(**args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pool_name", "PUBLIC" + HOSTILE_SUFFIX),
        ("acl_number", "1" + HOSTILE_SUFFIX),
    ],
)
def test_nat_fields_with_newline_are_rejected(field, value):
    """pool_name y acl_number se interpolaban crudos en `ip nat pool {name}` y
    `ip nat inside source list {acl} pool {name}`."""
    sent = []
    result = apply_nat_uc(_nat(**{field: value}), bridge_send=lambda js: sent.append(js) or True)
    assert not result["valid"], f"{field} aceptó un salto de línea"
    assert not sent


def test_legitimate_nat_still_works():
    sent = []
    result = apply_nat_uc(_nat(), bridge_send=lambda js: sent.append(js) or True)
    assert result["valid"]
    assert result["sent"]
    assert len(sent) == 1


def test_remove_nat_with_newline_is_not_sent():
    sent = []
    result = remove_nat_uc(
        router="R1", mode="dynamic",
        inside_interface="GigabitEthernet0/1", outside_interface="GigabitEthernet0/0",
        pool_name="PUBLIC" + HOSTILE_SUFFIX,
        bridge_send=lambda js: sent.append(js) or True,
    )
    assert not result["valid"]
    assert not result["sent"]
    assert not sent


def test_legitimate_remove_still_sends():
    sent = []
    result = remove_acl_uc(router="R1", name_or_number="BLOCK",
                           bridge_send=lambda js: sent.append(js) or True)
    assert result["valid"]
    assert result["sent"]
    assert len(sent) == 1


@pytest.mark.parametrize("sep", ["\u2028", "\u2029"])
def test_unicode_line_separators_are_rejected_too(sep):
    """U+2028/U+2029 terminan una línea en JS igual que \\n; netflow_rules ya los
    rechazaba y ahora todas las reglas comparten el mismo chequeo."""
    plan = build_vlan_plan(switch="SW1", vlans=[{"vlan_id": 10, "name": "DATA" + sep}])
    sent = []
    result = apply_vlan_uc(plan, bridge_send=lambda js: sent.append(js) or True)
    assert not result["valid"]
    assert not sent
