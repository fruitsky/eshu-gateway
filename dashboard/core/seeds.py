"""Seed-catalog dispatch for integrations.

The seed catalogs (proxmox, ha, ...) are the curated source of a service's
tools. This module centralizes the kind -> seeder mapping so both the
per-integration Seed endpoint and the automatic startup re-seed use the same
code path.
"""
from db.integrations import get_integrations
from core.proxmox_seed import seed_proxmox_tools
from core.ha_seed import seed_ha_tools

SEEDERS = {
    'proxmox': seed_proxmox_tools,
    'ha': seed_ha_tools,
}


def seed_for_kind(integration):
    """Apply the seed catalog for an integration's kind, if one exists.
    Returns (created, updated) or None if the kind has no seed."""
    seeder = SEEDERS.get(integration.get('kind') or 'custom')
    if not seeder:
        return None
    return seeder(integration['id'])


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
