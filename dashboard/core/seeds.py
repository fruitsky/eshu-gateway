"""Seed-catalog dispatch for integrations.

The seed catalogs (proxmox, ha, ...) are the curated source of a service's
tools. This module centralizes the kind -> seeder mapping so both the
per-integration Seed endpoint and the automatic startup re-seed use the same
code path. Every integration additionally receives the generic passthrough
tools (read/write, and WS tools for HA) so agents are never blocked by an
un-curated endpoint.
"""
from db.integrations import get_integrations
from core.proxmox_seed import seed_proxmox_tools
from core.ha_seed import seed_ha_tools
from core.omada_seed import seed_omada_tools
from core.pulse_seed import seed_pulse_tools
from core.generic_tools import seed_generic_tools

SEEDERS = {
    'proxmox': seed_proxmox_tools,
    'ha': seed_ha_tools,
    'omada': seed_omada_tools,
    'pulse': seed_pulse_tools,
}


def seed_for_kind(integration):
    """Apply the seed catalog for an integration's kind, if one exists, plus
    the generic passthrough floor. Returns (created, updated)."""
    seeder = SEEDERS.get(integration.get('kind') or 'custom')
    curated = seeder(integration['id']) if seeder else (0, 0)
    generic = seed_generic_tools(integration['id'], integration.get('kind') or 'custom')
    return curated[0] + generic[0], curated[1] + generic[1]


def reseed_all_integrations():
    """Re-apply each integration's seed catalog on startup. Idempotent — the
    seed updates tools in place and preserves the operator's enable/disable
    state, so a deployed catalog change propagates without a manual re-seed."""
    results = {}
    for integration in get_integrations():
        res = seed_for_kind(integration)
        if res is not None:
            results[integration['name']] = res
    return results
