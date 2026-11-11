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
        "fields": ["id", "type", "node", "status", "name", "vmid", "disk", "maxdisk", "mem", "maxmem"],
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
    # ── F21: snapshot tools. Internal: dashboard code only (§4 of the design).
    {
        "name": "get_cluster_status",
        "description": "Cluster status: the node rows with their name and IP. Used to identify a Proxmox node by address, never by DNS name.",
        "method": "GET",
        "path_template": "/cluster/status",
        "params": [],
        "fields": ["type", "name", "ip", "online", "nodeid", "local"],
        "example": '[{"type": "node", "name": "pve", "ip": "192.0.2.50", "online": 1}]',
        "read_only": True,
        "internal": True,
    },
    {
        "name": "get_access_permissions",
        "description": "The effective permissions of the calling user/token. Used by the dashboard to check that the main token cannot snapshot and that the snapshot token can.",
        "method": "GET",
        "path_template": "/access/permissions",
        "params": [],
        "example": '{"/": {"VM.Audit": 1}, "/vms/101": {"VM.Snapshot": 1}}',
        "read_only": True,
        "internal": True,
    },
    {
        "name": "get_version",
        "description": "The Proxmox VE version, used to know whether VM.Snapshot.Rollback exists (PVE 8).",
        "method": "GET",
        "path_template": "/version",
        "params": [],
        "fields": ["version", "release", "repoid"],
        "example": '{"version": "8.2.4", "release": "8.2", "repoid": "abc123"}',
        "read_only": True,
        "internal": True,
    },
    {
        "name": "get_container_config",
        "description": "Configuration of one LXC container, including its net* entries. Dashboard code only.",
        "method": "GET",
        "path_template": "/nodes/{node}/lxc/{vmid}/config",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Container id.", "required": True},
        ],
        "fields": ["hostname", "net0", "net1", "net2", "net3"],
        "example": '{"hostname": "web01", "net0": "name=eth0,ip=192.0.2.20/24"}',
        "read_only": True,
        "internal": True,
    },
    {
        "name": "get_task_status",
        "description": "Status of one cluster task by UPID: running or stopped, and its exit status. Dashboard code only.",
        "method": "GET",
        "path_template": "/nodes/{node}/tasks/{upid}/status",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "upid", "type": "string", "description": "The URL-encoded UPID.", "required": True},
        ],
        "fields": ["status", "exitstatus", "type", "id"],
        "example": '{"status": "stopped", "exitstatus": "OK"}',
        "read_only": True,
        "internal": True,
    },
    {
        "name": "snapshot_create",
        "description": "Create a snapshot of one guest. Dashboard code only.",
        "method": "POST",
        "path_template": "/nodes/{node}/{type}/{vmid}/snapshot",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "type", "type": "string", "description": "qemu or lxc.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Guest id.", "required": True},
            {"name": "snapname", "type": "string", "description": "Snapshot name.", "required": True},
            {"name": "description", "type": "string", "description": "Snapshot description.", "required": False},
            {"name": "vmstate", "type": "integer", "description": "0 (disk only).", "required": False},
        ],
        "example": '"UPID:pve:00001239:0001A2B3:...:snapshot:101:root@pam:"',
        "read_only": False,
        "internal": True,
    },
    {
        "name": "snapshot_list",
        "description": "List a guest's snapshots. Dashboard code only.",
        "method": "GET",
        "path_template": "/nodes/{node}/{type}/{vmid}/snapshot",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "type", "type": "string", "description": "qemu or lxc.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Guest id.", "required": True},
        ],
        "fields": ["name", "description", "snaptime", "parent"],
        "example": '[{"name": "eshu-r12-1700000000", "description": "eshu request #12", "snaptime": 1700000000}]',
        "read_only": True,
        "internal": True,
    },
    {
        "name": "snapshot_rollback",
        "description": "Roll a guest back to a snapshot. Dashboard code only.",
        "method": "POST",
        "path_template": "/nodes/{node}/{type}/{vmid}/snapshot/{snapname}/rollback",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "type", "type": "string", "description": "qemu or lxc.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Guest id.", "required": True},
            {"name": "snapname", "type": "string", "description": "Snapshot name.", "required": True},
        ],
        "example": '"UPID:pve:00001239:...:rollback:101:root@pam:"',
        "read_only": False,
        "internal": True,
    },
    {
        "name": "snapshot_delete",
        "description": "Delete a guest snapshot. Dashboard code only.",
        "method": "DELETE",
        "path_template": "/nodes/{node}/{type}/{vmid}/snapshot/{snapname}",
        "params": [
            {"name": "node", "type": "string", "description": "Node name.", "required": True},
            {"name": "type", "type": "string", "description": "qemu or lxc.", "required": True},
            {"name": "vmid", "type": "integer", "description": "Guest id.", "required": True},
            {"name": "snapname", "type": "string", "description": "Snapshot name.", "required": True},
        ],
        "example": '"UPID:pve:00001239:...:delsnapshot:101:root@pam:"',
        "read_only": False,
        "internal": True,
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
    # F21: the snapshot tools and the cluster-status read exist for dashboard
    # code only. Marking them here keeps every other seed untouched and makes
    # the flag idempotent on re-seed.
    from db.integrations import set_tools_internal
    internal = [t['name'] for t in PROXMOX_SEED_TOOLS if t.get('internal')]
    set_tools_internal(integration_id, internal, True)
    return created, updated
