"""F11: edit Home Assistant YAML through the Advanced SSH & Web Terminal add-on.

The add-on is not an Eshu gateway (Alpine, no systemd, rebuilt on update), so
the dashboard opens the SSH connection itself. The transport reuses F12's
hardened client (`core/ssh_client`) with a per-integration key and a pinned
host key; every command is one of the fixed templates below, never agent text.
The path rules and the write preflight/scan live in
`core/ha_config_preflight.py` (part 1).

Two invariants shape the write path:

* **one connection, one script** — realpath, hash check, backup, write,
  mode/owner copy, `mv`, `ha core check` and the automatic restore all happen
  in a single remote `sh` under a remote `timeout 100`, with one exit code per
  step. A dead SSH client cannot leave a second writer behind.
* **unknown is not failure** — an exit outside the script's known range (or a
  client timeout) leaves the backup on disk and is reported as
  `config_state_unknown`; the integration then refuses writes until a
  `ha_config_read` of the same path shows which content is in place.
"""
import os
import re
import secrets
import shlex
import tempfile
import threading
import time

from core import ssh_client, ssh_keys
from core.ha_config_preflight import (
    ConfigError, MAX_CONTENT, ROOT, SECRETS, check_write_path, scan,
    to_root_path, unified_diff, validate_write,
)
from core.secret_scrub import scrub_string
from db.audit import record_audit_event
from db.integrations import (
    get_integration_config, record_integration_call, set_integration_config,
)
from db.misc import get_meta_json, set_meta_json

TIMEOUT_SHORT = 30
TIMEOUT_LONG = 120
MAX_LIST = 200
USER_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$')
HOST_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$')
BACKUP_RE = re.compile(r'^' + re.escape(ROOT) + r'/[A-Za-z0-9._/-]{1,220}'
                       r'\.eshu-[0-9]{10}\.bak$')
REFUSED_DIRS = ('.storage/', '.ssh/', 'deps/', 'tts/')
REFUSED_SUFFIXES = ('.db', '.db-wal', '.db-shm')

# W4: /config is a symlink to /homeassistant on the add-on; BusyBox find
# does not follow a symlinked start point, so both walks start at the real
# root. U2: packages/ is walked recursively and is writable.
LIST_SCRIPT = (
    "find /homeassistant -maxdepth 1 -type f "
    "\\( -name '*.yaml' -o -name '*.yml' \\) "
    "-exec stat -c '%s %Y %n' {} \\; ; "
    "find /homeassistant/packages -type f "
    "\\( -name '*.yaml' -o -name '*.yml' \\) "
    "-exec stat -c '%s %Y %n' {} \\; 2>/dev/null"
)

# H2/U1-U4/W1-W4: one script, one connection, one exit code per step.
# BN3/Z1: verify and read in **one** connection, on one descriptor. The old
# read ran `realpath`/`stat` and then `head` in two calls, so a file swapped
# between them (a symlink to ../secrets.yaml or .storage) was read unmasked:
# the masking decision came from the requested name. Here the descriptor is
# opened once and its own target is checked before any byte is read.
READ_SCRIPT = r"""
# READ_SCRIPT — one descriptor, one read (BN3).
set -eu
path=$1
case "$path" in /*) ;; *) exit 19 ;; esac
real=$(realpath "$path") || exit 12
[ -f "$real" ] || exit 12
[ ! -L "$real" ] || exit 13
dir=$(dirname "$real")
case "$dir" in
  /homeassistant|/homeassistant/packages|/homeassistant/packages/*) ;;
  *) exit 11 ;;
esac
[ "$(realpath "$(dirname "$path")")" = "$dir" ] || exit 13
[ "$(basename "$real")" = "$(basename "$path")" ] || exit 13
exec 3< "$real" || exit 12
[ "$(realpath /proc/self/fd/3)" = "$real" ] || exit 13
[ -f /proc/self/fd/3 ] || exit 12
# BO2/BN3: a hard link to secrets.yaml has the same name elsewhere; masking is
# decided by name, so a link count other than one is refused (exit 21).
[ "$(stat -L -c %h /proc/self/fd/3)" = 1 ] || exit 21
head -c __MAX__ <&3
""".replace("__MAX__", str(MAX_CONTENT + 1))

# The backup's hash, for the pending record a timed-out restore leaves behind
# (nit): the backup is not a config path, so it needs its own tiny script.
HASH_SCRIPT = r"""
set -eu
f=$1
case "$f" in /*) ;; *) exit 19 ;; esac
[ -f "$f" ] || exit 12
[ ! -L "$f" ] || exit 13
[ "$(stat -L -c %h "$f")" = 1 ] || exit 21
case "$f" in *.eshu-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].bak) ;;
  *) exit 19 ;;
esac
sha256sum "$f" | cut -d" " -f1
"""

