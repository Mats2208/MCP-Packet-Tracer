"""
Deploy executor: copies scripts to the Windows clipboard
and generates step-by-step instructions for Packet Tracer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ...domain.models.plans import TopologyPlan
from ..generator.ptbuilder_generator import generate_ptbuilder_script, generate_full_script
from ..generator.cli_config_generator import generate_all_configs
from ...shared.utils import safe_name_component, resolve_within
from .executor_base import ExecutorBase


def _copy_to_clipboard(text: str) -> bool:
    """Copies text to the Windows clipboard using clip.exe."""
    if sys.platform != "win32":
        return False
    try:
        subprocess.run(
            "clip",
            input=text.encode("utf-16-le"),
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


class DeployExecutor(ExecutorBase):
    """
    Deploys a topology to Packet Tracer.

    Strategy:
    1. Generates the PTBuilder script (addDevice + addLink)
    2. Copies it to the Windows clipboard
    3. Exports files to disk (CLI configs, plan JSON)
    4. Returns step-by-step instructions
    """

    def __init__(self, output_dir: str | Path = "projects"):
        self.output_dir = Path(output_dir)

    def execute(self, plan: TopologyPlan, project_name: str | None = None) -> dict:
        """Deploys the plan: clipboard + files + instructions."""
        base_name = (project_name or plan.name or "topology").strip() or "topology"
        safe_name = safe_name_component(base_name)
        project_dir = resolve_within(self.output_dir, safe_name)
        project_dir.mkdir(parents=True, exist_ok=True)

        # Generate scripts
        topology_script = generate_ptbuilder_script(plan)
        full_script = generate_full_script(plan)
        configs = generate_all_configs(plan)

        # Copy the topology script to the clipboard
        clipboard_ok = _copy_to_clipboard(topology_script)

        # Save files to disk
        files: dict[str, str] = {}

        script_path = project_dir / "topology.js"
        script_path.write_text(topology_script, encoding="utf-8")
        files["topology_script"] = str(script_path)

        full_path = project_dir / "full_build.js"
        full_path.write_text(full_script, encoding="utf-8")
        files["full_script"] = str(full_path)

        for device_name, config_text in configs.items():
            cfg_path = resolve_within(
                project_dir, f"{safe_name_component(device_name, 'device')}_config.txt"
            )
            cfg_path.write_text(config_text, encoding="utf-8")
            files[f"config_{device_name}"] = str(cfg_path)

        plan_path = project_dir / "plan.json"
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        files["plan_json"] = str(plan_path)

        # Generate instructions
        instructions = self._build_instructions(
            plan, configs, clipboard_ok, project_dir
        )

        return {
            "status": "deployed" if clipboard_ok else "exported",
            "clipboard": clipboard_ok,
            "project_dir": str(project_dir),
            "files": files,
            "devices_count": len(plan.devices),
            "links_count": len(plan.links),
            "instructions": instructions,
        }

    def is_available(self) -> bool:
        """Available when running on Windows (for the clipboard)."""
        return sys.platform == "win32"

    @staticmethod
    def _build_instructions(
        plan: TopologyPlan,
        configs: dict[str, str],
        clipboard_ok: bool,
        project_dir: Path,
    ) -> str:
        """Generates step-by-step instructions to complete the deployment."""
        steps: list[str] = []

        # Step 1: PTBuilder script
        steps.append("=" * 60)
        steps.append("PASO 1: Crear topologia en Packet Tracer")
        steps.append("=" * 60)
        if clipboard_ok:
            steps.append("El script PTBuilder ya esta en tu portapapeles.")
            steps.append("")
            steps.append("  1. Abre Packet Tracer")
            steps.append("  2. Ve a Extensions > Scripting (o Builder Code Editor)")
            steps.append("  3. Pega el script (Ctrl+V)")
            steps.append("  4. Haz clic en 'Run' o presiona el boton de ejecutar")
            steps.append("")
            steps.append(f"Los dispositivos y enlaces se crearan automaticamente.")
        else:
            steps.append(f"Abre el archivo: {project_dir / 'topology.js'}")
            steps.append("Copia su contenido y pegalo en Packet Tracer:")
            steps.append("  Extensions > Scripting > Pegar > Run")

        # Step 2: Configure devices
        routers = [d for d in plan.devices if d.category == "router"]
        switches = [d for d in plan.devices if d.category == "switch"]

        if configs:
            steps.append("")
            steps.append("=" * 60)
            steps.append("PASO 2: Configurar dispositivos")
            steps.append("=" * 60)

            for router in routers:
                if router.name in configs:
                    steps.append(f"")
                    steps.append(f"  {router.name}:")
                    steps.append(f"    - Doble clic en {router.name} > pestaña CLI")
                    steps.append(f"    - Pega el contenido de: {project_dir / f'{router.name}_config.txt'}")

            for switch in switches:
                if switch.name in configs:
                    steps.append(f"")
                    steps.append(f"  {switch.name}:")
                    steps.append(f"    - Doble clic en {switch.name} > pestaña CLI")
                    steps.append(f"    - Pega el contenido de: {project_dir / f'{switch.name}_config.txt'}")

        # Step 3: Configure PCs
        pcs = [d for d in plan.devices if d.category in ("pc", "server", "laptop")]
        if pcs:
            steps.append("")
            steps.append("=" * 60)
            steps.append("PASO 3: Configurar hosts (PCs)")
            steps.append("=" * 60)
            for pc in pcs:
                if plan.dhcp_pools:
                    steps.append(f"  {pc.name}: Desktop > IP Configuration > DHCP")
                elif pc.interfaces:
                    for iface, ip_cidr in pc.interfaces.items():
                        ip = ip_cidr.split("/")[0]
                        steps.append(f"  {pc.name}: IP={ip}, Gateway={pc.gateway or 'N/A'}")

        # Step 4: Verify
        if plan.validations:
            steps.append("")
            steps.append("=" * 60)
            steps.append("PASO 4: Verificar conectividad")
            steps.append("=" * 60)
            for v in plan.validations:
                steps.append(f"  {v.check_type}: {v.from_device} -> {v.to_target} (esperado: {v.expected})")

        return "\n".join(steps)
