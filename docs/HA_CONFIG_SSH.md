# Editing Home Assistant YAML through Eshu (F11)

Eshu edits `/config/*.yaml` through the **Advanced SSH & Web Terminal**
add-on. The add-on is not an Eshu gateway (it is an Alpine container without
systemd, rebuilt on every add-on update), so the dashboard opens one SSH
connection per call with a key made only for this use.

## 1. The add-on

1. Install **Advanced SSH & Web Terminal**, keep **Protection mode on**, and
   bind it to the LAN only (no ingress, no public exposure).
2. Create the Eshu key pair on the dashboard host (or let the dashboard hold
   an existing ed25519 key):

   ```bash
   ssh-keygen -t ed25519 -N '' -C eshu-ha-config -f /tmp/eshu-ha-config
   ```

3. Add `/tmp/eshu-ha-config.pub` to the add-on's `authorized_keys` for the
   login user (normally `root`). Prefix it with
   `from="<dashboard address>"` if the dashboard has a fixed address.
4. On the add-on, `/config` is a symlink to `/homeassistant` (confirmed with
   BusyBox 1.37.0). Eshu's policy is root-relative because of that.

## 2. The integration in Eshu

Create an integration of kind `ha_ssh` with:

| field | value |
|---|---|
| `name` | a label, e.g. `ha-config` |
| `base_url` | `host` or `host:port` of the add-on |
| `secret` | the **private** key (`/tmp/eshu-ha-config`), Eshu-only |
| `config_json.ssh_user` | the login user (default `root`) |
| `config_json.ssh_port` | override the port from `base_url` if needed |

The API accepts the key as a JSON string; the dashboard form's single-line
secret field is not suitable for a PEM key, so create it with `curl` and the
session cookie you already use for the dashboard, or import it from a script.

## 3. Pin the host key (TOFU)

Run `ha_config_list` once. That first connection records the add-on's host key
and shows its `SHA256:…` fingerprint in the integration card. Compare it with
the add-on:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

A later connection whose key differs is refused (`ssh_host_key_changed`) and
audited. **Writes are refused until the pin exists** (`ssh_host_unpinned`), so
a write can never establish trust.

## 4. What the tools do

| tool | gated | notes |
|---|---|---|
| `ha_config_list()` | no | YAML under `/config` and `/config/packages`, with size and mtime |
| `ha_config_read(path)` | no | content, sha256, size; `secrets.yaml` returns key names with values masked |
| `ha_config_write(path, content, expect_hash, dry_run)` | **yes** | `dry_run` returns the diff; a real write backs up, writes atomically, copies mode/owner, runs `ha core check`, and restores the backup automatically if the check fails |
| `ha_config_restore(path, backup)` | **yes** | restores one `.eshu-<timestamp>.bak`, then checks; a failed check puts the previous content back |
| `ha_config_check()` | no | runs `ha core check` and records a valid result for `ha_core_restart` (10-minute window) |

A write's expected hash comes from `ha_config_read`; if the file changed in
between, nothing is written (`config_hash_changed`). Freeze blocks writes and
restores; reads keep working.

## 5. What Eshu does not do

Install packages, change add-on options, restart the add-on, touch `.storage`,
or run arbitrary commands. There is no shell and no `ssh_exec` here: every
command is a fixed template. If the add-on is removed or its key rotated,
writes fail closed until the integration is updated. If a write is
interrupted, the backup name is shown and the integration refuses further
writes (`config_state_unknown`) until a read of the path shows which content
is in place.
