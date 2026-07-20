# tests/

Suite de pruebas con pytest. Corre offline: ningun test necesita Packet Tracer.

Para el conteo actual y el desglose por archivo: `python -m pytest --collect-only -q`.
(Este README listaba 38 tests cuando ya habia 127; se quita el numero en vez de
volver a fijar uno que caduque.)

## Ejecución

```bash
# Todos los tests
python -m pytest tests/ -v

# Un archivo específico
python -m pytest tests/test_full_build.py -v

# Un test específico
python -m pytest tests/test_full_build.py::TestFullBuild::test_basic_2_routers -v
```

## Archivos

| Archivo | Tests | Cobertura |
|---------|-------|-----------|
| `test_full_build.py` | 7 | **Integración**: 2 routers, 3 routers+WAN, OSPF, single router, sin DHCP, con servers, estimation fields |
| `test_validator.py` | 4 | Validación: plan válido, nombre duplicado, modelo inválido, interfaz inválida |
| `test_generators.py` | 4 | PTBuilder: `addDevice` format, `addLink` format. CLI: hostname config, DHCP pool |
| `test_auto_fixer.py` | 2 | Auto-corrección: fix cable type (router-router→cross), no-fix-needed |
| `test_explainer.py` | 3 | Explicaciones: básica incluye routers, DHCP explained, WAN explained |
| `test_ip_planner.py` | 6 | IP: subredes LAN /24, secuenciales, inter-router /30, hosts /30, gateway .1 |
| `test_estimator.py` | 4 | Estimación: básica, WAN añade cloud, complejidad simple, complejidad escala |
| `test_regressions_runtime.py` | 8 | Regresiones: templates, ManualExecutor metadata, rutas multi-hop, first IP .2, fix_plan, estimator con laptops/AP, PC config estática/DHCP |

## Total: 38 tests, ~400 líneas de código
