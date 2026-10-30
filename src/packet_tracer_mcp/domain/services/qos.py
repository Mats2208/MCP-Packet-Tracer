"""QoS read-back (pt_read_qos): policy-map classes named from the running-config.

Measured on PT 9 (2026-10-30) on a 2911:
- PolicyMapManager.getPolicyMapCount() includes "?default-policy-map?" (dropped
  by the reader); getPolicyMapAt(i) -> getMapName, getClassCnt,
  isPriorityConfigured, isFairQueueConfigured, getClassAt(k).
- A policy-map class (PolicyMapQosClass) gives getPriority (kbps),
  isBandwidthConfigured, getBandwidth, isFairQueueConfigured, isClassDefault, but
  NOT its name (getClassName() answers the class "PolicyMapQosClass"). The classes
  come in running-config order, so the names are taken from `show running-config`.
- PolicyMap.toString() THROWS: never called.
"""

from __future__ import annotations

DEFAULT_CLASS = "class-default"


def policy_map_classes(running_config: str) -> dict[str, list[str]]:
    """{policy-map name: [class names in order]} from a running-config."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in (running_config or "").splitlines():
        if line.startswith("policy-map "):
            current = out.setdefault(line[len("policy-map "):].strip(), [])
        elif current is not None and line.startswith(" class "):
            current.append(line[len(" class "):].strip())
        elif line and not line.startswith(" "):
            current = None
    return out


def _running_text(raw) -> str:
    """enterCommand answers {first, second}; the config is whichever holds text."""
    if isinstance(raw, dict):
        parts = [str(raw.get(k) or "") for k in ("first", "second")]
        return max(parts, key=lambda t: t.count("\n"))
    return str(raw or "")


def name_classes(classes: list[dict], names: list[str]) -> list[dict]:
    """Give each API class its CLI name, by position.

    class-default is matched by its flag; the others by order among the
    non-default ones. A class left without a CLI counterpart keeps name None.
    """
    custom = [n for n in names if n != DEFAULT_CLASS]
    i = 0
    for cls in classes:
        if cls.get("class_default"):
            cls["name"] = DEFAULT_CLASS
        else:
            cls["name"] = custom[i] if i < len(custom) else None
            i += 1
    return classes


def _class_view(raw: dict) -> dict:
    prio = raw.get("priority_kbps")
    return {
        "name": None,
        "class_default": bool(raw.get("class_default")),
        "priority_kbps": prio if isinstance(prio, (int, float)) and prio > 0 else None,
        "bandwidth_kbps": raw.get("bandwidth") if raw.get("bandwidth_configured") else None,
        "fair_queue": raw.get("fair_queue"),
    }


def qos_policy_maps(policy_maps: list[dict], running_config) -> tuple[list[dict], list[str]]:
    """Policy-maps with named classes, plus warnings when the CLI and the API disagree."""
    text = _running_text(running_config)
    names = policy_map_classes(text)
    warnings: list[str] = []
    if policy_maps and not text.strip():
        warnings.append("The running-config could not be read: class names are unknown.")
    out = []
    for pm in policy_maps:
        classes = [_class_view(c) for c in pm.get("classes") or [] if isinstance(c, dict)]
        cli = names.get(pm.get("name") or "")
        if cli is None and not text.strip():
            cli = []
        elif cli is None:
            warnings.append(f"policy-map {pm.get('name')} is not in the running-config: "
                            "its classes stay unnamed.")
            cli = []
        name_classes(classes, cli)
        if cli and [c["name"] for c in classes if c["name"] != DEFAULT_CLASS] != \
                [n for n in cli if n != DEFAULT_CLASS]:
            warnings.append(f"policy-map {pm.get('name')}: the API lists {len(classes)} "
                            f"class(es), the running-config {len(cli)}; names may not line up.")
        view = {k: v for k, v in pm.items() if k != "classes"}
        view["classes"] = classes
        out.append(view)
    return out, warnings
