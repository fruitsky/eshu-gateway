# Static dictionary of common commands with one-line descriptions.
# Used by the statistics API to enrich the top commands list.
# Matched by longest-prefix lookup.
import re

CMD_DESCRIPTIONS = {
    # System info
    "uptime": "System uptime and load average",
    "uname -a": "Kernel version and system info",
    "hostname": "Display system hostname",
    "dmesg": "Kernel ring buffer messages",
    "whoami": "Current username",
    "id": "User and group IDs",
    "last": "Last logged-in users",
    "lscpu": "CPU architecture info",
    "lsblk": "Block device listing",
    "lsusb": "USB device listing",
    "lspci": "PCI device listing",
    "lshw": "Hardware configuration",
    "free -h": "Memory usage summary",
    "df -h": "Disk space per filesystem",
    "du -sh": "Directory disk usage",
    "mount": "Mounted filesystems",
    "blkid": "Block device UUIDs and labels",
    # Process & service management
    "ps aux": "All running processes",
    "top": "Interactive process viewer",
    "htop": "Interactive process viewer (enhanced)",
    "systemctl status": "Service status information",
    "systemctl restart": "Restart a service",
    "systemctl start": "Start a service",
    "systemctl stop": "Stop a service",
    "systemctl enable": "Enable service at boot",
    "systemctl disable": "Disable service at boot",
    "systemctl list-units": "List active systemd units",
    "systemctl daemon-reload": "Reload systemd configuration",
    "systemctl": "Manage system services",
    "journalctl -xe": "Systemd journal with explanations",
    "journalctl -u": "Journal logs for a specific unit",
    "journalctl --follow": "Tail journal logs in real time",
    "service": "SysV service management",
    # Package management
    "apt update": "Update package lists",
    "apt upgrade": "Upgrade all packages",
    "apt install": "Install a package",
    "apt remove": "Remove a package",
    "apt autoremove": "Remove unused dependencies",
    "apt list": "List installed packages",
    "dpkg -l": "List all installed Debian packages",
    "dpkg --configure -a": "Fix interrupted package installation",
    "snap list": "List installed Snap packages",
    # Network
    "ip a": "Network interface addresses",
    "ip link": "Network interface status",
    "ip route": "Routing table",
    "ifconfig": "Network interface config (legacy)",
    "ss -tlnp": "Listening TCP sockets with process",
    "ss -ulnp": "Listening UDP sockets with process",
    "ping": "Test network connectivity to a host",
    "traceroute": "Trace network path to a host",
    "dig": "DNS lookup utility",
    "nslookup": "DNS name resolution",
    "netstat -tlnp": "Network connections (legacy)",
    "curl": "HTTP client for API requests",
    "wget": "Download files via HTTP/HTTPS",
    "nc": "Netcat — TCP/UDP swiss army knife",
    "nmap": "Network port scanner",
    "tcpdump": "Network packet capture",
    "iptables -L": "Firewall rules listing",
    "ufw status": "Uncomplicated Firewall status",
    "firewall-cmd": "FirewallD management",
    # Storage & filesystem
    "ls": "List directory contents",
    "cd": "Change the current working directory",
    "ls -la": "List all files with details",
    "find": "Search for files in directory tree",
    "find / -exec": "Find and run a command on matching files",
    "find / -maxdepth": "Find within a depth-limited tree",
    "find / -type": "Find files by type (f=file, d=dir, l=symlink)",
    "find / -name": "Find files by name pattern",
    "find / -mtime": "Find files by modification time",
    "find / -size": "Find files by size",
    "find / -delete": "Find and delete matching files",
    "grep": "Search text with patterns",
    "cat": "Concatenate and display files",
    "less": "Pager for file viewing",
    "more": "Pager for file viewing (basic)",
    "head": "First lines of a file",
    "tail": "Last lines of a file",
    "tail -f": "Follow file in real time",
    "wc": "Word, line, and byte count",
    "sort": "Sort lines of text",
    "uniq": "Filter duplicate lines",
    "tee": "Pipe output to file and stdout",
    "diff": "Line-by-line file comparison",
    "chmod": "Change file permissions",
    "chown": "Change file owner and group",
    "ln": "Create file links",
    "rsync": "Efficient file sync/copy",
    "scp": "Secure file copy over SSH",
    "tar": "Archive utility (tar.gz)",
    "gzip": "Compress files",
    "gunzip": "Decompress gzip files",
    "zip": "Create ZIP archives",
    "unzip": "Extract ZIP archives",
    "md5sum": "Compute MD5 checksum",
    "sha256sum": "Compute SHA-256 checksum",
    "stat": "Detailed file metadata",
    "file": "Detect file type",
    "tree": "Directory tree view",
    "cp": "Copy files and directories",
    "mv": "Move/rename files",
    "rm": "Remove files",
    "mkdir": "Create directory",
    "rmdir": "Remove empty directory",
    # Container & VM management
    "docker": "Manage Docker containers and images",
    "docker ps": "List running containers",
    "docker ps -a": "List all containers",
    "docker images": "List Docker images",
    "docker pull": "Pull a container image",
    "docker start": "Start a container",
    "docker stop": "Stop a container",
    "docker restart": "Restart a container",
    "docker logs": "Fetch container logs",
    "docker exec": "Run command in a running container",
    "docker compose": "Docker Compose multi-container management",
    "docker inspect": "Inspect a container or image details",
    "docker compose up": "Start services defined in a compose file",
    "lxc list": "List LXD containers",
    "lxc info": "LXD container info",
    "pct list": "List Proxmox containers",
    "qm list": "List Proxmox VMs",
    "qm start": "Start a Proxmox VM",
    "qm stop": "Stop a Proxmox VM",
    "pvesh": "Proxmox API shell",
    # User management
    "useradd": "Create a user account",
    "usermod": "Modify a user account",
    "userdel": "Delete a user account",
    "passwd": "Change user password",
    "groupadd": "Create a group",
    "groups": "Show user group memberships",
    "who": "Who is logged in",
    "w": "Who is logged in (with activity)",
    # SSH
    "ssh": "Secure shell client",
    "ssh-keygen": "Generate SSH key pair",
    "ssh-copy-id": "Install SSH public key on remote host",
    # Task scheduling
    "crontab -l": "List cron jobs",
    "crontab -e": "Edit cron jobs",
    "at": "Schedule one-time task",
    "systemd-run": "Run a transient systemd service",
    # Security
    "openssl": "OpenSSL cryptography tool",
    "certbot": "Let's Encrypt TLS certificate tool",
    "fail2ban-client": "Fail2ban management",
    "auditctl": "Linux audit framework control",
    "ausearch": "Linux audit framework search",
    "sestatus": "SELinux status",
    # Logs & monitoring
    "tail -n 100": "Show last 100 lines of a file",
    "watch": "Run command repeatedly with output",
    "vmstat": "Virtual memory and system stats",
    "iostat": "I/O device statistics",
    "sar": "System activity reporter",
    "sensors": "Hardware sensor readings (lm-sensors)",
    "nethogs": "Network traffic by process",
    "iftop": "Network bandwidth monitoring",
    # Database
    "mysql": "MySQL/MariaDB client",
    "psql": "PostgreSQL client",
    "sqlite3": "SQLite3 database client",
    "redis-cli": "Redis client",
    "mongosh": "MongoDB shell",
    # Backup & restore
    "pg_dump": "PostgreSQL database dump",
    "mysqldump": "MySQL database dump",
    "restic": "Restic backup tool",
    "borg": "Borg backup tool",
    "rclone": "Cloud storage sync",
    # Proxmox specific
    "pvecm": "Proxmox cluster management",
    "pveceph": "Proxmox Ceph management",
    "ceph": "Ceph storage management",
    "zfs": "ZFS filesystem management",
    "pct enter": "Enter a Proxmox container shell",
    "qm terminal": "Open a Proxmox VM terminal",
    # Other utilities
    "echo": "Print text to stdout",
    "date": "Display or set date/time",
    "cal": "Calendar display",
    "bc": "Arbitrary precision calculator",
    "jq": "JSON query and manipulation",
    "yq": "YAML query and manipulation",
    "python3": "Python 3 interpreter",
    "node": "Node.js JavaScript runtime",
    "npm": "Node.js package manager",
    "git": "Distributed version control",
    "vim": "Text editor (Vi IMproved)",
    "nano": "Simple text editor",
    "micro": "Terminal text editor",
    "screen": "Terminal multiplexer",
    "tmux": "Terminal multiplexer",
    "tmux new-session": "Create a new tmux session",
    "tmate": "Instant terminal sharing",
    "env": "Display environment variables",
    "set": "Display shell variables",
    "alias": "Display or create shell aliases",
    "history": "Command history",
    "yes": "Output repeating 'y'",
    "sleep": "Delay for a specified time",
    "timeout": "Run command with time limit",
    "xargs": "Build and execute command lines",
    "parallel": "Run jobs in parallel",
    "make": "Build automation tool",
    "gcc": "GNU C compiler",
    "curl -s": "Quiet HTTP client for API calls",
    "curl -fsSL": "Silent HTTPS client for scripting",
    "curl -X POST": "HTTP POST request",
    "curl -X GET": "HTTP GET request",
}


