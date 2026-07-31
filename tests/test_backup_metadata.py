"""Tests de respaldo y metadatos del proyecto.

Ambas tools son closures en register_tools, así que se verifican por texto igual
que TestReconcileWiring en test_live_reconcile.py. La forma de los datos está
tomada de una lectura real contra PT 9.0.0.0810.
"""

from pathlib import Path


def _src() -> str:
    return Path("src/packet_tracer_mcp/adapters/mcp/tool_registry.py").read_text(
        encoding="utf-8"
    )


class TestBackupConfig:
    def test_startup_is_split_on_commas(self):
        """PT devuelve la startup-config con las líneas separadas por COMAS.

        Sin reconstruirla, el respaldo sale en una sola línea y no se puede
        pegar en una CLI.
        """
        src = _src()
        assert 'startup.split(",")' in src
        assert r'"\n".join(lines)' in src

    def test_xml_dump_is_capped(self):
        """serializeToXml devuelve ~19k chars por dispositivo; sin techo, una
        topología mediana desborda la respuesta de la tool."""
        src = _src()
        assert "_MAX_BACKUP_XML" in src
        assert "substring(0, {_MAX_BACKUP_XML})" in src

    def test_xml_is_opt_in(self):
        assert "include_xml: bool = False" in _src()

    def test_hosts_are_reported_not_crashed(self):
        src = _src()
        assert "typeof __d.getStartupFile !== 'function'" in src

    def test_empty_startup_tells_the_user_what_to_do(self):
        """Un equipo sin `write memory` no tiene startup-config: no es un error."""
        assert "write memory` en el equipo antes de respaldar" in _src()


class TestProjectMetadata:
    def test_description_write_is_opt_in(self):
        """Sin argumento la tool es de solo lectura."""
        src = _src()
        assert 'description: str = ""' in src
        assert "if new_desc else \"\"" in src

    def test_description_goes_through_json_dumps(self):
        assert "setNetworkDescription({json.dumps(new_desc)})" in _src()

    def test_unsaved_project_is_flagged(self):
        """Un proyecto sin guardar se pierde al cerrar PT; hay que decirlo."""
        assert "Proyecto SIN guardar" in _src()

    def test_setter_is_feature_detected(self):
        assert "typeof __f.setNetworkDescription === 'function'" in _src()


class TestWorkspaceOptions:
    def test_negative_polarity_setters_are_inverted(self):
        """PT expone dos de estas en negativo (`setDisableAutoCabling`,
        `setHideDevLabel`). Si el flag amistoso no se invierte, la tool hace
        exactamente lo contrario de lo que pide el usuario y en silencio."""
        src = _src()
        assert "setDisableAutoCabling({'false' if auto_cabling == 1 else 'true'})" in src
        assert "setHideDevLabel({'false' if show_device_labels == 1 else 'true'})" in src

    def test_positive_polarity_setters_are_not_inverted(self):
        src = _src()
        assert "setIsPortShown({'true' if show_port_labels == 1 else 'false'})" in src
        assert "setIsLinkLightShown({'true' if show_link_lights == 1 else 'false'})" in src

    def test_readback_undoes_the_inversion(self):
        """Lo que se devuelve tiene que estar en la misma polaridad que el input."""
        src = _src()
        assert "auto_cabling: !__o.isAutoCablingDisabled()" in src
        assert "show_device_labels: !__o.isHideDevLabel()" in src

    def test_all_flags_default_to_no_change(self):
        src = _src()
        for flag in ("auto_cabling", "external_network_access", "show_port_labels",
                     "show_link_lights", "show_device_labels"):
            assert f"{flag}: int = -1" in src

    def test_external_network_access_is_called_out(self):
        """Sacar tráfico del simulador a la red real merece un aviso."""
        assert "acceso a la red REAL habilitado" in _src()