WRITE_SCRIPT = r"""# WRITE_SCRIPT — one sh -c body. Arguments: $1 path, $2 expect, $3 backup,
# $4 tmp. Exit codes: the table above.
set -eu
set -f
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
me=$$
umask_for() {
  m=$(stat -L -c %a "$1")
  m=${m#"${m%???}"}
  umask "$(printf "%o" $(( 0777 & ~0$m )))"
}
fd_ok() {
  [ "$(readlink "$1")" = "$2" ] && [ -f "$1" ] && [ "$(stat -L -c %h "$1")" = 1 ]
}
path=$1; expect=$2; backup=$3; tmp=$4

case "$path"   in /*) ;; *) exit 19 ;; esac
case "$backup" in /*) ;; *) exit 19 ;; esac
case "$tmp"    in /*) ;; *) exit 19 ;; esac
case "$path" in *.yaml|*.yml) ;; *) exit 19 ;; esac
[ "$(basename "$path")" != secrets.yaml ] || exit 19

token_file=/run/s6/container_environment/SUPERVISOR_TOKEN
[ -r "$token_file" ] || exit 20
SUPERVISOR_TOKEN=$(cat "$token_file") || exit 20

real=$(realpath "$path") || exit 12
[ -f "$real" ] || exit 12
dir=$(dirname "$real")
[ -d "$dir" ] || exit 11
case "$dir" in
  /homeassistant|/homeassistant/packages|/homeassistant/packages/*) ;;
  *) exit 11 ;;
esac
[ "$(realpath "$(dirname "$path")")" = "$dir" ] || exit 13
[ "$(basename "$real")" = "$(basename "$path")" ] || exit 13
[ "$(basename "$real")" != secrets.yaml ] || exit 19

case "$backup" in
  "$real".eshu-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].bak) ;;
  *) exit 19 ;;
esac
case "$tmp" in "$real".eshu-tmp-*) ;; *) exit 19 ;; esac
[ "$(dirname "$backup")" = "$dir" ] || exit 19
[ "$(dirname "$tmp")" = "$dir" ] || exit 19
[ ! -e "$backup" ] && [ ! -L "$backup" ] || exit 19
[ ! -e "$tmp" ] && [ ! -L "$tmp" ] || exit 19

exec 3< "$real"
fd3=/proc/$me/fd/3
[ "$(readlink "$fd3")" = "$real" ] || exit 13
[ -f "$fd3" ] || exit 12
[ "$(stat -L -c %h "$fd3")" = 1 ] || exit 21

[ "$(sha256sum "$fd3" | cut -d" " -f1)" = "$expect" ] || exit 14

# AL1: the backup is created and qualified BEFORE it is written through.
umask_for "$fd3"
set -C
exec 4> "$backup"
set +C
fd4=/proc/$me/fd/4
fd_ok "$fd4" "$backup" || { exec 4>&-; rm -f "$backup"; exit 15; }
cat "$fd3" >&4 || { rm -f "$backup"; exit 15; }
# AL6: chown the inode the descriptor holds, not whatever the name points at.
chown "$(stat -L -c %u:%g "$fd3")" "$fd4" 2>/dev/null || true

set -C
exec 5> "$tmp"
set +C
fd5=/proc/$me/fd/5
fd_ok "$fd5" "$tmp" || { exec 5>&-; rm -f "$tmp"; exit 16; }
cat >&5 || { exec 5>&-; rm -f "$tmp"; exit 16; }
chown "$(stat -L -c %u:%g "$fd3")" "$fd5" 2>/dev/null || true
grep -qE "!include[[:space:]]+.*secrets\.yaml" "$fd5" \
  && { exec 5>&-; rm -f "$tmp" "$backup"; exit 22; }

mv -f "$tmp" "$real" || exit 17
[ "$(readlink "$fd5")" = "$real" ] || exit 17

redact() {
  set -f
  WITHHELD='[ESHU check output withheld: redaction unavailable]'
  # AL2: a symlinked secrets.yaml counts too — HA follows it, so a file may be
  # one. The list is every candidate; a find that failed means the list is not
  # trustworthy, and so does a candidate we cannot read.
  rc=0
  list=$(find /homeassistant -xdev -name secrets.yaml \( -type f -o -type l \) 2>/dev/null) || rc=$?
  if [ "$rc" -ne 0 ]; then printf "%s\n" "$WITHHELD"; return 0; fi
  # AQ1: the bytes are read in the subshell that checked the descriptor, through
  # that descriptor — awk never opens a name again, so a swap after the check
  # cannot redirect the read. The combined text is capped at 64 KiB and passed
  # through the environment, never argv. Any failure withholds.
  text=''
  bytes=0
  oldifs=$IFS
  IFS='
'
  for f in $list; do
    [ -n "$f" ] || continue
    # The name test is only here to avoid a blocking open() on a FIFO; what
    # qualifies the file is the descriptor check and the read below.
    if [ ! -f "$f" ]; then printf "%s\n" "$WITHHELD"; return 0; fi
    # A subshell, because a failed exec redirection ends a non-interactive
    # shell (dash 2, ash 1) — and the read uses fd 9, not the name.
    if ! body=$( ( exec 9< "$f" 2>/dev/null || exit 1
                   [ -f /proc/self/fd/9 ] || exit 1
                   cat /proc/self/fd/9 ) ); then
      printf "%s\n" "$WITHHELD"
      return 0
    fi
    n=$(printf "%s" "$body" | wc -c) || { printf "%s\n" "$WITHHELD"; return 0; }
    bytes=$((bytes + n))
    if [ "$bytes" -gt 65536 ]; then printf "%s\n" "$WITHHELD"; return 0; fi
    text="$text$body
"
  done
  IFS=$oldifs
  in=$(cat)
  # AL3/AQ1: one awk pass collects every value from the text the subshells read,
  # sorts longest first, then replaces. Values never leave awk and never touch
  # argv. awk opens no file now, so there is no getline whose return code could
  # be mis-read.
  printf "%s\n" "$in" | ESHU_SECRETS_TEXT="$text" awk -v withheld="$WITHHELD" '
    function hx(c) { return index("0123456789abcdef", tolower(c)) - 1 }
    function hex2(ss) { return hx(substr(ss, 1, 1)) * 16 + hx(substr(ss, 2, 1)) }
    function decode(v,   out, i, c, d) {
      out = ""
      for (i = 1; i <= length(v); i++) {
        c = substr(v, i, 1)
        if (c != "\\") { out = out c; continue }
        d = substr(v, i + 1, 1); i++
        if (d == "n") out = out "\n"
        else if (d == "t") out = out "\t"
        else if (d == "/") out = out "/"
        else if (d == "x" && substr(v, i + 1, 2) ~ /^[0-9A-Fa-f][0-9A-Fa-f]$/) {
          out = out sprintf("%c", hex2(substr(v, i + 1, 2))); i += 2
        } else out = out d
      }
      return out
    }
    function add1(v) {
      sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v)
      if (length(v) >= 4 && !(v in seen)) { seen[v] = 1; s[++n] = v }
    }
    function add(v,   q, i, c, out, closed) {
      sub(/^[ \t]+/, "", v)
      if (v == "") return
      q = substr(v, 1, 1)
      if (q == "\"" || q == "\047") {
        out = ""; closed = 0
        for (i = 2; i <= length(v); i++) {
          c = substr(v, i, 1)
          if (q == "\"") {
            if (c == "\\") { out = out c substr(v, i + 1, 1); i++; continue }
            if (c == "\"") { closed = 1; break }
          } else {
            if (c == "\047") {
              if (substr(v, i + 1, 1) == "\047") { out = out "\047"; i++; continue }
              closed = 1; break
            }
          }
          out = out c
        }
        add1(v)
        add1(out)
        if (closed) add1(decode(out))
        return
      }
      add1(v)
      # Nit: a comment may follow a space or a tab; strip at the first one.
      p = index(v, " #"); q = index(v, "\t#")
      if (q > 0 && (p == 0 || q < p)) p = q
      if (p > 0) add1(substr(v, 1, p - 1))
    }
    BEGIN {
      nf = split(ENVIRON["ESHU_SECRETS_TEXT"], L, "\n")
      inblock = 0
      for (fi = 1; fi <= nf; fi++) {
        l = L[fi]
        if (l ~ /^[^ \t#][^:]*:[ \t]*[|>][-+]?[ \t]*$/) { inblock = 1; continue }
        if (inblock) {
          if (l ~ /^[ \t]+[^ \t]/) { add(l); continue }
          inblock = 0
        }
        if (match(l, /^[^ \t#][^:]*:[ \t]/)) add(substr(l, RLENGTH + 1))
      }
      for (i = 1; i <= n; i++)
        for (j = i + 1; j <= n; j++)
          if (length(s[j]) > length(s[i])) { t = s[i]; s[i] = s[j]; s[j] = t }
    }
    { for (i = 1; i <= n; i++)
        while ((p = index($0, s[i])) > 0)
          $0 = substr($0, 1, p - 1) "***" substr($0, p + length(s[i]))
      print }'
}

crc=0
out=$(SUPERVISOR_TOKEN="$SUPERVISOR_TOKEN" timeout 100 ha core check 2>&1) || crc=$?
printf "%s\n" "$out" | redact | head -c 65536
if [ "$crc" -ne 0 ]; then
  rb="$real.eshu-tmp-rb-$me"
  [ ! -e "$rb" ] && [ ! -L "$rb" ] || exit 1
  umask_for "$fd4"
  set -C
  exec 6> "$rb"
  set +C
  fd6=/proc/$me/fd/6
  # AL1: the rollback file is qualified before it is written, and the mv is
  # confirmed to have installed the inode the descriptor holds.
  fd_ok "$fd6" "$rb" || { exec 6>&-; rm -f "$rb"; exit 1; }
  cat "$fd4" >&6 || { rm -f "$rb"; exit 1; }
  chown "$(stat -L -c %u:%g "$fd4")" "$fd6" 2>/dev/null || true
  mv -f "$rb" "$real" || exit 1
  [ "$(readlink "$fd6")" = "$real" ] || exit 1
  exit 18
fi
printf "\n"
printf "ok %s\n" "$(sha256sum "$fd5" | cut -d" " -f1)"
"""

