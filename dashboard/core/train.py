"""TfL train planning — an Eshu-owned, read-only capability.

The agent never sees the 60–80 KB Journey Planner payload. This module resolves
station names, fetches journeys, ranks them, and returns a short deterministic
summary. It is deliberately harness-agnostic: any MCP client can call it.

No API key is required. Data: TfL Unified API (powered by TfL Open Data).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date as _date, datetime, timedelta
from zoneinfo import ZoneInfo

TFL = "https://api.tfl.gov.uk"
LONDON = ZoneInfo("Europe/London")
UA = "eshu-trains/1.0 (Edgewood homelab; non-commercial)"
HTTP_TIMEOUT = 25
STATION_MODES = "national-rail,tube,dlr,elizabeth-line,overground"
DEFAULT_LINES = "southeastern"
MAX_OPTIONS = 5

MODE_LABELS = {
    "national-rail": "rail",
    "tube": "tube",
    "dlr": "DLR",
    "elizabeth-line": "Elizabeth line",
    "overground": "Overground",
    "bus": "bus",
    "walking": "walk",
}


class TrainError(Exception):
    """A failure that is safe to show the agent as a short sentence."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, **self.extra}


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------

def http_json(url: str, timeout: int = HTTP_TIMEOUT):
    """GET JSON. Returns (status, parsed_or_None, error_or_None); never raises.

    A transport failure must be distinguishable from an empty result so callers
    can say "can't get live data" rather than inventing a train. TfL
    occasionally returns a transient 429/5xx; retry once before giving up.
    """
    retry_codes = {429, 500, 502, 503, 504}
    for attempt in range(2):
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                try:
                    return response.status, json.loads(body), None
                except json.JSONDecodeError as exc:
                    return response.status, None, f"bad json: {exc}"
        except urllib.error.HTTPError as exc:
            if attempt == 0 and exc.code in retry_codes:
                time.sleep(0.4)
                continue
            return exc.code, None, f"http {exc.code}"
        except Exception as exc:  # noqa: BLE001 - degrade, never crash the tool
            return 0, None, f"{type(exc).__name__}: {exc}"
    return 0, None, "request failed"


