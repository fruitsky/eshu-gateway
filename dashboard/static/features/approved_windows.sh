# 4.5 APPROVED WINDOW TOKEN CHECK (v15.3 — recurring + prefix + expiry)
# ============================================================
# Reject a window-token command: log it (waiting so the window-rejected ticket
# lands in order), tell the agent why, then exit HARD. A rejected window must
# NOT fall through to JIT — doing so created duplicate window-rejected + JIT
# tickets per attempt (async tickets interleaving with later ones).
_window_reject() {
  local _reason="$1"
  log_window_reject "$cmd" "$_reason"
  wait || true
  echo "[LOCKED] Window rejected: $_reason — use a valid window token, or request JIT approval without a token."
  exit 1
}

if [ -n "${ESHU_WINDOW_TOKEN:-}" ] && [ -f /etc/eshu-windows.txt ] && [ -s /etc/eshu-windows.txt ]; then
  now_ts=$(date +%s)
  # Get UTC day-of-week: 1=Mon..7=Sun, as bitmask 1=Mon..64=Sun
  dow_num=$(date -u +%u)  # 1=Mon..7=Sun
  dow_bit=$(( 1 << (dow_num - 1) ))
  # Get minutes since midnight UTC
  now_mins=$(( 10#$(date -u +%H) * 60 + 10#$(date -u +%M) ))
  WINDOW_TOLERANCE_MIN=2  # ±2 minutes tolerance
  win_token_found=0

  while IFS='|' read -r win_token win_cmd win_start win_end win_dows win_xtime win_xpires win_mtype || [ -n "${win_token:-}" ]; do
    # Token must match
    [ "${win_token:-}" = "$ESHU_WINDOW_TOKEN" ] || continue
    win_token_found=1

    # Check expiry
    if [ "${win_xpires:-0}" != "0" ] && [ "${win_xpires:-0}" != "" ] && [ "$now_ts" -ge "${win_xpires:-0}" ]; then
      logger -t eshu-gateway "WINDOW EXPIRED: token=$ESHU_WINDOW_TOKEN expired at ${win_xpires}"
      _window_reject "window expired"
    fi

    # Check legacy time-window (window_start/window_end) OR recurring schedule
    win_start=${win_start:-0}; win_end=${win_end:-0}
    win_dows=${win_dows:-0}; win_xtime=${win_xtime:-0}; win_mtype=${win_mtype:-exact}

    if [ "$win_start" != "0" ] && [ "$win_end" != "0" ]; then
      # Legacy time-window mode
      if [ "$now_ts" -lt "$win_start" ]; then
        _window_reject "before window start"
      fi
      if [ "$now_ts" -gt "$win_end" ]; then
        _window_reject "window ended"
      fi
    elif [ "$win_xtime" != "0" ]; then
      # Recurring schedule mode
      # Check day-of-week bitmask (0 = every day)
      if [ "$win_dows" != "0" ] && [ $(( win_dows & dow_bit )) -eq 0 ]; then
        _window_reject "wrong day"
      fi
      # Check execution time with tolerance
      time_diff=$(( now_mins - win_xtime ))
      if [ "${time_diff#-}" -gt "$WINDOW_TOLERANCE_MIN" ]; then
        _window_reject "outside time window"
      fi
    fi

    # Command matching
    match_ok=0
    if [ "$win_mtype" = "prefix" ]; then
      # Prefix match: command starts with win_cmd
      case "$cmd" in
        "${win_cmd}"*) match_ok=1 ;;
      esac
    elif [ "$win_mtype" = "exact" ]; then
      # Exact match (also try multi-command JSON array)
      if [ "$cmd" = "$win_cmd" ]; then
        match_ok=1
      elif [ "${win_cmd:0:1}" = "[" ]; then
        # Parse JSON array of commands
        IFS=',' read -ra ARR <<< "$(echo "$win_cmd" | sed 's/^\[//;s/\]$//')"
        for el in "${ARR[@]}"; do
          cleaned=$(echo "$el" | sed 's/^[[:space:]]*"//; s/"[[:space:]]*$//')
          if [ "$cmd" = "$cleaned" ]; then match_ok=1; break; fi
        done
      fi
    fi

    if [ "$match_ok" = "1" ]; then
      # Atomic claim-and-burn: ask the dashboard to increment the execution
      # counter FIRST and gate the run on its answer. The server's counter is
      # authoritative, so a single-use token cannot be replayed inside the
      # ~30s local-cache sync lag (a second use gets 404 once at cap).
      #   200  -> server claimed it (execution_count incremented) -> run
      #   403  -> gateway IP mismatch / not authorized -> fail closed
      #   404  -> token consumed, disabled, expired, or exhausted -> fail closed
      #   network error (000) -> dashboard unreachable -> fail OPEN on the
      #   local cache so windows still work during a dashboard outage.
      WINDOW_CLAIM_HTTP=$(curl -m 3 -s -o /dev/null -w "%{http_code}" \
        -X POST "$DASHBOARD_URL/api/approved-windows/execute/$ESHU_WINDOW_TOKEN" \
        -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" 2>/dev/null || echo "000")
      if [ "$WINDOW_CLAIM_HTTP" = "404" ] || [ "$WINDOW_CLAIM_HTTP" = "403" ]; then
        logger -t eshu-gateway "APPROVED WINDOW REJECTED: token=$ESHU_WINDOW_TOKEN claim=$WINDOW_CLAIM_HTTP"
        _window_reject "window consumed or disabled"
      fi
      logger -t eshu-gateway "APPROVED WINDOW MATCH: token=$ESHU_WINDOW_TOKEN cmd=$cmd mtype=$win_mtype claim=$WINDOW_CLAIM_HTTP"
      run_sanitized bash -c "$cmd"
    else
      _window_reject "command mismatch"
    fi
  done < /etc/eshu-windows.txt
  # Token was presented but no matching line found
  if [ "$win_token_found" = "0" ] && [ -n "$ESHU_WINDOW_TOKEN" ]; then
    _window_reject "unknown token"
  fi
fi

# Token was presented but no windows are configured on this gateway — hard reject
# (no JIT fallback).
if [ -n "${ESHU_WINDOW_TOKEN:-}" ]; then
  _window_reject "no windows configured on this gateway"
fi

# ============================================================