RESTORE_SCRIPT = r"""# RESTORE_SCRIPT — $1 path, $2 backup, $3 tmp, $4 the backup's expected
# hash (AL5).
set -eu
set -f
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
me=$$
umask_for() {
  m=$(stat -L -c %a "$1")
  m=${m#"${m%???}"}
  umask "$(printf "%o" $(( 0777 & ~0$m )))"
}
fd_ok() {
  [ "$(readlink "$1")" = "$2" ] && [ -f "$1" ] && [ "$(stat -L -c %h "$1")" = 1 ]
}
path=$1; backup=$2; tmp=$3; expect=$4

case "$path"   in /*) ;; *) exit 19 ;; esac
case "$backup" in /*) ;; *) exit 19 ;; esac
case "$tmp"    in /*) ;; *) exit 19 ;; esac
case "$path" in *.yaml|*.yml) ;; *) exit 19 ;; esac
[ "$(basename "$path")" != secrets.yaml ] || exit 19

token_file=/run/s6/container_environment/SUPERVISOR_TOKEN
[ -r "$token_file" ] || exit 20
SUPERVISOR_TOKEN=$(cat "$token_file") || exit 20

real=$(realpath "$path") || exit 12
[ -f "$real" ] || exit 12
dir=$(dirname "$real")
[ -d "$dir" ] || exit 11
case "$dir" in
  /homeassistant|/homeassistant/packages|/homeassistant/packages/*) ;;
  *) exit 11 ;;
esac
[ "$(realpath "$(dirname "$path")")" = "$dir" ] || exit 13
[ "$(basename "$real")" = "$(basename "$path")" ] || exit 13
[ "$(basename "$real")" != secrets.yaml ] || exit 19

case "$backup" in
  "$real".eshu-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].bak) ;;
  *) exit 19 ;;
esac
case "$tmp" in "$real".eshu-tmp-*) ;; *) exit 19 ;; esac
[ "$(dirname "$backup")" = "$dir" ] || exit 19
[ "$(dirname "$tmp")" = "$dir" ] || exit 19
[ -f "$backup" ] && [ ! -L "$backup" ] || exit 19
[ ! -e "$tmp" ] && [ ! -L "$tmp" ] || exit 19

exec 3< "$real"
fd3=/proc/$me/fd/3
[ "$(readlink "$fd3")" = "$real" ] || exit 13
[ -f "$fd3" ] || exit 12
[ "$(stat -L -c %h "$fd3")" = 1 ] || exit 21

exec 4< "$backup"
fd4=/proc/$me/fd/4
[ "$(readlink "$fd4")" = "$backup" ] || exit 19
[ -f "$fd4" ] || exit 19
[ "$(stat -L -c %h "$fd4")" = 1 ] || exit 21
want=$(sha256sum "$fd4" | cut -d" " -f1)
# AL5: the backup is checked against the hash the dashboard recorded when it
# made it, before it is installed. Otherwise a changed backup is live before
# the dashboard compares hashes.
[ "$want" = "$expect" ] || exit 14

umask_for "$fd3"
set -C
exec 5> "$tmp"
set +C
fd5=/proc/$me/fd/5
fd_ok "$fd5" "$tmp" || { exec 5>&-; rm -f "$tmp"; exit 15; }
cat "$fd3" >&5 || { exec 5>&-; rm -f "$tmp"; exit 15; }
chown "$(stat -L -c %u:%g "$fd3")" "$fd5" 2>/dev/null || true

rb="$real.eshu-tmp-rb-$me"
[ ! -e "$rb" ] && [ ! -L "$rb" ] || exit 19
umask_for "$fd4"
set -C
exec 6> "$rb"
set +C
fd6=/proc/$me/fd/6
fd_ok "$fd6" "$rb" || { exec 6>&-; rm -f "$rb"; exit 16; }
cat "$fd4" >&6 || { exec 6>&-; rm -f "$rb"; exit 16; }
chown "$(stat -L -c %u:%g "$fd4")" "$fd6" 2>/dev/null || true
mv -f "$rb" "$real" || exit 16
[ "$(readlink "$fd6")" = "$real" ] || exit 1

redact() {
  set -f
  WITHHELD='[ESHU check output withheld: redaction unavailable]'
  # AL2: a symlinked secrets.yaml counts too — HA follows it, so a file may be
  # one. The list is every candidate; a find that failed means the list is not
  # trustworthy, and so does a candidate we cannot read.
  rc=0
  list=$(find /homeassistant -xdev -name secrets.yaml \( -type f -o -type l \) 2>/dev/null) || rc=$?
  if [ "$rc" -ne 0 ]; then printf "%s\n" "$WITHHELD"; return 0; fi
  # AQ1: the bytes are read in the subshell that checked the descriptor, through
  # that descriptor — awk never opens a name again, so a swap after the check
  # cannot redirect the read. The combined text is capped at 64 KiB and passed
  # through the environment, never argv. Any failure withholds.
  text=''
  bytes=0
  oldifs=$IFS
  IFS='
'
  for f in $list; do
    [ -n "$f" ] || continue
    # The name test is only here to avoid a blocking open() on a FIFO; what
    # qualifies the file is the descriptor check and the read below.
    if [ ! -f "$f" ]; then printf "%s\n" "$WITHHELD"; return 0; fi
    # A subshell, because a failed exec redirection ends a non-interactive
    # shell (dash 2, ash 1) — and the read uses fd 9, not the name.
    if ! body=$( ( exec 9< "$f" 2>/dev/null || exit 1
                   [ -f /proc/self/fd/9 ] || exit 1
                   cat /proc/self/fd/9 ) ); then
      printf "%s\n" "$WITHHELD"
      return 0
    fi
    n=$(printf "%s" "$body" | wc -c) || { printf "%s\n" "$WITHHELD"; return 0; }
    bytes=$((bytes + n))
    if [ "$bytes" -gt 65536 ]; then printf "%s\n" "$WITHHELD"; return 0; fi
    text="$text$body
"
  done
  IFS=$oldifs
  in=$(cat)
  # AL3/AQ1: one awk pass collects every value from the text the subshells read,
  # sorts longest first, then replaces. Values never leave awk and never touch
  # argv. awk opens no file now, so there is no getline whose return code could
  # be mis-read.
  printf "%s\n" "$in" | ESHU_SECRETS_TEXT="$text" awk -v withheld="$WITHHELD" '
    function hx(c) { return index("0123456789abcdef", tolower(c)) - 1 }
    function hex2(ss) { return hx(substr(ss, 1, 1)) * 16 + hx(substr(ss, 2, 1)) }
    function decode(v,   out, i, c, d) {
      out = ""
      for (i = 1; i <= length(v); i++) {
        c = substr(v, i, 1)
        if (c != "\\") { out = out c; continue }
        d = substr(v, i + 1, 1); i++
        if (d == "n") out = out "\n"
        else if (d == "t") out = out "\t"
        else if (d == "/") out = out "/"
        else if (d == "x" && substr(v, i + 1, 2) ~ /^[0-9A-Fa-f][0-9A-Fa-f]$/) {
          out = out sprintf("%c", hex2(substr(v, i + 1, 2))); i += 2
        } else out = out d
      }
      return out
    }
    function add1(v) {
      sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v)
      if (length(v) >= 4 && !(v in seen)) { seen[v] = 1; s[++n] = v }
    }
    function add(v,   q, i, c, out, closed) {
      sub(/^[ \t]+/, "", v)
      if (v == "") return
      q = substr(v, 1, 1)
      if (q == "\"" || q == "\047") {
        out = ""; closed = 0
        for (i = 2; i <= length(v); i++) {
          c = substr(v, i, 1)
          if (q == "\"") {
            if (c == "\\") { out = out c substr(v, i + 1, 1); i++; continue }
            if (c == "\"") { closed = 1; break }
          } else {
            if (c == "\047") {
              if (substr(v, i + 1, 1) == "\047") { out = out "\047"; i++; continue }
              closed = 1; break
            }
          }
          out = out c
        }
        add1(v)
        add1(out)
        if (closed) add1(decode(out))
        return
      }
      add1(v)
      # Nit: a comment may follow a space or a tab; strip at the first one.
      p = index(v, " #"); q = index(v, "\t#")
      if (q > 0 && (p == 0 || q < p)) p = q
      if (p > 0) add1(substr(v, 1, p - 1))
    }
    BEGIN {
      nf = split(ENVIRON["ESHU_SECRETS_TEXT"], L, "\n")
      inblock = 0
      for (fi = 1; fi <= nf; fi++) {
        l = L[fi]
        if (l ~ /^[^ \t#][^:]*:[ \t]*[|>][-+]?[ \t]*$/) { inblock = 1; continue }
        if (inblock) {
          if (l ~ /^[ \t]+[^ \t]/) { add(l); continue }
          inblock = 0
        }
        if (match(l, /^[^ \t#][^:]*:[ \t]/)) add(substr(l, RLENGTH + 1))
      }
      for (i = 1; i <= n; i++)
        for (j = i + 1; j <= n; j++)
          if (length(s[j]) > length(s[i])) { t = s[i]; s[i] = s[j]; s[j] = t }
    }
    { for (i = 1; i <= n; i++)
        while ((p = index($0, s[i])) > 0)
          $0 = substr($0, 1, p - 1) "***" substr($0, p + length(s[i]))
      print }'
}

crc=0
out=$(SUPERVISOR_TOKEN="$SUPERVISOR_TOKEN" timeout 100 ha core check 2>&1) || crc=$?
printf "%s\n" "$out" | redact | head -c 65536
if [ "$crc" -ne 0 ]; then
  mv -f "$tmp" "$real" || exit 1
  [ "$(readlink "$fd5")" = "$real" ] || exit 1
  exit 18
fi
got=$(sha256sum "$fd6" | cut -d" " -f1)
[ "$got" = "$want" ] || exit 1
rm -f "$tmp"
printf "\n"
printf "ok %s\n" "$got"
"""

