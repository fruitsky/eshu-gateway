"""Curated seed catalog for Proxmox VE.

These are the operations worth exposing to an agent, with descriptions and
examples written for an LLM (not raw API text). Read-only tools auto-run;
mutating tools route through the approval queue. Seeded idempotently via
the Integrations UI's "Seed Proxmox tools" action; operators then enable the
subset they want.
"""

PROXMOX_SEED_TOOLS = [
    # ── Read-only ──────────────────────────────────────────────────────
    {
        "name": "list_nodes",
        "description": "List all Proxmox cluster nodes and their basic state (online/offline, CPU/mem totals). Use this to discover node names.",
        "method": "GET",
        "path_template": "/nodes",
        "params": [],
        "fields": ["node", "status", "cpu", "maxcpu", "mem", "maxmem", "uptime"],
        "example": '[{"node": "pve", "status": "online", "cpu": 0.1, "maxcpu": 8, "mem": 5e9, "maxmem": 3.2e10}]',
        "read_only": True,
    },
    {
        "name": "get_cluster_resources",
        "description": "List all cluster resources (VMs, LXC containers, storages, nodes) with their running state in one call. Prefer this over per-node listing when you need a fleet-wide overview.",
        "method": "GET",
        "path_template": "/cluster/resources",
        "params": [
            {"name": "type", "type": "string", "description": "Filter by resource type: vm, storage, node, or sdn. Omit for all.", "required": False},
        ],
        "fields": ["id", "type", "node", "status", "name", "vmid"],
        "example": '[{"id": "qemu/100", "type": "qemu", "node": "pve", "status": "running", "name": "my-vm"}]',
        "read_only": True,
    },
    {
        "name": "list_vms",
        "description": "List all QEMU virtual machines on a specific node, including vmid, name, and status.",
        "method": "GET",
        "path_template": "/nodes/{node}/qemu",
        "params": [
            {"name": "node", "type": "string", "description": "Node name (from list_nodes).", "required": True},
        ],
        "fields": ["vmid", "name", "status", "type"],
        "example": '[{"vmid": 100, "name": "my-vm", "status": "running", "cpus": 2, "mem": 2e9}]',
        "read_only": True,
    },
    {
        "name": "get_vm_status",
        "description": "Get the current runtime status of a single VM (running/stopped, qmpstatus, uptime).",
        "method": "GET",
        "path_template": "/nodes/{node}/qemu/{vmid}/status/current",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "VM id.", "required": True},
        ],
        "fields": ["status", "qmpstatus", "uptime", "cpu", "cpus", "mem", "maxmem"],
        "example": '{"status": "running", "qmpstatus": "running", "uptime": 86400, "vmid": 100}',
        "read_only": True,
    },
    {
        "name": "get_vm_config",
        "description": "Get a VM's configuration (name, memory, cores, disks, network).",
        "method": "GET",
        "path_template": "/nodes/{node}/qemu/{vmid}/config",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "VM id.", "required": True},
        ],
        "fields": ["name", "memory", "cores", "sockets", "ostype"],
        "example": '{"name": "my-vm", "memory": 2048, "cores": 2, "net0": "virtio=...", "scsi0": "local-lvm:vm-100-disk-0"}',
        "read_only": True,
    },
    {
        "name": "list_containers",
        "description": "List all LXC containers on a node.",
        "method": "GET",
        "path_template": "/nodes/{node}/lxc",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
        ],
        "fields": ["vmid", "name", "status", "type"],
        "example": '[{"vmid": 200, "name": "ct-nginx", "status": "running"}]',
        "read_only": True,
    },
    {
        "name": "get_container_status",
        "description": "Get the current runtime status of a single LXC container.",
        "method": "GET",
        "path_template": "/nodes/{node}/lxc/{vmid}/status/current",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Container id.", "required": True},
        ],
        "fields": ["status", "uptime", "cpu", "cpus", "mem", "maxmem"],
        "example": '{"status": "running", "uptime": 3600, "vmid": 200}',
        "read_only": True,
    },
    {
        "name": "list_storages",
        "description": "List storage pools configured on a node.",
        "method": "GET",
        "path_template": "/nodes/{node}/storage",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
        ],
        "fields": ["storage", "type", "content", "active", "avail", "used", "total"],
        "example": '[{"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir"}]',
        "read_only": True,
    },
    {
        "name": "get_storage_content",
        "description": "List the contents (disk images, ISOs, backups, templates) of a storage pool on a node.",
        "method": "GET",
        "path_template": "/nodes/{node}/storage/{storage}/content",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "storage", "type": "string", "description": "Storage id (from list_storages).", "required": True},
        ],
        "fields": ["volid", "size", "format", "content"],
        "example": '[{"volid": "local-lvm:vm-100-disk-0", "size": 3.2e10, "format": "raw"}]',
        "read_only": True,
    },
    {
        "name": "get_cluster_tasks",
        "description": "List recent cluster tasks (the Proxmox audit trail) — useful to check whether a prior operation succeeded or is still running.",
        "method": "GET",
        "path_template": "/cluster/tasks",
        "params": [
            {"name": "userfilter", "type": "string", "description": "Filter tasks to a specific user.", "required": False},
            {"name": "limit", "type": "integer", "description": "Max number of tasks to return.", "required": False},
        ],
        "fields": ["type", "status", "user", "node", "starttime", "endtime"],
        "example": '[{"upid": "UPID:pve:...", "type": "qmstart", "status": "OK", "starttime": 1710000000}]',
        "read_only": True,
    },

    # ── Mutating (approval-gated) ──────────────────────────────────────
    {
        "name": "start_vm",
        "description": "Start a stopped QEMU virtual machine. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/nodes/{node}/qemu/{vmid}/status/start",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "VM id.", "required": True},
        ],
        "example": '{"data": "UPID:pve:00001234:..."}',
        "read_only": False,
    },
    {
        "name": "stop_vm",
        "description": "Immediately stop a QEMU virtual machine (hard stop; data may be lost). Prefer shutdown_vm for graceful stop. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/nodes/{node}/qemu/{vmid}/status/stop",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "VM id.", "required": True},
        ],
        "example": '{"data": "UPID:pve:00001235:..."}',
        "read_only": False,
    },
    {
        "name": "shutdown_vm",
        "description": "Gracefully shut down a QEMU virtual machine via ACPI. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/nodes/{node}/qemu/{vmid}/status/shutdown",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "VM id.", "required": True},
        ],
        "example": '{"data": "UPID:pve:00001236:..."}',
        "read_only": False,
    },
    {
        "name": "reboot_vm",
        "description": "Reboot a QEMU virtual machine. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/nodes/{node}/qemu/{vmid}/status/reboot",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "VM id.", "required": True},
        ],
        "example": '{"data": "UPID:pve:00001237:..."}',
        "read_only": False,
    },
    {
        "name": "start_container",
        "description": "Start a stopped LXC container. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/nodes/{node}/lxc/{vmid}/status/start",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Container id.", "required": True},
        ],
        "example": '{"data": "UPID:pve:00001238:..."}',
        "read_only": False,
    },
    {
        "name": "stop_container",
        "description": "Stop an LXC container. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/nodes/{node}/lxc/{vmid}/status/stop",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Container id.", "required": True},
        ],
        "example": '{"data": "UPID:pve:00001239:..."}',
        "read_only": False,
    },
]


def seed_proxmox_tools(integration_id: int):
    """Idempotently insert/refresh the curated Proxmox seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in PROXMOX_SEED_TOOLS:
        if tool['name'] in existing:
            update_tool(
                existing[tool['name']]['id'],
                name=tool['name'],
                description=tool['description'],
                method=tool['method'],
                path_template=tool['path_template'],
                params=tool['params'],
                fields=tool.get('fields'),
                example=tool['example'],
                read_only=tool['read_only'],
                seeded=True,
            )
            updated += 1
        else:
            create_tool(
                integration_id,
                tool['name'],
                tool['description'],
                tool['method'],
                tool['path_template'],
                tool['params'],
                tool['example'],
                read_only=tool['read_only'],
                fields=tool.get('fields'),
                seeded=True,
            )
            created += 1
    return created, updated
