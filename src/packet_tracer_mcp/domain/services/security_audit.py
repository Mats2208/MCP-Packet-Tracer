"""
Auditoría de postura de seguridad sobre la topología viva de PT.

Lógica pura, sin bridge — testeable con dicts sintéticos, igual que topology_diff.

NOTA DE DISEÑO — este módulo nunca recibe ni devuelve credenciales. El lector del
bridge clasifica cada credencial por su prefijo y manda SOLO la etiqueta del
algoritmo ("md5", "type7", ...). Un hash en la salida de una tool termina en el
contexto del LLM y en los logs del cliente MCP; la etiqueta alcanza para auditar
y no hay razón para pagar ese riesgo.

Clasificación de algoritmos (verificada contra PT 9.0.0.0810):
  $1$...  -> "md5"      `enable secret` / `username X secret` (type 5)
  $8$...  -> "pbkdf2"   type 8
  $9$...  -> "scrypt"   type 9
  hex     -> "type7"    `password` con service-password-encryption — REVERSIBLE
  resto   -> "plaintext"
"""

from __future__ import annotations

# Algoritmos que un atacante puede revertir a la contraseña original: type 7 es
# un cifrado Vigenère con clave publicada (hay decodificadores online), y
# plaintext ni siquiera lo intenta.
REVERSIBLE_ALGOS = frozenset({"type7", "plaintext"})

# MD5 sin salt por dispositivo es crackeable offline con hardware moderno. No es
# reversible, así que es un grado menos grave que type 7, pero Cisco recomienda
# type 8/9 desde hace años.
WEAK_HASH_ALGOS = frozenset({"md5"})

# 0x2102 (8450) es el valor normal. 0x2142 (8514) salta la startup-config en el
# arranque: es el procedimiento de recuperación de contraseña, y dejarlo puesto
# significa que un reboot descarta toda la configuración de seguridad.
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

    # --- Acceso a modo privilegiado ---
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

    # `enable password` y `enable secret` pueden coexistir; el password es
    # reversible y queda en la config aunque el secret sea el que manda.
    if dev.get("enable_password_set"):
        findings.append(_finding(
            name, "ENABLE_PASSWORD_PRESENT", "medium",
            "Hay un `enable password` configurado, que se guarda de forma reversible.",
            "Borralo con `no enable password` y dejá solo `enable secret`.",
        ))

    # --- Credenciales locales ---
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

    # --- Config global ---
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
    """Audita la postura de seguridad de los dispositivos leídos del bridge.

    `devices` es la salida del lector de pt_audit_security: una lista de dicts con
    las banderas ya clasificadas (nunca credenciales). Los dispositivos que no
    exponen configuración IOS (PCs, servidores) se descartan antes de llegar acá.
    """
    findings: list[dict] = []
    for dev in devices:
        findings.extend(_audit_device(dev))

    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = f["severity"]
        if sev in counts:
            counts[sev] += 1

    # Ordenar por gravedad para que lo importante aparezca primero: el consumidor
    # es un LLM que puede truncar, y no queremos que se pierda un finding alto.
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), f["device"], f["code"]))

    return {
        "secure": counts["high"] == 0 and counts["medium"] == 0,
        "devices_audited": len(devices),
        "counts": counts,
        "findings": findings,
    }
