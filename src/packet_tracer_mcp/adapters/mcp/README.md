# adapters/mcp/

Capa de protocolo MCP — registra las herramientas (tools) y recursos (resources) que el LLM puede invocar.

## Archivos

### `tool_registry.py`
**~3200 líneas** — Registro monolítico de los 46 MCP tools.

Función principal: `register_tools(mcp: FastMCP) → None`

Cada tool se define como una función decorada con `@mcp.tool()` dentro de `register_tools()`.

El registro no solo declara las tools: también resuelve **por qué canal** viaja cada
comando hacia Packet Tracer. Cuando la ventana de la extensión está abierta se usa el
bridge HTTP (`127.0.0.1:54321`); cuando está cerrada, el comando se deja como un archivo
en el buzón que el Script Engine lee en disco (ver `_pick_channel` y el módulo
`infrastructure/execution/`). El servidor elige **un** canal por comando, nunca ambos.

#### Tools registradas (46)

| Grupo | Tool | Descripción |
|-------|------|-------------|
| **Consulta** | `pt_list_devices` | Catálogo de dispositivos con puertos |
| | `pt_list_templates` | Templates de topología disponibles |
| | `pt_get_device_details` | Detalle de un modelo específico |
| | `pt_list_modules` | Módulos de expansión para un modelo |
| **Estimación** | `pt_estimate_plan` | Dry-run sin generar plan completo |
| **Planificación** | `pt_plan_topology` | Genera plan completo → JSON |
| **Validación** | `pt_validate_plan` | Errores/warnings tipados |
| | `pt_fix_plan` | Auto-corrección + re-validación |
| | `pt_explain_plan` | Explicación en lenguaje natural |
| | `pt_diff` | Diferencias entre plan y topología en PT |
| **Generación** | `pt_generate_script` | Script PTBuilder JS (± configs) |
| | `pt_generate_configs` | CLI IOS por dispositivo |
| **Pipeline** | `pt_full_build` | Plan + validar + generar + explicar + estimar |
| **Despliegue** | `pt_deploy` | Clipboard + archivos + instrucciones |
| | `pt_export` | Solo archivos a disco |
| | `pt_export_topology` | Exporta la topología viva de PT a plan |
| | `pt_live_deploy` | Despliegue directo vía bridge (HTTP o archivo) |
| **Proyectos (plan JSON)** | `pt_list_projects` | Listar topologías guardadas (plan.json) |
| | `pt_load_project` | Cargar proyecto por nombre (plan.json) |
| **Proyectos (.pkt real)** | `pt_save_project` | Guarda el `.pkt` REAL de PT vía bridge |
| | `pt_open_project` | Abre un `.pkt` REAL de PT vía bridge |
| **Verificación** | `pt_verify_connectivity` | Ping real con parseo del resultado |
| | `pt_health_check` | Chequeo de salud del servidor/entorno |
| | `pt_bridge_status` | Estado del bridge y conexión con PT |
| **Interacción** | `pt_query_topology` | Consultar dispositivos/links actuales en PT |
| | `pt_add_device` | Agregar dispositivo |
| | `pt_delete_device` | Eliminar dispositivo |
| | `pt_rename_device` | Renombrar dispositivo |
| | `pt_move_device` | Mover dispositivo en canvas |
| | `pt_add_link` | Agregar enlace (valida puertos y cable) |
| | `pt_delete_link` | Eliminar enlace |
| | `pt_set_port` | Configurar un puerto |
| | `pt_send_raw` | Enviar JS arbitrario al Script Engine |
| **Módulos** | `pt_add_module` | Instalar un módulo en un slot |
| | `pt_install_modules_batch` | Instalar varios módulos en lote |
| **Config avanzada** | `pt_apply_vlan` | Aplicar VLANs |
| | `pt_apply_stp` | Aplicar Spanning Tree |
| | `pt_apply_acl` | Aplicar ACL |
| | `pt_apply_acl_object` | Aplicar ACL basada en objetos |
| | `pt_remove_acl` | Quitar ACL |
| | `pt_remove_acl_object` | Quitar ACL de objetos |
| | `pt_apply_nat` | Aplicar NAT |
| | `pt_remove_nat` | Quitar NAT |
| | `pt_apply_port_security` | Aplicar port-security |
| | `pt_apply_hardening` | Aplicar hardening de dispositivo |
| | `pt_apply_interface_tuning` | Tuning de interfaces |

#### Helpers internos
- `_pick_channel(...)` — Enruta cada comando por HTTP (:54321) o por el buzón de archivos según si la ventana de la extensión está abierta
- `_http_get(url)` / `_http_post(url, data)` — Comunicación HTTP con el bridge
- `_js_escape(s)` — Escape de strings para JS
- `_bridge_is_up()` / `_bridge_pt_connected()` — Verificación de conectividad

### `resource_registry.py`
**~64 líneas** — Registro de 5 MCP resources estáticos.

Función principal: `register_resources(mcp: FastMCP) → None`

| Resource URI | Contenido |
|-------------|-----------|
| `pt://catalog/devices` | Catálogo completo de dispositivos con puertos |
| `pt://catalog/cables` | Tipos de cable disponibles |
| `pt://catalog/aliases` | Alias comunes → modelo real |
| `pt://catalog/templates` | Templates con descripción, rangos, routing default |
| `pt://capabilities` | Versión, features, límites del servidor |