CHECK_SCRIPT = r"""# CHECK_SCRIPT — ha_config_check: no file argument; the whole tree is
# checked.
set -eu
set -f
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
me=$$
[ -d /homeassistant ] || exit 11
[ ! -L /homeassistant ] || exit 13
[ "$(realpath /homeassistant)" = /homeassistant ] || exit 13

token_file=/run/s6/container_environment/SUPERVISOR_TOKEN
[ -r "$token_file" ] || exit 20
SUPERVISOR_TOKEN=$(cat "$token_file") || exit 20


redact() {
  set -f
  WITHHELD='[ESHU check output withheld: redaction unavailable]'
  # AL2: a symlinked secrets.yaml counts too — HA follows it, so a file may be
  # one. The list is every candidate; a find that failed means the list is not
  # trustworthy, and so does a candidate we cannot read.
  rc=0
  list=$(find /homeassistant -xdev -name secrets.yaml \( -type f -o -type l \) 2>/dev/null) || rc=$?
  if [ "$rc" -ne 0 ]; then printf "%s\n" "$WITHHELD"; return 0; fi
  # AQ1: the bytes are read in the subshell that checked the descriptor, through
  # that descriptor — awk never opens a name again, so a swap after the check
  # cannot redirect the read. The combined text is capped at 64 KiB and passed
  # through the environment, never argv. Any failure withholds.
  text=''
  bytes=0
  oldifs=$IFS
  IFS='
'
  for f in $list; do
    [ -n "$f" ] || continue
    # The name test is only here to avoid a blocking open() on a FIFO; what
    # qualifies the file is the descriptor check and the read below.
    if [ ! -f "$f" ]; then printf "%s\n" "$WITHHELD"; return 0; fi
    # A subshell, because a failed exec redirection ends a non-interactive
    # shell (dash 2, ash 1) — and the read uses fd 9, not the name.
    if ! body=$( ( exec 9< "$f" 2>/dev/null || exit 1
                   [ -f /proc/self/fd/9 ] || exit 1
                   cat /proc/self/fd/9 ) ); then
      printf "%s\n" "$WITHHELD"
      return 0
    fi
    n=$(printf "%s" "$body" | wc -c) || { printf "%s\n" "$WITHHELD"; return 0; }
    bytes=$((bytes + n))
    if [ "$bytes" -gt 65536 ]; then printf "%s\n" "$WITHHELD"; return 0; fi
    text="$text$body
"
  done
  IFS=$oldifs
  in=$(cat)
  # AL3/AQ1: one awk pass collects every value from the text the subshells read,
  # sorts longest first, then replaces. Values never leave awk and never touch
  # argv. awk opens no file now, so there is no getline whose return code could
  # be mis-read.
  printf "%s\n" "$in" | ESHU_SECRETS_TEXT="$text" awk -v withheld="$WITHHELD" '
    function hx(c) { return index("0123456789abcdef", tolower(c)) - 1 }
    function hex2(ss) { return hx(substr(ss, 1, 1)) * 16 + hx(substr(ss, 2, 1)) }
    function decode(v,   out, i, c, d) {
      out = ""
      for (i = 1; i <= length(v); i++) {
        c = substr(v, i, 1)
        if (c != "\\") { out = out c; continue }
        d = substr(v, i + 1, 1); i++
        if (d == "n") out = out "\n"
        else if (d == "t") out = out "\t"
        else if (d == "/") out = out "/"
        else if (d == "x" && substr(v, i + 1, 2) ~ /^[0-9A-Fa-f][0-9A-Fa-f]$/) {
          out = out sprintf("%c", hex2(substr(v, i + 1, 2))); i += 2
        } else out = out d
      }
      return out
    }
    function add1(v) {
      sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v)
      if (length(v) >= 4 && !(v in seen)) { seen[v] = 1; s[++n] = v }
    }
    function add(v,   q, i, c, out, closed) {
      sub(/^[ \t]+/, "", v)
      if (v == "") return
      q = substr(v, 1, 1)
      if (q == "\"" || q == "\047") {
        out = ""; closed = 0
        for (i = 2; i <= length(v); i++) {
          c = substr(v, i, 1)
          if (q == "\"") {
            if (c == "\\") { out = out c substr(v, i + 1, 1); i++; continue }
            if (c == "\"") { closed = 1; break }
          } else {
            if (c == "\047") {
              if (substr(v, i + 1, 1) == "\047") { out = out "\047"; i++; continue }
              closed = 1; break
            }
          }
          out = out c
        }
        add1(v)
        add1(out)
        if (closed) add1(decode(out))
        return
      }
      add1(v)
      # Nit: a comment may follow a space or a tab; strip at the first one.
      p = index(v, " #"); q = index(v, "\t#")
      if (q > 0 && (p == 0 || q < p)) p = q
      if (p > 0) add1(substr(v, 1, p - 1))
    }
    BEGIN {
      nf = split(ENVIRON["ESHU_SECRETS_TEXT"], L, "\n")
      inblock = 0
      for (fi = 1; fi <= nf; fi++) {
        l = L[fi]
        if (l ~ /^[^ \t#][^:]*:[ \t]*[|>][-+]?[ \t]*$/) { inblock = 1; continue }
        if (inblock) {
          if (l ~ /^[ \t]+[^ \t]/) { add(l); continue }
          inblock = 0
        }
        if (match(l, /^[^ \t#][^:]*:[ \t]/)) add(substr(l, RLENGTH + 1))
      }
      for (i = 1; i <= n; i++)
        for (j = i + 1; j <= n; j++)
          if (length(s[j]) > length(s[i])) { t = s[i]; s[i] = s[j]; s[j] = t }
    }
    { for (i = 1; i <= n; i++)
        while ((p = index($0, s[i])) > 0)
          $0 = substr($0, 1, p - 1) "***" substr($0, p + length(s[i]))
      print }'
}

crc=0
out=$(SUPERVISOR_TOKEN="$SUPERVISOR_TOKEN" timeout 100 ha core check 2>&1) || crc=$?
printf "%s\n" "$out" | redact | head -c 65536
[ "$crc" -eq 0 ] || exit 18
printf "\n"
printf "ok check\n"
"""