def parse_hhmm(value: str) -> int:
    """'13:30' -> 810 minutes past midnight."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (value or "").strip())
    if not match:
        raise TrainError("bad_time", f"bad time {value!r}; use HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TrainError("bad_time", f"bad time {value!r}; use HH:MM")
    return hour * 60 + minute


def fmt_hhmm(total: int) -> str:
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def hhmm_from_iso(value: str) -> str:
    match = re.search(r"T(\d{2}:\d{2})", value or "")
    return match.group(1) if match else ""


def today_london() -> _date:
    return datetime.now(LONDON).date()


def normalize_date(value) -> _date:
    """Accept an ISO date, 'today'/'tomorrow', or None (today in London)."""
    if value is None or str(value).strip() == "":
        return today_london()
    text = str(value).strip().lower()
    if text == "today":
        return today_london()
    if text == "tomorrow":
        return today_london() + timedelta(days=1)
    try:
        return _date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise TrainError(
            "bad_date", f"bad date {value!r}; use YYYY-MM-DD, today or tomorrow") from exc


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"false", "0", "no", "depart_after", "departing", "leave_after"}:
        return False
    if text in {"true", "1", "yes", "arrive_by", "arriving", "be_there_by"}:
        return True
    return default


def _as_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


# ---------------------------------------------------------------------------
# station resolution
# ---------------------------------------------------------------------------

def _resolve_hub(naptan: str, label: str) -> dict:
    """Turn a TfL interchange hub (HUBCST, HUBLBG, ...) into one rail/tube child.

    Search returns hubs for central London names like 'Cannon Street' and
    'London Bridge'. The journey planner needs a child NaPTAN, so resolve the
    hub rather than making the user guess the internal id.
    """
    status, data, error = http_json(f"{TFL}/StopPoint/{urllib.parse.quote(naptan)}")
    if status != 200 or not isinstance(data, dict):
        raise TrainError(
            "station_lookup_failed",
            f"I couldn't resolve {label!r} to a station right now "
            f"({error or status}).")

    children = data.get("children") or []
    usable = []
    for mode in ("national-rail", "elizabeth-line", "dlr", "overground", "tube"):
        for child in children:
            modes = child.get("modes") or []
            if mode in modes and child.get("id"):
                usable.append({
                    "name": child.get("commonName") or child.get("name") or label,
                    "naptan": child.get("id"),
                    "modes": modes,
                })
        if usable:
            break

    if not usable:
        raise TrainError(
            "station_hub",
            f"{label!r} resolves to a hub ({naptan}) with no usable rail or "
            "tube station. Try the specific station name.")
    if len(usable) > 1:
        raise TrainError(
            "station_ambiguous",
            f"{label!r} has more than one station. Which one do you mean?",
            candidates=usable[:8])
    return usable[0]


def resolve_station(query: str) -> dict:
    """Resolve a free-text station to exactly one NaPTAN.

    Ambiguity is returned as a structured error with candidates, never guessed:
    sending someone to the wrong station is worse than asking one question.
    """
    text = (query or "").strip()
    if not text:
        raise TrainError("missing_station", "Which station do you mean?")
    if re.fullmatch(r"HUB[A-Za-z0-9]+", text):
        return _resolve_hub(text, text)
    if re.fullmatch(r"(?:910|940|490)[A-Za-z0-9]+", text):
        return {"name": text, "naptan": text, "modes": []}

    url = (f"{TFL}/StopPoint/Search/{urllib.parse.quote(text)}"
           f"?modes={STATION_MODES}")
    status, data, error = http_json(url)
    if status != 200 or not isinstance(data, dict):
        raise TrainError(
            "station_lookup_failed",
            f"I couldn't look up {text!r} right now ({error or status}).")

    matches = data.get("matches") or []
    if not matches:
        raise TrainError(
            "station_not_found",
            f"I couldn't find a station called {text!r}. Try the station name, "
            "e.g. 'Petts Wood'.")

    candidates = [
        {
            "name": match.get("name") or match.get("commonName") or text,
            "naptan": match.get("id") or "",
            "modes": match.get("modes") or [],
        }
        for match in matches[:8]
    ]

    # This is a train planner. If the query has rail/hub matches, drop tube-only
    # noise from unrelated places ('Bromley' should not offer Bromley-by-Bow).
    railish = [
        candidate for candidate in candidates
        if candidate["naptan"].startswith("910") or candidate["naptan"].startswith("HUB")
    ]
    if railish:
        candidates = railish

    # Common London names ('Waterloo', 'Victoria') come back with a hub plus
    # out-of-town stations of the same name. Prefer the hub whose name is an
    # exact match over asking a pointless question.
    exact_hubs = [
        candidate for candidate in candidates
        if candidate["naptan"].startswith("HUB")
        and candidate["name"].strip().lower() == text.lower()
    ]
    if len(exact_hubs) == 1:
        return _resolve_hub(exact_hubs[0]["naptan"], exact_hubs[0]["name"])

    if len(candidates) > 1:
        raise TrainError(
            "station_ambiguous",
            f"{text!r} matches more than one station. Which one do you mean?",
            candidates=candidates)

    chosen = candidates[0]
    if str(chosen["naptan"]).startswith("HUB"):
        return _resolve_hub(chosen["naptan"], chosen["name"])
    return chosen


# ---------------------------------------------------------------------------
# journey fetch and parse
# ---------------------------------------------------------------------------

def fetch_journeys(frm: str, to: str, day: _date, time_str: str,
                   arrive_by: bool = True) -> list:
    """Fetch raw journeys. The payload is parsed here and never returned."""
    url = (
        f"{TFL}/Journey/JourneyResults/{urllib.parse.quote(frm)}/to/"
        f"{urllib.parse.quote(to)}"
        f"?date={day.strftime('%Y%m%d')}"
        f"&time={fmt_hhmm(parse_hhmm(time_str)).replace(':', '')}"
        f"&journeyPreference=LeastTime"
    )
    if arrive_by:
        url += "&timeIs=Arriving"

    status, data, error = http_json(url)
    if status != 200 or not isinstance(data, dict):
        raise TrainError(
            "tfl_unavailable",
            f"I can't get live journey data from TfL right now "
            f"({error or status}). Check nationalrail.co.uk before you travel.")
    journeys = data.get("journeys") or []
    if not journeys:
        raise TrainError(
            "no_journeys",
            "TfL returned no journeys for that station pair and time. Check "
            "the station names, or try a slightly different time.")
    return journeys


def _ride_legs(journey: dict) -> list:
    return [
        leg for leg in (journey.get("legs") or [])
        if (leg.get("mode") or {}).get("name") != "walking"
    ]


def _mode_label(rides: list) -> str:
    labels = []
    for leg in rides:
        name = (leg.get("mode") or {}).get("name") or ""
        label = MODE_LABELS.get(name, name.replace("-", " "))
        if label and label not in labels:
            labels.append(label)
    return " + ".join(labels) or "train"


def _changes_phrase(count: int) -> str:
    if count == 0:
        return "direct"
    return f"{count} change" if count == 1 else f"{count} changes"


def candidate_from_journey(journey: dict):
    legs = journey.get("legs") or []
    if not legs:
        return None
    first = legs[0]
    last = legs[-1]
    scheduled_depart = hhmm_from_iso(
        first.get("scheduledDepartureTime") or first.get("departureTime"))
    depart = hhmm_from_iso(
        first.get("departureTime") or first.get("scheduledDepartureTime"))
    scheduled_arrive = hhmm_from_iso(
        last.get("scheduledArrivalTime") or journey.get("arrivalDateTime"))
    arrive = hhmm_from_iso(
        journey.get("arrivalDateTime") or last.get("scheduledArrivalTime"))
    if not depart or not arrive:
        return None
    rides = _ride_legs(journey)
    changes = max(len(rides) - 1, 0)
    duration = journey.get("duration")
    if not duration:
        duration = max(parse_hhmm(arrive) - parse_hhmm(depart), 0)
    disrupted = any(
        leg.get("isDisrupted") or leg.get("disruptions") for leg in legs)
    summary = ""
    if rides:
        summary = ((rides[0].get("instruction") or {}).get("summary")
                   or (rides[0].get("mode") or {}).get("name") or "")
    delay = (parse_hhmm(depart) - parse_hhmm(scheduled_depart)
             if scheduled_depart else 0)
    arrival_delay = (parse_hhmm(arrive) - parse_hhmm(scheduled_arrive)
                     if scheduled_arrive else 0)
    return {
        "depart": depart,
        "arrive": arrive,
        "scheduled_depart": scheduled_depart or depart,
        "scheduled_arrive": scheduled_arrive or arrive,
        "delay_minutes": max(delay, 0),
        "arrival_delay_minutes": max(arrival_delay, 0),
        "depart_min": parse_hhmm(depart),
        "arrive_min": parse_hhmm(arrive),
        "duration": int(duration),
        "changes": changes,
        "mode": _mode_label(rides),
        "summary": summary,
        "disrupted": bool(disrupted),
    }


def rank_candidates(candidates: list, arrive_by: bool, time_min: int,
                    limit: int = 3) -> tuple[list, bool]:
    """Return (options, meets_deadline).

    arrive_by: prefer fewest changes, then shortest duration, then the latest
    departure. depart_after: prefer earliest arrival, then fewest changes.
    """
    limit = _as_int(limit, 3, 1, MAX_OPTIONS)
    deduped = {}
    for candidate in candidates:
        key = (candidate["depart"], candidate["arrive"],
               candidate["changes"], candidate["duration"])
        deduped.setdefault(key, candidate)
    ordered = list(deduped.values())

    if arrive_by:
        meeting = [c for c in ordered if c["arrive_min"] <= time_min]
        if meeting:
            meeting.sort(key=lambda c: (c["changes"], c["duration"], -c["depart_min"]))
            selected = meeting[:limit]
            latest = max(c["depart_min"] for c in meeting)
            for candidate in selected:
                if candidate["depart_min"] == latest and len(meeting) > 1:
                    candidate["note"] = "latest that makes it"
            return selected, True
        ordered.sort(key=lambda c: (c["arrive_min"], c["changes"], c["duration"]))
        selected = ordered[:limit]
        for candidate in selected:
            candidate["note"] = "earliest available"
        return selected, False

    later = [c for c in ordered if c["depart_min"] >= time_min]
    if later:
        later.sort(key=lambda c: (c["arrive_min"], c["changes"], c["duration"]))
        return later[:limit], True
    ordered.sort(key=lambda c: (c["depart_min"], c["arrive_min"]))
    selected = ordered[:limit]
    for candidate in selected:
        candidate["note"] = "last available"
    return selected, False


def format_plan(result: dict) -> str:
    frm = result["origin"]["name"]
    to = result["destination"]["name"]
    date_label = result["date"]
    today = today_london().isoformat()
    suffix = "" if date_label == today else f" on {date_label}"
    if result["mode"] == "arrive_by":
        header = f"{frm} → {to}, arriving by {result['time']}{suffix}:"
    else:
        header = f"{frm} → {to}, leaving after {result['time']}{suffix}:"

    options = result.get("options") or []
    if not options:
        return header + "\nNo options found. Check the station names and time."

    lines = [header]
    if not result.get("meets_deadline", True):
        if result["mode"] == "arrive_by":
            lines.append(
                f"No option arrives by {result['time']}. Earliest available:")
        else:
            lines.append(
                f"No option leaves after {result['time']}. Last available:")

    for index, option in enumerate(options, 1):
        detail = _changes_phrase(option["changes"])
        line = (f"{index}. {option['depart']} → {option['arrive']} "
                f"({option['duration']} min, {option['mode']}, {detail})")
        if option.get("delay_minutes"):
            line += (f" — {option['delay_minutes']} min late "
                     f"(was {option['scheduled_depart']})")
        elif option.get("arrival_delay_minutes"):
            line += f" — arrives {option['arrival_delay_minutes']} min late"
        if option.get("disrupted"):
            line += " ⚠ disruption"
        if option.get("note"):
            line += f" — {option['note']}"
        lines.append(line)
    return "\n".join(lines)


def validate_request_time(day: _date, time_min: int, arrive_by: bool,
                          now: datetime | None = None) -> None:
    """Reject a past date, or an arrive-by time that has already passed today.

    A depart-after time in the past is left alone: "leaving after 13:30" at
    14:00 still has useful later trains.
    """
    current = now or datetime.now(LONDON)
    if day < current.date():
        raise TrainError(
            "date_passed",
            f"{day.isoformat()} has already passed. Which date do you mean?")
    if (arrive_by and day == current.date()
            and time_min < current.hour * 60 + current.minute - 1):
        raise TrainError(
            "time_passed",
            f"{fmt_hhmm(time_min)} has already passed today. Do you mean tomorrow?")


def plan(from_query: str, to_query: str, time_str: str, arrive_by: bool = True,
         date_value=None, limit: int = 3) -> dict:
    """Resolve, fetch, rank and format one journey query."""
    time_min = parse_hhmm(time_str)
    day = normalize_date(date_value)
    frm = resolve_station(from_query)
    to = resolve_station(to_query)

    journeys = fetch_journeys(frm["naptan"], to["naptan"], day,
                              fmt_hhmm(time_min), arrive_by=arrive_by)
    candidates = [c for c in (candidate_from_journey(j) for j in journeys) if c]
    if not candidates:
        raise TrainError(
            "no_usable_journeys",
            "TfL returned journeys but none had usable departure/arrival times.")

    options, meets = rank_candidates(candidates, bool(arrive_by), time_min, limit)
    result = {
        "ok": True,
        "origin": frm,
        "destination": to,
        "date": day.isoformat(),
        "time": fmt_hhmm(time_min),
        "mode": "arrive_by" if arrive_by else "depart_after",
        "meets_deadline": meets,
        "options": options,
    }
    result["text"] = format_plan(result)
    return result


# ---------------------------------------------------------------------------
# line status
# ---------------------------------------------------------------------------

def fetch_line_status(lines: str | list | None = None) -> list:
    if isinstance(lines, list):
        requested = [str(line).strip() for line in lines if str(line).strip()]
    else:
        requested = [part.strip() for part in str(lines or DEFAULT_LINES).split(",")
                     if part.strip()]
    if not requested:
        requested = [DEFAULT_LINES]

    url = f"{TFL}/Line/{urllib.parse.quote(','.join(requested))}/Status"
    status, data, error = http_json(url)
    if status != 200 or not isinstance(data, list):
        raise TrainError(
            "tfl_unavailable",
            f"I can't get line status from TfL right now ({error or status}).")

    out = []
    for line in data:
        statuses = line.get("lineStatuses") or [{}]
        current = statuses[0] or {}
        reason = (current.get("reason") or "").strip()
        out.append({
            "id": line.get("id") or "",
            "name": line.get("name") or line.get("id") or "",
            "severity": current.get("statusSeverity", 0),
            "description": current.get("statusSeverityDescription") or "Unknown",
            "reason": reason,
        })
    if not out:
        raise TrainError("no_status", "TfL returned no line status.")
    return out


def format_status(lines: list) -> str:
    parts = []
    for line in lines:
        text = f"{line['name']}: {line['description']}"
        reason = (line.get("reason") or "").strip()
        links = re.findall(r"https?://\S+", reason)
        reason_text = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", reason)).strip(" .;:-")
        if len(reason_text) > 200:
            reason_text = reason_text[:197] + "..."
        if reason_text:
            text += f" — {reason_text}"
        if links:
            text += f"\nDetail: {links[0]}"
        parts.append(text)
    return "\n".join(parts)


def status(lines: str | list | None = None) -> dict:
    rows = fetch_line_status(lines)
    return {"ok": True, "lines": rows, "text": format_status(rows)}


# ---------------------------------------------------------------------------
# tool handlers (registered in core.tool_handlers.HANDLERS)
# ---------------------------------------------------------------------------

def _handler_error(exc: TrainError) -> str:
    return json.dumps(exc.as_dict(), ensure_ascii=False)


def handle_train_plan(integration, tool, args, ctx=None) -> str:
    args = args or {}
    try:
        origin = args.get("origin") or args.get("from")
        destination = args.get("destination") or args.get("to")
        if not str(origin or "").strip():
            raise TrainError("missing_origin", "Which station are you starting from?")
        if not str(destination or "").strip():
            raise TrainError("missing_destination", "Which station are you going to?")
        if not str(args.get("time") or "").strip():
            raise TrainError(
                "missing_time", "What time do you need to arrive, or leave?")
        arrive_by = _as_bool(args.get("arrive_by"), default=True)
        validate_request_time(
            normalize_date(args.get("date")),
            parse_hhmm(args.get("time")),
            arrive_by,
        )
        result = plan(
            origin,
            destination,
            args.get("time"),
            arrive_by=arrive_by,
            date_value=args.get("date"),
            limit=_as_int(args.get("limit"), 3, 1, MAX_OPTIONS),
        )
        return json.dumps(result, ensure_ascii=False)
    except TrainError as exc:
        return _handler_error(exc)
    except Exception as exc:  # noqa: BLE001 - never leak a traceback
        return json.dumps(
            {"ok": False, "error": "train_failed",
             "message": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False)


def handle_train_status(integration, tool, args, ctx=None) -> str:
    args = args or {}
    try:
        return json.dumps(status(args.get("lines") or DEFAULT_LINES),
                          ensure_ascii=False)
    except TrainError as exc:
        return _handler_error(exc)
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"ok": False, "error": "train_failed",
             "message": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False)
