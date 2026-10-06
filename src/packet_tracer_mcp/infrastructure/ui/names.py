"""Nombres de pestañas y apps del diálogo de un dispositivo.

Los objectName de los botones del Desktop salen de la documentación de
`DeviceDialog::setWidgetVisible` (help/default/IpcAPI de PT 9.0.1) y se
verificaron por UI Automation contra un PC-PT real: la AutomationId de cada
botón termina en ese objectName (ej. `...CommandPromptBtn`). "MIBBroswerBtn"
va con la errata de Cisco a propósito.
"""

from __future__ import annotations

import re

# alias normalizado → objectName del botón en Desktop
DESKTOP_APPS: dict[str, str] = {
    "ipconfiguration": "IPConfigBtn", "ipconfig": "IPConfigBtn",
    "dialup": "DialupBtn",
    "terminal": "TerminalBtn",
    "commandprompt": "CommandPromptBtn", "cmd": "CommandPromptBtn", "prompt": "CommandPromptBtn",
    "webbrowser": "WebBrowserBtn", "browser": "WebBrowserBtn", "web": "WebBrowserBtn",
    "pcwireless": "PCWirelessBtn", "wireless": "PCWirelessBtn",
    "vpn": "VPNBtn",
    "trafficgenerator": "TrafficGeneratorBtn",
    "mibbrowser": "MIBBroswerBtn",
    "ciscoipcommunicator": "IPCommunicatorBtn", "ipcommunicator": "IPCommunicatorBtn",
    "email": "EmailBtn", "mail": "EmailBtn", "mailbrowser": "EmailBtn",
    "pppoedialer": "PPPoEDialerBtn", "pppoe": "PPPoEDialerBtn",
    "texteditor": "TextEditorBtn", "editor": "TextEditorBtn",
    "firewall": "FirewallBtn", "ipv4firewall": "FirewallBtn",
    "ipv6firewall": "IPv6FirewallBtn",
    "aaaaccounting": "AAAAccountingBtn",
    "netflowcollector": "NetflowCollectorBtn",
    "ioxide": "IoXSdkBtn", "ioxsdk": "IoXSdkBtn",
    "tftpservice": "TftpBtn", "tftp": "TftpBtn",
    "sshclient": "SshClientBtn", "ssh": "SshClientBtn",
    "bluetooth": "BluetoothBtn",
    "iotmonitor": "IoT Monitor_btn",
    "iotide": "IoT IDE_btn",
    "userappsmanager": "User Apps Manager_btn",
    "supervisoryworkstation": "Supervisory Workstation_btn",
}

# Pestañas conocidas (el nombre visible). Cada tipo de dispositivo muestra un
# subconjunto: PC = Physical/Config/Desktop/Programming/Attributes, router =
# Physical/Config/CLI/Attributes, Server-PT suma Services.
TABS = ("Physical", "Config", "CLI", "Desktop", "Services", "GUI", "Programming", "Attributes")


def normalize(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def desktop_app_object_name(app: str | None) -> str | None:
    """objectName del botón para una app ('Command Prompt', 'cmd', 'web_browser'...)."""
    key = normalize(app)
    if not key:
        return None
    if key in DESKTOP_APPS:
        return DESKTOP_APPS[key]
    # También se acepta el objectName tal cual ("CommandPromptBtn").
    for obj in DESKTOP_APPS.values():
        if normalize(obj) == key:
            return obj
    return None


def known_apps() -> list[str]:
    return sorted({k for k in DESKTOP_APPS if len(k) > 4})


def tab_matches(wanted: str, visible_name: str) -> bool:
    return normalize(wanted) == normalize(visible_name)