class HaConfigError(ValueError):
    """A refused or failed HA config call with a stable code."""

    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _now():
    return int(time.time())


# ── the integration record ─────────────────────────────────────────────

def target(integration):
    """`{'ip', 'port', 'user'}` for the add-on, from base_url + config_json."""
    cfg = get_integration_config(integration)
    base = str((integration or {}).get('base_url') or '').strip()
    host, _, port_text = base.rpartition(':')
    if host and port_text.isdigit():
        port = int(port_text)
    else:
        host, port = base, 22
    host = host.strip('[]')
    if not HOST_RE.match(host or ''):
        raise HaConfigError('config_integration_invalid',
                            'the integration base_url must be host or host:port')
    try:
        port = int(cfg.get('ssh_port') or port or 22)
    except (TypeError, ValueError):
        raise HaConfigError('config_integration_invalid', 'invalid ssh port')
    if not 1 <= port <= 65535:
        raise HaConfigError('config_integration_invalid', 'invalid ssh port')
    user = str(cfg.get('ssh_user') or 'root')
    if not USER_RE.match(user):
        raise HaConfigError('config_integration_invalid', 'invalid ssh user')
    return {'ip': host, 'port': port, 'user': user}


def _key_material(integration):
    key = str((integration or {}).get('secret') or '')
    if 'PRIVATE KEY' not in key:
        raise HaConfigError('config_key_missing',
                            'the integration has no SSH private key')
    return key if key.endswith('\n') else key + '\n'


def _known_hosts_host(target_):
    if target_.get('port') and int(target_['port']) != 22:
        return '[%s]:%s' % (target_['ip'], target_['port'])
    return target_['ip']


def _pin_state(integration):
    cfg = get_integration_config(integration)
    return str(cfg.get('host_key') or ''), str(cfg.get('host_fingerprint') or '')


def _save_pin(integration, key_part):
    fingerprint = ssh_keys.fingerprint(key_part)
    set_integration_config(integration, {'host_key': key_part,
                                         'host_fingerprint': fingerprint})
    return fingerprint


# ── freeze, lock and the unknown-state record ──────────────────────────

def _frozen():
    from db.gateways import get_trigger_freeze
    return get_trigger_freeze() is not None


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _integration_lock(integration):
    name = (integration or {}).get('name') or ''
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HaConfigError('config_busy',
                            'another write or restore is in flight for this '
                            'integration')
    return lock


def _state_key(integration):
    return 'ha_config_state_' + ((integration or {}).get('name') or '')


def pending_state(integration):
    return get_meta_json(_state_key(integration), {}) or {}


def _set_pending(integration, state):
    set_meta_json(_state_key(integration), state)


def _clear_pending(integration):
    _set_pending(integration, {})


def _check_key(integration):
    return 'ha_check_config_' + ((integration or {}).get('name') or '')


def _invalidate_check(integration):
    """H5: a write makes every earlier config check stale before it starts."""
    set_meta_json(_check_key(integration), {})


def _record_check(integration, valid):
    if valid:
        set_meta_json(_check_key(integration),
                      {'result': 'valid', 'at': _now(), 'source': 'ha_ssh'})


# ── path policy ────────────────────────────────────────────────────────

def _rel_parts(root_path):
    rel = root_path[len(ROOT) + 1:]
    return rel, rel.split('/')


