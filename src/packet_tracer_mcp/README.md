# packet_tracer_mcp

Main module of the MCP server for Cisco Packet Tracer.

## Architecture

Follows **Clean Architecture / Domain-Driven Design** with a clear separation of layers:

```
packet_tracer_mcp/
├── adapters/mcp/       → Capa de protocolo MCP (tools + resources)
├── application/        → Casos de uso + DTOs (entrada/salida)
├── domain/             → Lógica de negocio pura
│   ├── models/         → Modelos de datos (Plan, Request, Error)
│   ├── services/       → Servicios (Orchestrator, IPPlanner, Validator...)
│   └── rules/          → Reglas de validación (dispositivos, cables, IPs)
├── infrastructure/     → Preocupaciones externas
│   ├── catalog/        → Catálogo de dispositivos, cables, templates
│   ├── generator/      → Generadores de scripts PTBuilder + CLI configs
│   ├── execution/      → Estrategias de despliegue (manual, live bridge)
│   └── persistence/    → Persistencia de proyectos
├── shared/             → Enums, constantes, utilidades
├── server.py           → Punto de entrada MCP
├── settings.py         → Configuración global
└── __main__.py         → Entry point: python -m packet_tracer_mcp
```

## Data flow

```
Request → TopologyRequest → Orchestrator → TopologyPlan → Validator
                                                ↓
                                    Generator (PTBuilder JS + CLI configs)
                                                ↓
                                    Executor (Manual / Deploy / Live Bridge)
```

## Root files

| File | Purpose |
|---------|-----------|
| `server.py` | Creates the `FastMCP` instance, registers tools/resources, starts over HTTP (:39000) or stdio |
| `__main__.py` | Entry point for `python -m packet_tracer_mcp` — calls `server.main()` |
| `settings.py` | Global constants: `VERSION` (0.4.0), `SERVER_NAME`, `SERVER_INSTRUCTIONS` |

## Running

```bash
# Streamable HTTP (default, puerto 39000)
python -m packet_tracer_mcp

# Modo stdio (debug/legacy)
python -m packet_tracer_mcp --stdio
```

## Layer dependencies

```
adapters/mcp → application/use_cases → domain/services → domain/models
                                              ↓
                                    infrastructure/ (catalog, generator, execution)
                                              ↓
                                         shared/ (enums, constants, utils)
```

No circular dependencies. The `domain` layer never imports from `infrastructure` directly — communication goes through interfaces.
