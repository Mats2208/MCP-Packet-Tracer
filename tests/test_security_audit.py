"""Tests de auditoría de seguridad (pt_audit_security).

La forma de los dicts de entrada está verificada contra PT 9.0.0.0810: un 2911
con `enable secret cisco123`, `service password-encryption`, `username admin
secret`, `username oper password` y `banner motd` devolvió enable secret con
prefijo "$1$", admin con "$1$" y oper con hex type-7.
"""

from pathlib import Path

import pytest

from src.packet_tracer_mcp.domain.services.security_audit import (
    CONFIG_REGISTER_BYPASS,
    CONFIG_REGISTER_NORMAL,
    audit_security,
)


def _device(**overrides) -> dict:
    """Un dispositivo endurecido; los tests degradan lo que quieren probar."""
    base = {
        "name": "R1",
        "model": "2911",
        "hostname": "R1",
        "enable_secret_set": True,
        "enable_secret_algo": "scrypt",
        "enable_password_set": False,
        "service_password_encryption": True,
        "banner_set": True,
        "users": [{"name": "admin", "algo": "scrypt"}],
        "config_register": CONFIG_REGISTER_NORMAL,
    }
    base.update(overrides)
    return base


def _codes(result: dict) -> set[str]:
    return {f["code"] for f in result["findings"]}


def _by_code(result: dict, code: str) -> dict:
    return next(f for f in result["findings"] if f["code"] == code)


class TestSecurityAuditBaseline:
    def test_hardened_device_is_clean(self):
        result = audit_security([_device()])
        assert result["secure"]
        assert result["findings"] == []
        assert result["devices_audited"] == 1

    def test_empty_topology(self):
        result = audit_security([])
        assert result["devices_audited"] == 0
        assert result["findings"] == []

    def test_bare_device_reports_the_obvious_gaps(self):
        result = audit_security([_device(
            enable_secret_set=False,
            enable_secret_algo=None,
            service_password_encryption=False,
            banner_set=False,
            users=[],
        )])
        assert not result["secure"]
        assert "NO_ENABLE_SECRET" in _codes(result)
        assert "NO_SERVICE_PASSWORD_ENCRYPTION" in _codes(result)
        assert "NO_LOCAL_USERS" in _codes(result)
        assert "NO_BANNER_MOTD" in _codes(result)

    def test_every_finding_carries_a_suggestion(self):
        """El consumidor es un LLM que tiene que poder autocorregirse."""
        result = audit_security([_device(
            enable_secret_set=False,
            service_password_encryption=False,
            banner_set=False,
            users=[{"name": "oper", "algo": "type7"}],
            config_register=CONFIG_REGISTER_BYPASS,
        )])
        assert result["findings"]
        for f in result["findings"]:
            assert f["suggestion"].strip()
            assert f["severity"] in ("high", "medium", "low")
            assert f["device"] == "R1"


class TestCredentialAlgorithms:
    def test_reversible_user_credential_is_high(self):
        result = audit_security([_device(users=[{"name": "oper", "algo": "type7"}])])
        finding = _by_code(result, "USER_CREDENTIAL_REVERSIBLE")
        assert finding["severity"] == "high"
        assert "oper" in finding["message"]

    def test_md5_user_credential_is_only_low(self):
        """MD5 es crackeable pero no reversible: un grado menos que type 7."""
        result = audit_security([_device(users=[{"name": "admin", "algo": "md5"}])])
        assert _by_code(result, "USER_CREDENTIAL_WEAK_ALGO")["severity"] == "low"
        assert "USER_CREDENTIAL_REVERSIBLE" not in _codes(result)

    def test_md5_enable_secret_is_medium(self):
        result = audit_security([_device(enable_secret_algo="md5")])
        assert _by_code(result, "ENABLE_SECRET_WEAK_ALGO")["severity"] == "medium"

    def test_reversible_enable_secret_is_high(self):
        result = audit_security([_device(enable_secret_algo="plaintext")])
        assert _by_code(result, "ENABLE_SECRET_REVERSIBLE")["severity"] == "high"

    @pytest.mark.parametrize("algo", ["scrypt", "pbkdf2"])
    def test_modern_hashes_raise_nothing(self, algo):
        result = audit_security([_device(enable_secret_algo=algo, users=[{"name": "a", "algo": algo}])])
        assert result["findings"] == []

    def test_enable_password_flagged_even_with_a_secret(self):
        """Pueden coexistir; el password queda guardado de forma reversible."""
        result = audit_security([_device(enable_password_set=True)])
        assert _by_code(result, "ENABLE_PASSWORD_PRESENT")["severity"] == "medium"


