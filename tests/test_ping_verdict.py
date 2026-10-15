"""Veredicto del ping y consola IOS.

Dos regresiones distintas, las dos en pt_verify_connectivity:

1. `interpret_ping` devolvia un bool "llego al menos uno", asi que 1 de 4
   paquetes se reportaba como "CONECTIVIDAD OK" igual que 4 de 4. Un enlace
   agonizante se veia idéntico a uno sano.
2. El JS pedia `getCommandPrompt()`, que SOLO existe en hosts. Contra un router
   reventaba con `TypeError: Property 'getCommandPrompt' of object is not a
   function`, pese a que el docstring documenta el formato IOS. Verificado
   contra PT 9.0.1: los routers exponen `getCommandLine()`, y los PCs exponen
   las dos, asi que `getCommandLine()` sirve para ambos.
"""

import pytest

from src.packet_tracer_mcp.shared.utils import classify_ping, interpret_ping
from src.packet_tracer_mcp.adapters.mcp.tools.live import (
    console_ping_arm_js, console_ping_poll_js,
)


HOST_FULL = "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
HOST_PARTIAL = "Packets: Sent = 4, Received = 1, Lost = 3 (75% loss)"
HOST_NONE = "Packets: Sent = 4, Received = 0, Lost = 4 (100% loss)"
IOS_FULL = "Success rate is 100 percent (5/5)"
IOS_PARTIAL = "Success rate is 80 percent (4/5)"
IOS_NONE = "Success rate is 0 percent (0/5)"


class TestClassifyPing:
    @pytest.mark.parametrize("stat,expected", [
        (HOST_FULL, "ok"),
        (HOST_PARTIAL, "partial"),
        (HOST_NONE, "none"),
        (IOS_FULL, "ok"),
        (IOS_PARTIAL, "partial"),
        (IOS_NONE, "none"),
        ("", "none"),
        ("basura sin estadisticas", "none"),
    ])
    def test_classification(self, stat, expected):
        assert classify_ping(stat) == expected

    def test_partial_loss_is_not_the_same_as_full_success(self):
        """El bug: 1 de 4 se reportaba igual que 4 de 4."""
        assert classify_ping(HOST_PARTIAL) != classify_ping(HOST_FULL)

    def test_interpret_ping_keeps_its_boolean_contract(self):
        """Sigue siendo 'llego al menos uno' para quien ya lo usaba."""
        assert interpret_ping(HOST_PARTIAL) is True
        assert interpret_ping(HOST_NONE) is False
        assert interpret_ping(IOS_FULL) is True


class TestIosConsoleJs:
    def test_arm_js_uses_getcommandline(self):
        js = console_ping_arm_js("R1", "192.168.5.1")
        assert "getCommandLine()" in js

    def test_arm_js_never_uses_the_host_only_api(self):
        """getCommandPrompt no existe en IOS: es lo que rompia el ping."""
        js = console_ping_arm_js("R1", "192.168.5.1")
        assert "getCommandPrompt" not in js

    def test_poll_js_never_uses_the_host_only_api(self):
        js = console_ping_poll_js("R1", 0)
        assert "getCommandPrompt" not in js
        assert "getCommandLine()" in js

    def test_arm_js_answers_the_setup_dialog(self):
        """Los routers arrancan parados en 'initial configuration dialog'.

        Verificado contra PT 9.0.1: sin responderlo, el `ping` se consume como
        respuesta al yes/no y nunca se ejecuta.
        """
        js = console_ping_arm_js("R1", "192.168.5.1")
        assert "yes/no" in js

    def test_arm_js_carries_the_target(self):
        js = console_ping_arm_js("R1", "10.0.0.9")
        assert "ping 10.0.0.9" in js

    def test_device_name_is_escaped(self):
        """Un nombre con comillas no puede romper el JS generado."""
        js = console_ping_arm_js('R"1', "10.0.0.9")
        assert 'getDevice("R\\"1")' in js or "getDevice(\"R\\\"1\")" in js
