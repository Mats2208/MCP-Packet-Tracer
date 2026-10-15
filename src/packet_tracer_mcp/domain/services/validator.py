"""
Topology plan validator.

Uses the rules in domain/rules/ with typed errors.
"""

from __future__ import annotations
from ..models.plans import TopologyPlan
from ..models.errors import ValidationResult
from ..rules.device_rules import validate_devices
from ..rules.ip_rules import validate_ips, validate_dhcp
from ..rules.cable_rules import validate_links
from ..rules.topology_rules import (
    validate_connectivity, validate_routing, validate_wireless,
)


def validate_plan(plan: TopologyPlan) -> ValidationResult:
    """
    Validates a complete plan.
    Returns a ValidationResult with typed errors and warnings.
    Also updates plan.errors and plan.warnings for compatibility.
    """
    result = ValidationResult()

    # Devices
    result.errors.extend(validate_devices(plan))

    # Links and cables
    link_errors, link_warnings = validate_links(plan)
    result.errors.extend(link_errors)
    result.warnings.extend(link_warnings)

    # IPs
    result.errors.extend(validate_ips(plan))

    # DHCP
    dhcp_issues = validate_dhcp(plan)
    # DHCP gateway mismatch is a warning, not a critical error
    for issue in dhcp_issues:
        if issue.code.value == "DHCP_GATEWAY_MISMATCH":
            result.warnings.append(issue)
        else:
            result.errors.append(issue)

    # Graph shape: islands and inconsistent routing. It runs last because it assumes
    # that devices and links have already been validated one at a time.
    result.errors.extend(validate_connectivity(plan))
    result.errors.extend(validate_routing(plan))
    # Warning: this is a PT limitation, not a plan error.
    result.warnings.extend(validate_wireless(plan))

    # Sync with plan.errors/warnings for compatibility
    plan.errors = result.error_messages()
    plan.warnings = result.warning_messages()

    return result
