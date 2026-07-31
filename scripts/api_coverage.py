"""Mide qué porcentaje de la API IPC de Packet Tracer usa este MCP.

Publicar un porcentaje sin decir cómo se midió no vale nada, así que este script
existe para que cualquiera pueda reproducir el número.

Uso
---
1. Abrí PT con la extensión MCP Control Center y una topología con al menos un
   router llamado R1, un switch SW1 y un PC1 (`pt_full_build` genera esos nombres).
2. Desde el cliente MCP, pedile a `pt_send_raw` que ejecute el JS que imprime
   `union_js()` de acá abajo. Escribe la lista de métodos a `pt_union.txt`.
3. `python scripts/api_coverage.py`

Metodología
-----------
- DENOMINADOR: unión de nombres de método distintos alcanzables desde el grafo de
  objetos IPC, enumerando 41 objetos y excluyendo los 9 miembros boilerplate que
  todo objeto PT arrastra (`_parser`, `getClassName`, `getObjectUuid`,
  `register*`, `unregister*`). Es un PISO, no el total real: `OspfProcess`,
  `EigrpProcess`, `DhcpProcess` y `StpProcess` devuelven null en dispositivos sin
  configurar, y `FrameInstance` / `DhcpPool` / `Cluster` necesitan estado previo
  para materializarse. Por lo tanto el porcentaje reportado es un TECHO.
- NUMERADOR: nombres que aparecen como `.metodo(` en el código del servidor y en
  el Script Engine de la extensión, intersectados con el denominador. La
  intersección es lo que filtra los falsos positivos: un `.split(` de Python no
  existe en la API de PT y se descarta solo.
"""

from __future__ import annotations

import re
import pathlib
import subprocess
import sys

UNION_FILE = "pt_union.txt"
CALL = re.compile(r"\.([a-zA-Z][A-Za-z0-9_]{2,})\s*\(")

# Los 9 miembros que PT expone en TODO objeto; contarlos inflaría ambos lados.
BOILERPLATE = (
    "_parser", "getClassName", "getObjectUuid",
    "registerDelegate", "registerEvent", "registerObjectEvent",
    "unregisterDelegate", "unregisterEvent", "unregisterObjectEvent",
)


def scanned(path: str) -> bool:
    return (path.startswith("src/packet_tracer_mcp/") and path.endswith(".py")) or (
        path.startswith("EXTENSION/script-engine/") and path.endswith(".js")
    )


def methods_in(text: str) -> set[str]:
    return {m.group(1) for m in CALL.finditer(text)}


def used_at_revision(rev: str, union: set[str]) -> set[str]:
    """Métodos usados en un commit. Útil para medir el delta de una fase."""
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", rev],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.split()
    names: set[str] = set()
    for path in filter(scanned, listing):
        # bytes + decode explícito: `text=True` usa el encoding de locale y en
        # Windows corrompe archivos UTF-8, lo que hace desaparecer métodos y
        # exagera el delta.
        blob = subprocess.run(
            ["git", "show", f"{rev}:{path}"], capture_output=True, check=True
        ).stdout.decode("utf-8", "replace")
        names |= methods_in(blob)
    return names & union


def used_in_worktree(union: set[str]) -> set[str]:
    """Métodos usados por el contenido TRACKEADO, no por el worktree crudo.

    `.gitignore` excluye `EXTENSION/script-engine/*.js` salvo `main.js`: los demás
    son helpers derivados de PTBuilder que viven en la máquina pero no se
    publican. Contarlos inflaría la cobertura con código que quien clone el repo
    no recibe, así que se mide `git ls-files` — y el número es reproducible desde
    un clone limpio.
    """
    listing = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.split()
    names: set[str] = set()
    for path in filter(scanned, listing):
        p = pathlib.Path(path)
        if p.exists():
            names |= methods_in(p.read_text(encoding="utf-8", errors="replace"))
    return names & union


