"""
LiveExecutor: sends topology commands to Packet Tracer in real-time
via the HTTP bridge (PTBuilder webview polling).
"""

from __future__ import annotations
import json
import time
from ...domain.models.plans import TopologyPlan
from ..generator.ptbuilder_generator import generate_executable_script


class LiveExecutor:
    """Execute topology plans directly in a running Packet Tracer instance."""

    def __init__(self, bridge):
        self._bridge = bridge

    def execute(self, plan: TopologyPlan, delay: float = 1.0) -> dict:
        """
        Send all topology commands to PT through the bridge.

        Returns dict with execution details.
        """
        if not self._bridge.is_connected:
            return {
                "success": False,
                "error": "Bridge not connected. Make sure PTBuilder is polling.",
                "commands_sent": 0,
            }

        script = generate_executable_script(plan)
        # Split into individual commands (each line that isn't a comment/blank)
        commands = [
            line.strip() for line in script.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]

        sent = 0
        for cmd in commands:
            self._bridge.send(cmd)
            sent += 1
            time.sleep(delay)

        return {
            "success": True,
            "commands_sent": sent,
            "devices": len(plan.devices),
            "links": len(plan.links),
        }
