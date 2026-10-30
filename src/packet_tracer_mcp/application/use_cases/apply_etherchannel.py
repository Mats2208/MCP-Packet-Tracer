"""Use case: bundle switch ports into an EtherChannel in PT."""

from __future__ import annotations
from typing import Callable

from ...domain.models.etherchannel import EtherChannelConfig
from ...domain.rules.etherchannel_rules import (
    validate_etherchannel, validate_etherchannel_against_topology,
)
from ...infrastructure.generator.etherchannel_cli_generator import generate_etherchannel_cli
from .apply_router_features import run_ios_feature


def apply_etherchannel_uc(cfg: EtherChannelConfig, query_pt_topology: Callable | None = None,
                          bridge_send: Callable | None = None, dry_run: bool = False) -> dict:
    return run_ios_feature(cfg, cfg.switch, validate_etherchannel,
                           validate_etherchannel_against_topology, generate_etherchannel_cli,
                           query_pt_topology, bridge_send, dry_run)
