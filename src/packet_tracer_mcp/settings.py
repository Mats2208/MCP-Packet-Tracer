"""
Configuración global del servidor.
"""

VERSION = "0.4.0"

SERVER_NAME = "Packet Tracer MCP"

SERVER_INSTRUCTIONS = (
    "Servidor MCP para crear, configurar y validar topologías de red "
    "en Cisco Packet Tracer. Usa las tools en este orden:\n"
    "1) pt_list_devices — consulta dispositivos disponibles\n"
    "2) pt_plan_topology — genera un plan completo desde un request\n"
    "3) pt_validate_plan — verifica que el plan sea correcto\n"
    "4) pt_generate_script — genera el script PTBuilder\n"
    "5) pt_generate_configs — genera las configs CLI\n"
    "6) pt_full_build — hace todo de una vez (incluye deploy)\n"
    "7) pt_deploy — copia script al portapapeles + instrucciones\n"
    "8) pt_live_deploy — envia comandos directo a PT en tiempo real\n"
    "9) pt_bridge_status — verifica conexion con PT\n"
    "10) pt_export — exporta a archivo"
)