def check_read_path(path):
    """A path the read/list tools may touch, as the add-on sees it."""
    text = to_root_path(path)
    if not text.startswith(ROOT + '/'):
        raise HaConfigError('config_path_refused',
                            'the path must be under /config')
    rel, parts = _rel_parts(text)
    if not rel or any(p in ('', '.', '..') or p.startswith('.') for p in parts):
        raise HaConfigError('config_path_refused',
                            'the path has an empty or dot segment')
    if not rel.endswith(('.yaml', '.yml')):
        raise HaConfigError('config_path_refused', 'only .yaml/.yml are read')
    if '/' in rel and not rel.startswith('packages/'):
        raise HaConfigError('config_path_refused',
                            'only top-level files or packages/ may be read')
    low = rel.lower()
    if any(low.startswith(d) for d in REFUSED_DIRS):
        raise HaConfigError('config_path_refused',
                            'that directory is not config')
    if low.endswith(REFUSED_SUFFIXES):
        raise HaConfigError('config_path_refused', 'that file type is not config')
    return text


def _display_path(root_path):
    """`/homeassistant/x` -> `/config/x` (the form agents and operators use)."""
    if root_path.startswith(ROOT + '/'):
        return '/config' + root_path[len(ROOT):]
    return root_path


def _mask_secrets(content):
    """BN2: every top-level key's value is masked, body included.

    Only a top-level key (column 0, not a comment) and a real comment at
    column 0 survive. Everything else — indented lines, list items, a block
    scalar's body, a folded or multi-line quoted value — belongs to the value
    above it, so it is dropped. The old rule masked only lines containing a
    colon and printed block-scalar lines (a PEM key, a token) in clear.
    """
    out = []
    for raw in str(content or '').splitlines():
        line = raw.rstrip('\r')
        if not line:
            continue
        if line.startswith('#'):
            out.append(line)
            continue
        if line[0] in ' \t-':
            continue                      # body of the key above it
        key, sep, _value = line.partition(':')
        if sep and key:
            out.append(key + ': "***"')
        # A column-0 line with no key is not printable YAML; drop it.
    return '\n'.join(out) + ('\n' if out else '')


# ── the transport ──────────────────────────────────────────────────────

def _record(integration, template, ctx, res, elapsed_ms):
    out = res.get('stdout') or ''
    err = res.get('stderr') or ''
    # BL1: a binary read returns bytes; the audit summary is text.
    if isinstance(out, bytes):
        out = out.decode('utf-8', 'replace')
    if isinstance(err, bytes):
        err = err.decode('utf-8', 'replace')
    tail = scrub_string(out + err)
    tail = tail[-8192:]
    try:
        record_integration_call(
            integration=(integration or {}).get('name') or '',
            tool=template,
            agent=(ctx or {}).get('agent', ''),
            method='SSH',
            path=template,
            status_code=200 if res.get('rc') == 0 else 500,
            latency_ms=elapsed_ms,
            response_summary=tail,
            response_bytes=len(tail.encode('utf-8')),
            truncated=1 if len(tail) >= 8192 else 0,
            outcome='ok' if res.get('rc') == 0 else 'error',
            session_id=(ctx or {}).get('session_id', ''),
            execution_id=(ctx or {}).get('execution_id', ''),
            reason=(ctx or {}).get('reason', ''),
            approval=(ctx or {}).get('approval', ''),
            decided_at=(ctx or {}).get('decided_at', 0),
            decided_by=(ctx or {}).get('decided_by', ''),
            host=target(integration)['ip'])
    except Exception:  # noqa: BLE001 - a record must never break the call
        pass


def run(integration, template, command, ctx=None, stdin=None,
        timeout=TIMEOUT_SHORT, require_pin=False, binary=False):
    """Run one fixed command over SSH; returns {rc, stdout, stderr, timed_out}.

    `binary=True` (BL1) keeps stdout as bytes; F11's read hashes those bytes
    and only decodes them for display.

    TOFU: with no pin, the call runs with `accept-new`, records the host key
    it saw and returns; `require_pin=True` refuses before connecting (writes
    can never establish trust).
    """
    target_ = target(integration)
    key = _key_material(integration)
    pin, _fingerprint = _pin_state(integration)
    if require_pin and not pin:
        raise HaConfigError(
            'ssh_host_unpinned',
            'run ha_config_list once to pin this add-on\'s host key before '
            'writing')
    key_dir = ssh_keys.ensure_key_dir()
    fd, key_path = tempfile.mkstemp(prefix='ha-config-', dir=key_dir)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(key)
    known_hosts = os.path.join(key_dir, 'ha-known-%s' % secrets.token_hex(6))
    if pin:
        hostspec = _known_hosts_host(target_)
        with open(known_hosts, 'w') as fh:
            fh.write('%s %s\n' % (hostspec, pin))
        os.chmod(known_hosts, 0o600)
    else:
        open(known_hosts, 'w').close()
        os.chmod(known_hosts, 0o600)
    started = time.time()
    try:
        res = ssh_client.run_ssh(target_, command, known_hosts, key_path,
                                 timeout, stdin=stdin,
                                 strict_host_key=bool(pin), binary=binary)
        if not pin and res.get('rc') == 0:
            try:
                line = open(known_hosts).read().splitlines()[0]
                fields = line.split()
                if len(fields) >= 3:
                    key_part = ' '.join(fields[-2:])
                    fingerprint = _save_pin(integration, key_part)
                    record_audit_event(
                        'ha_config_pin', target_['ip'],
                        details=('Pinned %s host key %s' % (target_['ip'],
                                                           fingerprint)),
                        actor=(ctx or {}).get('agent', ''))
            except (OSError, IndexError):
                pass
    finally:
        for path in (key_path, known_hosts):
            try:
                os.unlink(path)
            except OSError:
                pass
    _record(integration, template, ctx, res, int((time.time() - started) * 1000))
    err = (res.get('stderr') or '')
    if isinstance(err, bytes):
        err = err.decode('utf-8', 'replace')
    if res.get('rc') != 0:
        if 'Host key verification failed' in err or \
                'REMOTE HOST IDENTIFICATION HAS CHANGED' in err:
            record_audit_event(
                'ha_config_host_key_changed', target_['ip'],
                details=('Refused %s: the pinned host key does not match' %
                         target_['ip']),
                actor=(ctx or {}).get('agent', ''))
            raise HaConfigError('ssh_host_key_changed',
                                'the add-on host key changed since it was pinned')
        if 'Permission denied' in err or 'publickey' in err:
            raise HaConfigError('ssh_auth_failed',
                                'the add-on refused the key (authorized_keys?)')
    return res


def _shell_cmd(script, *args):
    return 'sh -c %s _ %s' % (shlex.quote(script),
                              ' '.join(shlex.quote(str(a)) for a in args))