def union_js() -> str:
    """JS de enumeración para pasarle a pt_send_raw. Requiere R1, SW1 y PC1."""
    excluded = ",".join(f"{k}:1" for k in BOILERPLATE)
    return (
        f"var B={{{excluded}}};var U={{}},N=0;"
        "function ab(g){try{var o=g();if(!o)return;for(var k in o){"
        "if(B[k])continue;if(!U[k]){U[k]=1;N++;}}}catch(e){}}"
        "var app=ipc.appWindow(),net=ipc.network(),ws=app.getActiveWorkspace(),"
        "lw=ws.getLogicalWorkspace();var r1=net.getDevice('R1'),sw1=net.getDevice('SW1'),"
        "pc1=net.getDevice('PC1');var p0=r1.getPort('GigabitEthernet0/0');"
        "var G=[function(){return ipc;},function(){return app;},function(){return ws;},"
        "function(){return lw;},function(){return net;},function(){return ipc.simulation();},"
        "function(){return ipc.options();},function(){return ipc.systemFileManager();},"
        "function(){return ipc.hardwareFactory();},function(){return ipc.ipcManager();},"
        "function(){return ipc.multiUserManager();},function(){return ipc.userAppManager();},"
        "function(){return r1;},function(){return sw1;},function(){return pc1;},"
        "function(){return p0;},function(){return pc1.getPort('FastEthernet0');},"
        "function(){return sw1.getPort('FastEthernet0/1');},function(){return net.getLinkAt(0);},"
        "function(){return r1.getCommandLine();},function(){return r1.getConsole();},"
        "function(){return r1.getConsoleLine();},function(){return r1.getRootModule();},"
        "function(){return r1.getClassMapManager();},function(){return r1.getPolicyMapManager();},"
        "function(){return r1.getParameterMapManager();},function(){return r1.getNetflowExporterManager();},"
        "function(){return r1.getNetflowMonitorManager();},function(){return r1.getNetflowRecordManager();},"
        "function(){return r1.getDescriptor();},function(){return r1.getVtyLine(0);},"
        "function(){return app.getActiveFile();},function(){return r1.getPhysicalObject();},"
        "function(){return r1.getProcess('AclProcess');},function(){return sw1.getProcess('VlanManager');},"
        "function(){return r1.getProcess('RipProcess');},function(){return r1.getProcess('NatProcess');},"
        "function(){return p0.getEncapProcess();},function(){return p0.getKeepAliveProcess();},"
        "function(){return p0.getQosQueue();},function(){return p0.getHardwareQueue();},"
        # FrameInstance y sus hijos solo existen con tráfico en el event list. Si
        # PT está en Realtime o no se generó nada, estos tres devuelven null y el
        # denominador sale ~40 métodos más chico — de ahí que sea un piso.
        "function(){var s=ipc.simulation();return s.getFrameInstanceCount()>0?s.getFrameInstanceAt(0):null;},"
        "function(){var s=ipc.simulation();if(s.getFrameInstanceCount()===0)return null;"
        "var f=s.getFrameInstanceAt(0);return f.getFlowChartNodeCount()>0?f.getFlowChartNodeAt(0):null;},"
        "function(){var s=ipc.simulation();if(s.getFrameInstanceCount()===0)return null;"
        "var f=s.getFrameInstanceAt(0);return f.getFlowChartNodeCount()>0?f.getFrameDecsionAt(0):null;}];"
        "for(var i=0;i<G.length;i++){ab(G[i]);}var list=[];for(var k in U){list.push(k);}list.sort();"
        f"ipc.systemFileManager().writePlainTextToFile('{UNION_FILE}', list.join('\\n'));"
        "reportResult('union='+N+' objetos='+G.length);"
    )


def main() -> int:
    if "--emit-js" in sys.argv:
        print(union_js())
        return 0

    path = pathlib.Path(UNION_FILE)
    if not path.exists():
        print(f"Falta {UNION_FILE}. Corre `python scripts/api_coverage.py --emit-js`, "
              "pasale ese JS a pt_send_raw con PT abierto, y volve a intentar.")
        return 1

    union = {n for n in path.read_text(encoding="utf-8", errors="replace").split("\n") if n}
    now = used_in_worktree(union)
    print(f"Denominador: {len(union)} metodos distintos (piso - ver docstring)")
    print(f"En uso     : {len(now)} -> {len(now) * 100 / len(union):.1f}%")

    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        rev = sys.argv[1]
        base = used_at_revision(rev, union)
        delta = len(now) - len(base)
        print(f"\nContra {rev}: {len(base)} -> {len(base) * 100 / len(union):.1f}%")
        print(f"Delta      : {delta:+d} metodos ({delta * 100 / len(union):+.1f} pts)")
        if now - base:
            print("Nuevos     : " + ", ".join(sorted(now - base)))
        if base - now:
            print("PERDIDOS   : " + ", ".join(sorted(base - now)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
