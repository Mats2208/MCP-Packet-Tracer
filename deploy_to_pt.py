"""
Deploy topología build_1r_10pc a Packet Tracer via HTTP bridge.

Prerequisitos:
  - PT está abierto con Builder Code Editor
  - El bootstrap de polling está corriendo en PT

Uso:
  python deploy_to_pt.py
"""
import json
import sys
import time

from src.packet_tracer_mcp.infrastructure.execution.live_bridge import PTCommandBridge
from src.packet_tracer_mcp.infrastructure.execution.live_executor import LiveExecutor
from src.packet_tracer_mcp.domain.models.plans import TopologyPlan

PLAN_PATH = "projects/build_1r_10pc/plan.json"
WAIT_FOR_PT_SECS = 60  # max espera a que PT empiece a hacer polling
DELAY_BETWEEN_CMDS = 0.8  # segundos entre comandos


def main():
    print("=" * 60)
    print("PT Live Deploy — build_1r_10pc")
    print("=" * 60)

    # 1. Cargar plan
    print(f"\n[1/4] Cargando plan desde {PLAN_PATH}...")
    with open(PLAN_PATH, "r", encoding="utf-8") as f:
        plan_data = json.load(f)
    plan = TopologyPlan(**plan_data)
    print(f"      Plan: {len(plan.devices)} dispositivos, {len(plan.links)} enlaces")

    # 2. Iniciar bridge
    print("\n[2/4] Iniciando HTTP bridge en http://127.0.0.1:54321...")
    bridge = PTCommandBridge()
    bridge.start()
    print("      Bridge activo.")

    # 3. Esperar conexión de PT
    print(f"\n[3/4] Esperando conexión de Packet Tracer (máx {WAIT_FOR_PT_SECS}s)...")
    print("      >> Asegurate de que el bootstrap esté corriendo en Builder Code Editor <<")
    print()
    
    deadline = time.time() + WAIT_FOR_PT_SECS
    dots = 0
    while time.time() < deadline:
        if bridge.is_connected:
            print(f"\n      ✅ PT conectado!")
            break
        print(".", end="", flush=True)
        dots += 1
        if dots % 20 == 0:
            print()
        time.sleep(1)
    else:
        print(f"\n\n❌ Timeout: PT no se conectó en {WAIT_FOR_PT_SECS}s")
        print("   Asegurate de pegar el bootstrap en Builder Code Editor y hacer Run.")
        sys.exit(1)

    # 4. Desplegar
    print(f"\n[4/4] Desplegando topología en PT (delay={DELAY_BETWEEN_CMDS}s/cmd)...")
    executor = LiveExecutor(bridge)
    result = executor.execute(plan, delay=DELAY_BETWEEN_CMDS)

    if result["success"]:
        print(f"\n✅ Deploy completado!")
        print(f"   Comandos enviados: {result['commands_sent']}")
        print(f"   Dispositivos: {result['devices']}")
        print(f"   Enlaces: {result['links']}")
        print(f"\n   Revisá PT — los dispositivos deben aparecer configurados.")
    else:
        print(f"\n❌ Deploy falló: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