class TestConfigRegister:
    def test_bypass_register_is_high(self):
        result = audit_security([_device(config_register=CONFIG_REGISTER_BYPASS)])
        finding = _by_code(result, "CONFIG_REGISTER_BYPASS")
        assert finding["severity"] == "high"
        assert "0x2142" in finding["message"]

    def test_normal_register_is_silent(self):
        result = audit_security([_device(config_register=CONFIG_REGISTER_NORMAL)])
        assert "CONFIG_REGISTER_BYPASS" not in _codes(result)

    def test_unknown_register_is_silent(self):
        """Un modelo que no expone el registro no debe generar ruido."""
        result = audit_security([_device(config_register=None)])
        assert "CONFIG_REGISTER_BYPASS" not in _codes(result)


class TestAggregation:
    def test_counts_and_ordering(self):
        result = audit_security([
            _device(name="R1", enable_secret_set=False),          # high
            _device(name="R2", service_password_encryption=False),  # medium
            _device(name="R3", banner_set=False),                   # low
        ])
        assert result["devices_audited"] == 3
        assert result["counts"] == {"high": 1, "medium": 1, "low": 1}
        # Lo grave primero: el LLM puede truncar la lista.
        assert [f["severity"] for f in result["findings"]] == ["high", "medium", "low"]

    def test_low_only_still_counts_as_secure(self):
        """`secure` mide altos y medios; un banner faltante no tumba la auditoría."""
        result = audit_security([_device(banner_set=False)])
        assert result["secure"]
        assert result["counts"]["low"] == 1


class TestSecurityAuditReader:
    """Guards sobre el JS que lee del bridge. Es un closure en register_tools, así
    que se verifica por texto, igual que TestReconcileWiring en
    test_live_reconcile.py."""

    def _src(self) -> str:
        return Path("src/packet_tracer_mcp/adapters/mcp/tool_registry.py").read_text(
            encoding="utf-8"
        )

    def _js(self) -> str:
        src = self._src()
        block = src.split("_SECURITY_AUDIT_JS = (", 1)[1]
        return block.split("\n    )", 1)[0]

    def test_reader_never_ships_a_credential(self):
        """El hash no puede cruzar el bridge: terminaría en el contexto del LLM.

        Solo se manda la etiqueta del algoritmo y banderas booleanas.
        """
        js = self._js()
        assert "enable_secret_algo: __algo(__sec)" in js
        assert "enable_secret_set: !!__sec" in js
        # Ninguna clave del payload lleva el valor crudo.
        assert "getEnableSecret()," not in js
        assert "__d.getUserPass(__u) }" not in js
        assert "algo: __algo(__d.getUserPass(__u))" in js

    def test_reader_skips_hosts(self):
        """Llamar getters IOS en un PC lanza y abre un modal que congela el bridge."""
        assert "typeof __d.getEnableSecret !== 'function'" in self._js()

    def test_user_enumeration_is_guarded(self):
        """getUserEntryAt lanza 'out of bound' en vez de devolver null."""
        js = self._js()
        assert "getUserPassCount()" in js
        assert "catch (__ue) {}" in js

    def test_payload_stays_single_line(self):
        """PT descarta saltos reales al ejecutar el código: el JS va en una línea."""
        src = self._src()
        marker = "_SECURITY_AUDIT_JS = ("
        assert marker in src
        # Cada fragmento es un literal adyacente sin \n embebido.
        assert "\\n" not in self._js()
