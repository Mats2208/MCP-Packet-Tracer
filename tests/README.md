# tests/

Suite de pruebas con pytest. Corre **offline**: ningún test necesita Packet Tracer
(los casos de bridge levantan un `PTCommandBridge` en un puerto efímero o simulan el
Script Engine en un hilo).

Para el conteo actual y el desglose por archivo:

```bash
python -m pytest --collect-only -q     # no se fija un número que caduque
```

## Ejecución

```bash
# Todos los tests (desde la raíz del repo)
python -m pytest

# Un archivo específico
python -m pytest tests/test_full_build.py -v

# Un test específico
python -m pytest tests/test_full_build.py::TestFullBuild::test_basic_2_routers -v
```

## Qué se cubre

- **Dominio**: validación (IP, VLAN, ACL, hardening, cables, dispositivos), planning,
  asignación de IPs, auto-fixer, estimación.
- **Generadores**: PTBuilder JS e IOS CLI, incluyendo tests **adversariales** de inyección
  (comillas, saltos de línea, `..`) en `test_injection_regressions.py`.
- **Seguridad del bridge**: token, límites de cuerpo, DNS rebinding, long-poll y lote
  (`test_bridge_security.py`); protocolo del file-bridge (`test_file_bridge.py`).
- **Integración**: `pt_full_build` end-to-end, diff/health-check, reconcile.