# Auto-loaded descriptions from the system whatis database
_WHATSIS_CACHE = {}

def load_whatis_db():
    """Parse /usr/share/man/whatis into a base-command -> description dict."""
    import os
    path = '/usr/share/man/whatis'
    if not os.path.exists(path):
        return
    with open(path, errors='ignore') as f:
        for line in f:
            line = line.strip()
            if ' - ' not in line or line.startswith('#'):
                continue
            name = line.split(' (', 1)[0].strip()
            desc = line.split(' - ', 1)[-1].strip()
            if name and desc:
                _WHATSIS_CACHE[name] = desc

_PREFIXES_TO_STRIP = ('sudo ', 'nice ', 'nohup ', 'ionice ', 'env ', 'time ')

# Commands whose descriptions are trivial/noise — suppress entirely.
TRIVIAL_COMMANDS = {'echo', 'printf', 'date', 'whoami', 'sleep', 'yes', 'true',
                    'false', 'env', 'cd', 'echo -e', 'echo '}


# Chain operators used to split compound commands into meaningful fragments.
# Spaces are required around `|` so grep -E 'foo|bar' stays a single fragment.
_CHAIN_SPLIT = re.compile(r'\s*(?:&&|\|\||;)\s*|\s*\|\s*')


def _describe_single(cmd: str) -> str:
    """Describe a single (non-chained) command."""
    cmd = cmd.strip()
    for pfx in _PREFIXES_TO_STRIP:
        if cmd.startswith(pfx):
            cmd = cmd[len(pfx):]
            break
    base = cmd.split()[0] if cmd.split() else cmd
    # Suppress trivial commands entirely
    if base in TRIVIAL_COMMANDS:
        return ""
    best = ""
    for prefix, desc in CMD_DESCRIPTIONS.items():
        if cmd.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    if best:
        return CMD_DESCRIPTIONS[best]
    # Fall back to whatis cache via base command
    whatis_desc = _WHATSIS_CACHE.get(base, _WHATSIS_CACHE.get(cmd, ""))
    if whatis_desc:
        return whatis_desc
    # Final fallback: find the static entry whose prefix most closely matches
    # the base command (e.g. base "docker" matches "docker ps").
    for prefix, desc in CMD_DESCRIPTIONS.items():
        if prefix.split()[0] == base and len(prefix) > len(best):
            best = prefix
    return CMD_DESCRIPTIONS.get(best, "")


def describe_command(cmd: str) -> str:
    """Return a description for a command, handling compound chains.
    Splits on && / || / ; / |, describes each meaningful fragment, and joins
    the results so longer chains produce a fuller summary."""
    parts = [p for p in _CHAIN_SPLIT.split(cmd.strip()) if p.strip()]
    if len(parts) > 1:
        descs = [d for d in (_describe_single(p) for p in parts) if d]
        if len(descs) == 1:
            return descs[0]
        if len(descs) > 1:
            return ' · '.join(descs)
        return ''
    return _describe_single(cmd)
