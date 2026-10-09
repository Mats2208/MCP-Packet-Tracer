"""
Security posture audit of PT's live topology.

Pure logic, no bridge — testable with synthetic dicts, like topology_diff.

DESIGN NOTE — this module never receives or returns credentials. The bridge
reader classifies each credential by its prefix and sends ONLY the algorithm
label ("md5", "type7", ...). A hash in a tool's output ends up in the LLM's
context and in the MCP client's logs; the label is enough to audit,
and there is no reason to take that risk.

Algorithm classification (verified against PT 9.0.0.0810):
  $1$...  -> "md5"      `enable secret` / `username X secret` (type 5)
  $8$...  -> "pbkdf2"   type 8
  $9$...  -> "scrypt"   type 9
  hex     -> "type7"    `password` with service-password-encryption — REVERSIBLE
  other   -> "plaintext"
"""

from __future__ import annotations

# Algorithms an attacker can reverse to the original password: type 7 is
# a Vigenère cipher with a published key (online decoders exist), and
# plaintext does not even try.
REVERSIBLE_ALGOS = frozenset({"type7", "plaintext"})

# MD5 without a per-device salt is crackable offline with modern hardware. It is not
# reversible, so it is one step less severe than type 7, but Cisco has recommended
# type 8/9 for years.
WEAK_HASH_ALGOS = frozenset({"md5"})

# 0x2102 (8450) is the normal value. 0x2142 (8514) skips the startup-config at
# boot: it is the password-recovery procedure, and leaving it set
# means a reboot discards the entire security configuration.
CONFIG_REGISTER_NORMAL = 0x2102
CONFIG_REGISTER_BYPASS = 0x2142


def _finding(device: str, code: str, severity: str, message: str, suggestion: str) -> dict:
    return {
        "device": device,
        "code": code,
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
    }


def _audit_device(dev: dict) -> list[dict]:
    name = dev.get("name", "?")
    findings: list[dict] = []

    # --- Privileged mode access ---
    if not dev.get("enable_secret_set"):
        findings.append(_finding(
            name, "NO_ENABLE_SECRET", "high",
            "Sin `enable secret`: cualquiera con acceso a la consola entra a modo privilegiado.",
            "Configurá `enable secret <clave>` (o usá pt_apply_hardening con enable_secret).",
        ))
    else:
        algo = dev.get("enable_secret_algo")
        if algo in REVERSIBLE_ALGOS:
            findings.append(_finding(
                name, "ENABLE_SECRET_REVERSIBLE", "high",
                f"El `enable secret` está guardado con un algoritmo reversible ({algo}).",
                "Reconfiguralo con `enable secret` (hash) en vez de `enable password`.",
            ))
        elif algo in WEAK_HASH_ALGOS:
            findings.append(_finding(
                name, "ENABLE_SECRET_WEAK_ALGO", "medium",
                "El `enable secret` usa MD5 (type 5), crackeable offline.",
                "Si el IOS lo soporta, usá `enable algorithm-type scrypt secret <clave>`.",
            ))

    # `enable password` and `enable secret` can coexist; the password is
    # reversible and stays in the config even though the secret is the one that takes effect.
    if dev.get("enable_password_set"):
        findings.append(_finding(
            name, "ENABLE_PASSWORD_PRESENT", "medium",
            "Hay un `enable password` configurado, que se guarda de forma reversible.",
            "Borralo con `no enable password` y dejá solo `enable secret`.",
        ))

    # --- Local credentials ---
    users = dev.get("users") or []
    for user in users:
        uname = user.get("name", "?")
        ualgo = user.get("algo")
        if ualgo in REVERSIBLE_ALGOS:
            findings.append(_finding(
                name, "USER_CREDENTIAL_REVERSIBLE", "high",
                f"El usuario local '{uname}' guarda su credencial de forma reversible ({ualgo}).",
                f"Recreálo con `username {uname} secret <clave>` en vez de `password`.",
            ))
        elif ualgo in WEAK_HASH_ALGOS:
            findings.append(_finding(
                name, "USER_CREDENTIAL_WEAK_ALGO", "low",
                f"El usuario local '{uname}' usa MD5 (type 5).",
                f"Si el IOS lo soporta: `username {uname} algorithm-type scrypt secret <clave>`.",
            ))

    if not users:
        findings.append(_finding(
            name, "NO_LOCAL_USERS", "low",
            "No hay usuarios locales: no se puede exigir `login local` en VTY ni usar SSH.",
            "Creá al menos un usuario con `username <user> secret <clave>`.",
        ))

    # --- Global config ---
    if not dev.get("service_password_encryption"):
        findings.append(_finding(
            name, "NO_SERVICE_PASSWORD_ENCRYPTION", "medium",
            "`service password-encryption` está apagado: las claves quedan en claro en la config.",
            "Activalo con `service password-encryption` (no reemplaza a `secret`, lo complementa).",
        ))

    if not dev.get("banner_set"):
        findings.append(_finding(
            name, "NO_BANNER_MOTD", "low",
            "Sin banner MOTD. En varias jurisdicciones el aviso legal es requisito para perseguir un acceso no autorizado.",
            "Configurá `banner motd` (o usá pt_apply_hardening con banner_motd).",
        ))

    reg = dev.get("config_register")
    if reg == CONFIG_REGISTER_BYPASS:
        findings.append(_finding(
            name, "CONFIG_REGISTER_BYPASS", "high",
            f"El config-register es 0x{reg:04x}: en el próximo reboot el equipo IGNORA la startup-config.",
            "Restauralo con `config-register 0x2102` y guardá la configuración.",
        ))

    return findings


def audit_security(devices: list[dict]) -> dict:
    """Audits the security posture of the devices read from the bridge.

    `devices` is the output of the pt_audit_security reader: a list of dicts with
    the flags already classified (never credentials). Devices that do not
    expose IOS configuration (PCs, servers) are discarded before they get here.
    """
    findings: list[dict] = []
    for dev in devices:
        findings.extend(_audit_device(dev))

    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = f["severity"]
        if sev in counts:
            counts[sev] += 1

    # Sort by severity so the important items come first: the consumer
    # is an LLM that may truncate, and we do not want a high finding to get lost.
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), f["device"], f["code"]))

    return {
        "secure": counts["high"] == 0 and counts["medium"] == 0,
        "devices_audited": len(devices),
        "counts": counts,
        "findings": findings,
    }
