"""Seed-catalog dispatch for integrations.

The seed catalogs (proxmox, ha, ...) are the curated source of a service's
tools. This module centralizes the kind -> seeder mapping so both the
per-integration Seed endpoint and the automatic startup re-seed use the same
code path. Every integration additionally receives the generic passthrough
tools (read/write, and WS tools for HA) so agents are never blocked by an
un-curated endpoint.
"""
from db.integrations import get_integrations
from core.proxmox_seed import PROXMOX_SEED_TOOLS, seed_proxmox_tools
from core.ha_seed import HA_SEED_TOOLS, seed_ha_tools
from core.omada_seed import OMADA_SEED_TOOLS, seed_omada_tools
from core.pulse_seed import PULSE_SEED_TOOLS, seed_pulse_tools
from core.jellyfin_seed import JELLYFIN_SEED_TOOLS, seed_jellyfin_tools
from core.pihole_seed import PIHOLE_SEED_TOOLS, seed_pihole_tools
from core.arr_seed import SONARR_SEED_TOOLS, RADARR_SEED_TOOLS, seed_sonarr_tools, seed_radarr_tools
from core.prowlarr_seed import PROWLARR_SEED_TOOLS, seed_prowlarr_tools
from core.generic_tools import generic_tools_for, seed_generic_tools

SEEDERS = {
    'proxmox': seed_proxmox_tools,
    'ha': seed_ha_tools,
    'omada': seed_omada_tools,
    'pulse': seed_pulse_tools,
    'jellyfin': seed_jellyfin_tools,
    'pihole': seed_pihole_tools,
    'sonarr': seed_sonarr_tools,
    'radarr': seed_radarr_tools,
    'prowlarr': seed_prowlarr_tools,
}

_CATALOGS = {
    'proxmox': PROXMOX_SEED_TOOLS,
    'ha': HA_SEED_TOOLS,
    'omada': OMADA_SEED_TOOLS,
    'pulse': PULSE_SEED_TOOLS,
    'jellyfin': JELLYFIN_SEED_TOOLS,
    'pihole': PIHOLE_SEED_TOOLS,
    'sonarr': SONARR_SEED_TOOLS,
    'radarr': RADARR_SEED_TOOLS,
    'prowlarr': PROWLARR_SEED_TOOLS,
}

# Kinds that must NOT receive the generic read/write passthrough floor. Jellyfin
# is fully curated because its API key is admin-level and cannot be scoped down
# — a generic passthrough would undo the write gating. Pi-hole is fully curated
# because toggling blocking is LAN-wide and the surface is tiny. Sonarr/Radarr
# are fully curated because *arr writes can trigger torrent searches and file
# deletions. Prowlarr is fully curated because indexer definitions carry
# credentials in fields[] and a generic read would expose them.
NO_GENERIC_KINDS = {'jellyfin', 'pihole', 'sonarr', 'radarr', 'prowlarr'}


def seed_tool_names(kind: str) -> set:
    """Names of the tools that seeding would create/re-create for a kind —
    the curated catalog plus the generic floor (where applicable). Used to flag
    seed-managed tools in the UI so operators don't delete them expecting them
    to stay gone (they reappear on the next reseed)."""
    names = {t['name'] for t in _CATALOGS.get(kind or 'custom', [])}
    if (kind or 'custom') not in NO_GENERIC_KINDS:
        names.update(t['name'] for t in generic_tools_for(kind or 'custom'))
    return names


def seed_for_kind(integration):
    """Apply the seed catalog for an integration's kind, if one exists, plus
    the generic passthrough floor (except NO_GENERIC_KINDS). Returns
    (created, updated)."""
    seeder = SEEDERS.get(integration.get('kind') or 'custom')
    curated = seeder(integration['id']) if seeder else (0, 0)
    if (integration.get('kind') or 'custom') in NO_GENERIC_KINDS:
        return curated
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
