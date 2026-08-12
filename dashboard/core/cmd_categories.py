# Static command-to-category mapping.
# Used by the statistics API to categorize gateway activity.
# Order matters — first match wins.

# (prefix, category_label)
CATEGORY_RULES = [
    # System Services
    ("systemctl", "System Services"),
    ("journalctl", "System Services"),
    ("service ", "System Services"),
    ("systemd-run", "System Services"),
    # Package Management
    ("apt ", "Package Management"),
    ("apt-get", "Package Management"),
    ("dpkg ", "Package Management"),
    ("snap ", "Package Management"),
    ("pip ", "Package Management"),
    ("npm ", "Package Management"),
    # Network
    ("curl ", "Network"),
    ("wget ", "Network"),
    ("ping", "Network"),
    ("ss ", "Network"),
    ("ip a", "Network"),
    ("ip link", "Network"),
    ("ip route", "Network"),
    ("ifconfig", "Network"),
    ("dig", "Network"),
    ("nslookup", "Network"),
    ("netstat", "Network"),
    ("nmap", "Network"),
    ("tcpdump", "Network"),
    ("iptables", "Network"),
    ("ufw ", "Network"),
    ("firewall-cmd", "Network"),
    ("nc ", "Network"),
    ("traceroute", "Network"),
    # Storage & Filesystem
    ("df ", "Storage & FS"),
    ("du ", "Storage & FS"),
    ("mount", "Storage & FS"),
    ("lsblk", "Storage & FS"),
    ("fdisk", "Storage & FS"),
    ("mkfs", "Storage & FS"),
    ("dd ", "Storage & FS"),
    ("zfs ", "Storage & FS"),
    ("ceph ", "Storage & FS"),
    ("tar ", "Storage & FS"),
    ("gzip", "Storage & FS"),
    ("gunzip", "Storage & FS"),
    ("rsync", "Storage & FS"),
    ("scp ", "Storage & FS"),
    ("find ", "Storage & FS"),
    ("ls ", "Storage & FS"),
    ("cp ", "Storage & FS"),
    ("mv ", "Storage & FS"),
    ("rm ", "Storage & FS"),
    ("mkdir", "Storage & FS"),
    ("rmdir", "Storage & FS"),
    ("chmod", "Storage & FS"),
    ("chown", "Storage & FS"),
    ("ln ", "Storage & FS"),
    ("blkid", "Storage & FS"),
    ("stat ", "Storage & FS"),
    ("file ", "Storage & FS"),
    ("tree ", "Storage & FS"),
    ("sync", "Storage & FS"),
    # Containers & VMs
    ("docker ", "Containers"),
    ("lxc ", "Containers"),
    ("pct ", "Containers"),
    ("qm ", "VMs"),
    ("pvesh", "Proxmox"),
    ("pvecm", "Proxmox"),
    ("pveceph", "Proxmox"),
    ("kubectl", "Containers"),
    # Monitoring & Observability
    ("sensors", "Monitoring"),
    ("iostat", "Monitoring"),
    ("vmstat", "Monitoring"),
    ("sar ", "Monitoring"),
    ("nethogs", "Monitoring"),
    ("iftop", "Monitoring"),
    ("watch ", "Monitoring"),
    ("tail -f", "Monitoring"),
    ("tail -n", "Monitoring"),
    ("tail -", "Monitoring"),
    # Databases
    ("mysql", "Databases"),
    ("psql", "Databases"),
    ("sqlite3", "Databases"),
    ("redis-cli", "Databases"),
    ("mongosh", "Databases"),
    ("pg_dump", "Databases"),
    ("mysqldump", "Databases"),
    # Security
    ("openssl", "Security"),
    ("certbot", "Security"),
    ("fail2ban", "Security"),
    ("sestatus", "Security"),
    ("auditctl", "Security"),
    ("ausearch", "Security"),
    ("ssh-keygen", "Security"),
    ("nmap", "Security"),
    ("passwd", "Security"),
    ("useradd", "Security"),
    ("usermod", "Security"),
    ("userdel", "Security"),
    # Version Control
    ("git ", "Version Control"),
    # Scripting & Languages
    ("python3", "Scripting"),
    ("python ", "Scripting"),
    ("node ", "Scripting"),
    ("npm ", "Scripting"),
    ("bash ", "Scripting"),
    ("make", "Scripting"),
    ("gcc", "Scripting"),
    # Task Scheduling
    ("crontab", "Task Scheduling"),
    ("at ", "Task Scheduling"),
    # System Info & Basics
    ("free ", "System Info"),
    ("top", "System Info"),
    ("htop", "System Info"),
    ("ps ", "System Info"),
    ("uptime", "System Info"),
    ("dmesg", "System Info"),
    ("uname", "System Info"),
    ("hostname", "System Info"),
    ("whoami", "System Info"),
    ("id ", "System Info"),
    ("who ", "System Info"),
    ("w ", "System Info"),
    ("last", "System Info"),
    ("lscpu", "System Info"),
    ("lshw", "System Info"),
    ("lspci", "System Info"),
    ("lsusb", "System Info"),
    ("reboot", "System"),
    ("shutdown", "System"),
    ("poweroff", "System"),
    # Editing & Viewing
    ("vim ", "Editing"),
    ("nano ", "Editing"),
    ("micro", "Editing"),
    ("cat ", "Editing"),
    ("less ", "Editing"),
    ("more ", "Editing"),
    ("head ", "Editing"),
    ("tail ", "Editing"),
    # Utilities
    ("echo ", "Utilities"),
    ("date", "Utilities"),
    ("cal", "Utilities"),
    ("bc ", "Utilities"),
    ("yes ", "Utilities"),
    ("sleep ", "Utilities"),
    ("timeout ", "Utilities"),
    ("xargs", "Utilities"),
    ("sort", "Utilities"),
    ("uniq", "Utilities"),
    ("tee ", "Utilities"),
    ("cut ", "Utilities"),
    ("tr ", "Utilities"),
    ("awk ", "Utilities"),
    ("sed ", "Utilities"),
    ("grep ", "Utilities"),
    ("wc ", "Utilities"),
    ("diff", "Utilities"),
    ("cmp", "Utilities"),
    ("jq ", "Utilities"),
    ("yq ", "Utilities"),
]

# Priority-ordered categories for display
CATEGORY_ORDER = [
    "Storage & FS", "System Services", "Network", "Package Management",
    "Containers", "VMs", "Proxmox", "Monitoring", "Databases", "Security",
    "Version Control", "Scripting", "Task Scheduling", "System Info",
    "System", "Editing", "Utilities", "Other"
]


def categorize_command(cmd: str) -> str:
    """Return the category label for a command. Strips common prefixes first."""
    import re as _re
    c = cmd.strip()
    for pfx in ('sudo ', 'nice ', 'nohup ', 'ionice ', 'env ', 'time '):
        if c.startswith(pfx):
            c = c[len(pfx):]
            break
    for prefix, label in CATEGORY_RULES:
        if c.startswith(prefix):
            return label
    return "Other"