def _text(value):
    """Decode a binary step's output for a message (BL1)."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', 'replace')
    return value or ''


def _run_checked(integration, template, command, ctx=None, stdin=None,
                 timeout=TIMEOUT_SHORT, require_pin=False, binary=False):
    res = run(integration, template, command, ctx=ctx, stdin=stdin,
              timeout=timeout, require_pin=require_pin, binary=binary)
    if res.get('timed_out'):
        raise HaConfigError('config_timeout', 'the add-on did not answer in time')
    return res


def list_configs(integration, ctx=None):
    """YAML files under /config and /config/packages, with size and mtime."""
    res = _run_checked(integration, 'list', LIST_SCRIPT, ctx=ctx)
    if res.get('rc') != 0:
        raise HaConfigError('config_list_failed',
                            'could not list /config on the add-on')
    out = []
    for line in (res.get('stdout') or '').splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        size, mtime, path = parts
        if not path.startswith(ROOT + '/'):
            continue
        try:
            out.append({'path': _display_path(path), 'size': int(size),
                        'mtime': int(mtime)})
        except ValueError:
            continue
        if len(out) >= MAX_LIST:
            break
    return out


def read_config(integration, path, ctx=None):
    """`{content, sha256, size, secrets_masked}`; secrets.yaml is masked.

    BL1: the hash and the size are the remote file's **raw bytes**. The read
    runs with `binary=True`, so CRLF and invalid UTF-8 are not rewritten on
    the way here; only `content` is decoded, with replacement, for display.
    The write script compares `sha256sum` on the add-on, so this is the same
    number.
    """
    root_path = check_read_path(path)
    res = _run_checked(integration, 'read',
                       _shell_cmd(READ_SCRIPT, root_path), ctx=ctx, binary=True)
    rc = int(res.get('rc') or 0)
    if rc != 0:
        if rc == 12:
            raise HaConfigError('config_not_found',
                                'the file does not exist on the add-on')
        if rc == 21:
            raise HaConfigError('config_path_refused',
                                'the file is a hard link')
        if rc in (11, 13, 19):
            raise HaConfigError(
                'config_path_refused',
                'the path resolves outside the config tree or changed while '
                'it was opened')
        raise HaConfigError('config_read_failed', 'could not read the file')
    raw = res.get('stdout') or b''
    if isinstance(raw, str):        # a stub that has not been taught bytes
        raw = raw.encode('utf-8')
    size = len(raw)
    if size > MAX_CONTENT:
        raise HaConfigError('config_too_large',
                            'the file is over %d bytes' % MAX_CONTENT)
    import hashlib
    sha = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode('utf-8')
        editable = True
    except UnicodeDecodeError:
        # BL1: a file that is not valid UTF-8 would come back with U+FFFD, and
        # a write built from that text would replace bytes the agent never
        # saw. Read it for display, but say it is not editable.
        text = raw.decode('utf-8', errors='replace')
        editable = False
    masked = root_path.endswith('/' + SECRETS)
    pending = pending_state(integration)
    if pending and pending.get('path') == root_path:
        # U1: a read of the path resolves an unknown state.
        if sha in (pending.get('old_sha'), ):
            _clear_pending(integration)
        elif sha == pending.get('new_sha'):
            _clear_pending(integration)
    out = {'path': _display_path(root_path),
           'content': _mask_secrets(text) if masked else text,
           'sha256': sha, 'size': size, 'secrets_masked': masked,
           'editable': editable}
    if not editable:
        out['reason'] = 'config_not_utf8'
    return out


def write_config(integration, path, content, expect_hash, ctx=None,
                 dry_run=False):
    """Validate, then (unless dry_run) one remote script: back up, write,
    check, and restore automatically on a failed check."""
    if _frozen():
        raise HaConfigError('frozen', 'the fleet is frozen; writes are refused')
    if not _pin_state(integration)[0]:
        # The first connection of a new integration must be a read; a write may
        # never be the thing that establishes trust.
        raise HaConfigError(
            'ssh_host_unpinned',
            'run ha_config_list once to pin this add-on\'s host key before '
            'writing')
    info = validate_write(path, content)
    root_path = info['path']
    text = content if isinstance(content, str) else str(content or '')
    if not re.fullmatch(r'[0-9a-f]{64}', str(expect_hash or '')):
        raise HaConfigError('config_hash_required',
                            'pass expect_hash from ha_config_read')
    old = read_config(integration, path, ctx=ctx)
    if not old.get('editable', True):
        raise HaConfigError(
            'config_not_utf8',
            'the file is not valid UTF-8, so a write would replace bytes you '
            'never saw; edit it on the add-on')
    if old['sha256'] != expect_hash:
        # Also in a dry run: the diff the card shows must be against the bytes
        # the write will replace, or the operator approves a stale diff.
        raise HaConfigError('config_hash_changed',
                            'the file changed since it was read; read it again')
    diff = unified_diff(info['path'], old['content'], text)
    if dry_run:
        return {'dry_run': True, 'path': _display_path(root_path),
                'expect_hash': expect_hash, 'sha256': info['sha256'],
                'size': info['size'], 'risky': info['risky'],
                'includes': info['includes'], 'diff': diff,
                'backup': _backup_name(root_path)}
    pending = pending_state(integration)
    if pending:
        raise HaConfigError(
            'config_state_unknown',
            'a previous write is unresolved for this integration (backup %s); '
            'read the path to resolve it' % (pending.get('backup') or '?'))
    backup = _backup_name(root_path)
    tmp = root_path + '.eshu-tmp-' + secrets.token_hex(8)
    _invalidate_check(integration)   # H5: before the connection
    lock = _integration_lock(integration)
    started = time.time()
    try:
        res = run(integration, 'write',
                  _shell_cmd(WRITE_SCRIPT, root_path, expect_hash, backup, tmp),
                  ctx=ctx, stdin=text, timeout=TIMEOUT_LONG, require_pin=True,
                  binary=True)
    finally:
        lock.release()
    if res.get('timed_out'):
        _mark_unknown(integration, root_path, expect_hash, info['sha256'], backup)
        raise HaConfigError(
            'config_state_unknown',
            'the write timed out; the backup is %s — read the path to resolve'
            % backup)
    rc = int(res.get('rc') or 0)
    if rc == 0:
        # BL1: the script's last line is `ok <sha>` for the bytes now on the
        # add-on. If it is not the hash of what we sent, the file is not what
        # the card approved; treat the state as unknown rather than report ok.
        text = _text(res.get('stdout'))
        lines = text.strip().splitlines()
        ok_line = lines[-1] if lines else ''
        if ok_line != 'ok ' + info['sha256']:
            _mark_unknown(integration, root_path, expect_hash,
                          info['sha256'], backup)
            raise HaConfigError(
                'config_state_unknown',
                'the write ended with an unexpected hash (%s); the backup is '
                '%s — read the path to resolve' %
                (scrub_string(ok_line)[:80] or 'empty', backup))
        check = scrub_string('\n'.join(lines[:-1])).strip()
        _record_check(integration, True)
        record_audit_event(
            'ha_config_write', target(integration)['ip'],
            details=('Wrote %s (%s -> %s), backup %s' %
                     (_display_path(root_path), expect_hash[:12],
                      info['sha256'][:12], backup)),
            actor=(ctx or {}).get('agent', ''))
        return {'ok': True, 'path': _display_path(root_path),
                'backup': backup, 'sha256': info['sha256'],
                'check': 'valid', 'check_output': check}
    output = (_text(res.get('stdout')) + _text(res.get('stderr'))).strip()
    if rc == 18:
        record_audit_event(
            'ha_config_rollback', target(integration)['ip'],
            details=('ha core check failed after writing %s; restored %s' %
                     (_display_path(root_path), backup)),
            actor=(ctx or {}).get('agent', ''))
        raise HaConfigError(
            'config_check_failed',
            'ha core check failed; the backup was restored. %s' %
            scrub_string(output)[-4000:])
    if 10 <= rc <= 17 or rc == 19:
        raise _write_error(rc)
    _mark_unknown(integration, root_path, expect_hash, info['sha256'], backup)
    raise HaConfigError(
        'config_state_unknown',
        'the write ended with an unknown status (%s); the backup is %s — '
        'read the path to resolve' % (rc, backup))


def restore_config(integration, path, backup, ctx=None):
    """Put one `.eshu-<ts>.bak` back, check, and undo on a failed check."""
    if _frozen():
        raise HaConfigError('frozen', 'the fleet is frozen; restores are refused')
    root_path = check_write_path(path)
    name = str(backup or '').strip()
    if not name.startswith(root_path + '.eshu-') or not BACKUP_RE.match(name):
        raise HaConfigError('config_backup_refused',
                            'the backup must belong to the same path')
    if not re.fullmatch(r'%s\.eshu-[0-9]{10}\.bak'
                        % re.escape(root_path), name):
        raise HaConfigError('config_backup_refused',
                            'the backup name is not one of this file\'s backups')
    pending = pending_state(integration)
    if pending:
        raise HaConfigError(
            'config_state_unknown',
            'a previous write is unresolved for this integration (backup %s); '
            'read the path to resolve it' % (pending.get('backup') or '?'))
    tmp = root_path + '.eshu-tmp-' + secrets.token_hex(8)
    _invalidate_check(integration)
    # The two contents a timed-out restore could leave: what is there now,
    # and the backup. A read of the path then resolves the unknown state.
    old_sha = read_config(integration, path, ctx=ctx).get('sha256') or ''
    backup_sha = ''
    try:
        hs = _run_checked(integration, 'hash', _shell_cmd(HASH_SCRIPT, name),
                          ctx=ctx)
        if hs.get('rc') == 0:
            backup_sha = (_text(hs.get('stdout')) or '').strip()
    except HaConfigError:
        backup_sha = ''
    if not re.fullmatch(r'[0-9a-f]{64}', backup_sha or ''):
        raise HaConfigError(
            'config_backup_refused',
            'the backup could not be hashed on the add-on; restore refused')
    lock = _integration_lock(integration)
    try:
        res = run(integration, 'restore',
                  _shell_cmd(RESTORE_SCRIPT, root_path, name, tmp, backup_sha),
                  ctx=ctx, timeout=TIMEOUT_LONG, require_pin=True, binary=True)
    finally:
        lock.release()
    if res.get('timed_out'):
        _mark_unknown(integration, root_path, old_sha, backup_sha, name)
        raise HaConfigError(
            'config_state_unknown',
            'the restore timed out; the backup is %s — read the path to '
            'resolve' % name)
    rc = int(res.get('rc') or 0)
    if rc == 0:
        _record_check(integration, True)
        record_audit_event(
            'ha_config_restore', target(integration)['ip'],
            details='Restored %s from %s' % (_display_path(root_path), name),
            actor=(ctx or {}).get('agent', ''))
        return {'ok': True, 'path': _display_path(root_path),
                'backup': name, 'check': 'valid',
                'check_output': scrub_string(_text(res.get('stdout'))).strip()}
    if rc == 18:
        raise HaConfigError(
            'config_check_failed',
            'ha core check failed after the restore; the previous content was '
            'put back. %s' % scrub_string(_text(res.get('stdout')) +
                                          _text(res.get('stderr')))[-4000:])
    if rc == 14:
        raise HaConfigError(
            'config_hash_changed',
            'the backup changed since it was made; restore refused')
    if rc == 20:
        raise HaConfigError(
            'config_ha_token_missing',
            'the add-on did not provide the Supervisor token for ha core check')
    if rc in (12, 19, 21):
        raise HaConfigError('config_backup_refused',
                            'the backup does not exist or is not valid')
    _mark_unknown(integration, root_path, old_sha, backup_sha, name)
    raise HaConfigError(
        'config_state_unknown',
        'the restore ended with an unknown status (%s); the backup is %s' %
        (rc, name))


def check_config(integration, ctx=None):
    """Run `ha core check`; a valid result is recorded for F10's restart.

    BN1: the check runs in a fixed script whose output is redacted against the
    add-on's `secrets.yaml` before it is printed, and capped after redaction.
    `scrub_string` runs again here as the second check.
    """
    res = _run_checked(integration, 'check', _shell_cmd(CHECK_SCRIPT),
                       ctx=ctx, timeout=TIMEOUT_LONG)
    output = scrub_string(_text(res.get('stdout')) +
                          _text(res.get('stderr'))).strip()
    valid = res.get('rc') == 0
    _record_check(integration, valid)
    record_audit_event(
        'ha_config_check', target(integration)['ip'],
        details='ha core check: %s' % ('valid' if valid else 'invalid'),
        actor=(ctx or {}).get('agent', ''))
    return {'result': 'valid' if valid else 'invalid',
            'ok': valid, 'output': output}


def _backup_name(root_path):
    return '%s.eshu-%d.bak' % (root_path, _now())


def _mark_unknown(integration, root_path, old_sha, new_sha, backup):
    _set_pending(integration, {'path': root_path, 'old_sha': old_sha,
                               'new_sha': new_sha, 'backup': backup,
                               'at': _now()})
    record_audit_event(
        'ha_config_state_unknown', target(integration)['ip'],
        details=('Write/restore of %s ended without a known result; backup %s' %
                 (_display_path(root_path), backup)),
        actor='')


def _write_error(rc):
    mapping = {
        10: ('config_path_refused', 'the directory is not the HA config tree'),
        11: ('config_path_refused', 'the file is not under the config tree'),
        12: ('config_not_found', 'the file no longer exists'),
        13: ('config_path_refused', 'the path is a symlink or escapes the tree'),
        14: ('config_hash_changed', 'the file changed since it was read'),
        15: ('config_backup_failed', 'the backup could not be written'),
        16: ('config_write_failed', 'the new content could not be written'),
        17: ('config_write_failed', 'the new content could not be moved in'),
        19: ('config_path_refused', 'the path arguments were refused'),
        20: ('config_ha_token_missing',
             'the add-on did not provide the Supervisor token for ha core check'),
        21: ('config_path_refused', 'the file is a hard link'),
        22: ('config_includes_secrets',
             'the content !includes a secrets file'),
    }
    code, message = mapping.get(rc, ('config_write_failed', 'the write failed'))
    return HaConfigError(code, '%s (remote exit %s)' % (message, rc))
