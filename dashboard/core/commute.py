"""Eshu-owned commute profiles, checks, and notification slots.

Read-only checks for chat; a scheduler-only `due()` path that dedupes slots in
Eshu's own state table. The TfL fetch/parse layer is `core.train`, so there is
one implementation of station resolution and journey parsing.
"""
from __future__ import annotations

import copy
import json
import re
import time
from datetime import date as _date, datetime, timedelta
from zoneinfo import ZoneInfo

from core import train
from db import commute as store
from db.misc import get_meta_json, set_meta_json

LONDON = ZoneInfo("Europe/London")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULT_LINES = ["southeastern"]
BANK_HOLIDAYS_URL = "https://www.gov.uk/bank-holidays.json"
NOTIFY_WINDOW_START = 4 * 60          # 04:00 local
NOTIFY_WINDOW_END = 10 * 60           # 10:00 local
SLOT_GRACE_MINUTES = 1                # scheduler runs every minute
BANK_HOLIDAY_CACHE_KEY = "commute_bank_holidays"
BANK_HOLIDAY_TTL = 86400

PROFILE_FIELDS = {
    "display_name", "from", "to", "days", "arrive_by", "usual_train",
    "never_before", "notify_times", "warn_minutes", "lines", "alerts_only",
    "overrides", "paused_until",
}
TIME_FIELDS = ("arrive_by", "usual_train", "never_before")


class CommuteError(Exception):
    """A validation or lookup failure safe to show the agent as a sentence."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, **self.extra}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def parse_time(value: str) -> int:
    try:
        return train.parse_hhmm(value)
    except train.TrainError as exc:
        raise CommuteError("bad_time", exc.message) from exc


def fmt_time(total: int) -> str:
    return train.fmt_hhmm(total)


def today_london() -> _date:
    return datetime.now(LONDON).date()


def now_london(at: str | None = None) -> datetime:
    current = datetime.now(LONDON)
    if at:
        minutes = parse_time(at)
        current = current.replace(hour=minutes // 60, minute=minutes % 60,
                                  second=0, microsecond=0)
    return current


def parse_date(value, field: str = "date") -> _date:
    try:
        return _date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise CommuteError("bad_date", f"{field}: use YYYY-MM-DD") from exc


def _deep(value):
    return copy.deepcopy(value)


def _as_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _normalise_days(value) -> list:
    if not isinstance(value, list) or not value:
        raise CommuteError("bad_days", "days: needs at least one day")
    if any(not isinstance(day, str) or day not in DAYS for day in value):
        unknown = [day for day in value if day not in DAYS]
        raise CommuteError(
            "bad_days", f"days: unknown day(s) {unknown}; use {DAYS}")
    return [day for day in DAYS if day in value]


def _normalise_station(value, field: str) -> dict:
    if isinstance(value, str):
        try:
            resolved = train.resolve_station(value)
        except train.TrainError as exc:
            raise CommuteError(exc.code, f"{field}: {exc.message}",
                               **exc.extra) from exc
        return {"name": resolved["name"], "naptan": resolved["naptan"]}
    if not isinstance(value, dict):
        raise CommuteError(
            "bad_station",
            f"{field}: give a station name or a {{name, naptan}} object")
    naptan = str(value.get("naptan") or "").strip()
    if not naptan:
        raise CommuteError("bad_station", f"{field}: naptan is required")
    if naptan.startswith("HUB"):
        raise CommuteError(
            "station_hub",
            f"{field}: {naptan} is a hub; use the specific station's NaPTAN")
    name = str(value.get("name") or naptan).strip()
    if not name:
        raise CommuteError("bad_station", f"{field}: name is required")
    return {"name": name, "naptan": naptan}


def _normalise_times(value, usual_minutes: int) -> list:
    if not isinstance(value, list) or not value:
        raise CommuteError("bad_notify_times",
                           "notify_times: give a list of HH:MM times")
    if any(not isinstance(item, str) for item in value):
        raise CommuteError("bad_notify_times",
                           "notify_times: every entry must be HH:MM")
    times = sorted(item.strip() for item in value)
    if len(set(times)) != len(times):
        raise CommuteError("bad_notify_times",
                           "notify_times: duplicate times are not allowed")
    for item in times:
        minutes = parse_time(item)
        if not (NOTIFY_WINDOW_START <= minutes <= NOTIFY_WINDOW_END):
            raise CommuteError(
                "bad_notify_times",
                f"notify_times: {item} is outside the supported 04:00–10:00 window")
        if minutes >= usual_minutes:
            raise CommuteError(
                "bad_notify_times",
                f"notify_times: {item} must be before usual_train")
    return times


def _normalise_overrides(value) -> dict:
    if not isinstance(value, dict):
        raise CommuteError("bad_overrides", "overrides: must be an object")
    out = {}
    for key, entry in value.items():
        day = parse_date(key, "overrides date").isoformat()
        if entry in ("skip", "office"):
            out[day] = entry
            continue
        if not isinstance(entry, dict):
            raise CommuteError(
                "bad_overrides",
                f"overrides[{day}]: use 'skip', 'office' or an object")
        allowed = {"skip", "office", "arrive_by", "usual_train", "notify_times"}
        unknown = set(entry) - allowed
        if unknown:
            raise CommuteError(
                "bad_overrides",
                f"overrides[{day}]: unknown field(s) {sorted(unknown)}")
        clean = {}
        if "skip" in entry:
            clean["skip"] = bool(entry["skip"])
        if "office" in entry:
            clean["office"] = bool(entry["office"])
        for field in ("arrive_by", "usual_train"):
            if field in entry:
                parse_time(str(entry[field]))
                clean[field] = str(entry[field])
        if "notify_times" in entry:
            clean["notify_times"] = _normalise_times(
                entry["notify_times"], parse_time(clean.get("usual_train") or "23:59"))
        if not clean:
            raise CommuteError(
                "bad_overrides", f"overrides[{day}]: empty override")
        out[day] = clean
    return out


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_patch(patch: dict, current: dict) -> dict:
    """Validate and merge a profile patch. Returns the merged profile.

    Every failure is a hard rejection with a readable reason; callers must not
    write on error.
    """
    if not isinstance(patch, dict):
        raise CommuteError("bad_patch", "patch: give a JSON object")
    patch = dict(patch)
    if "notify_at" in patch:
        if "notify_times" not in patch:
            patch["notify_times"] = [patch["notify_at"]]
        patch.pop("notify_at", None)

    unknown = set(patch) - PROFILE_FIELDS
    if unknown:
        raise CommuteError(
            "unknown_field", f"patch: unknown field(s) {sorted(unknown)}")

    merged = _deep(current or {})
    errors = []

    for field in ("from", "to"):
        if field in patch:
            merged[field] = _normalise_station(patch[field], field)

    if "days" in patch:
        merged["days"] = _normalise_days(patch["days"])

    if "notify_times" in patch:
        merged["notify_times"] = patch["notify_times"]

    for field in TIME_FIELDS:
        if field in patch:
            parse_time(str(patch[field]))
            merged[field] = str(patch[field]).strip()

    if "display_name" in patch:
        name = str(patch["display_name"] or "").strip()
        if not name:
            errors.append("display_name: must not be empty")
        else:
            merged["display_name"] = name

    if "warn_minutes" in patch:
        merged["warn_minutes"] = _as_int(patch["warn_minutes"], 3, 0, 120)

    if "lines" in patch:
        lines = patch["lines"]
        if (not isinstance(lines, list) or not lines
                or any(not isinstance(line, str) or not line.strip() for line in lines)):
            errors.append("lines: needs a list of TfL line ids")
        else:
            merged["lines"] = [line.strip() for line in lines]

    if "alerts_only" in patch:
        merged["alerts_only"] = bool(patch["alerts_only"])

    if "overrides" in patch:
        merged["overrides"] = _normalise_overrides(patch["overrides"])

    if "paused_until" in patch:
        value = patch["paused_until"]
        if value is None:
            merged["paused_until"] = None
        else:
            merged["paused_until"] = parse_date(value, "paused_until").isoformat()

    for field in ("from", "to", "days", "arrive_by", "usual_train",
                  "never_before", "notify_times", "warn_minutes", "lines",
                  "alerts_only", "overrides", "paused_until"):
        if field not in merged:
            errors.append(f"{field}: is required")

    if errors:
        raise CommuteError("invalid_profile", "; ".join(errors))

    usual = parse_time(merged["usual_train"])
    arrive = parse_time(merged["arrive_by"])
    never = parse_time(merged["never_before"])
    if "notify_times" in merged:
        merged["notify_times"] = _normalise_times(merged["notify_times"], usual)
    for entry in (merged.get("overrides") or {}).values():
        if isinstance(entry, dict) and entry.get("notify_times"):
            entry["notify_times"] = _normalise_times(entry["notify_times"], usual)
    if never > usual:
        raise CommuteError(
            "invalid_profile", "never_before must not be later than usual_train")
    if arrive <= usual:
        raise CommuteError(
            "invalid_profile", "arrive_by must be after usual_train")
    merged.setdefault("overrides", {})
    merged.setdefault("paused_until", None)
    merged.setdefault("alerts_only", False)
    merged.setdefault("lines", list(DEFAULT_LINES))
    merged.setdefault("display_name", "")
    return merged


def normalize_for_import(raw: dict) -> dict:
    """Turn the old profiles.json shape into the Eshu shape."""
    if not isinstance(raw, dict):
        raise CommuteError("bad_profile", "profile must be an object")
    config = {key: value for key, value in raw.items()
              if key not in ("chat_id", "active")}
    if "notify_times" not in config and config.get("notify_at"):
        config["notify_times"] = [config["notify_at"]]
    config.pop("notify_at", None)
    config.setdefault("never_before", config.get("usual_train"))
    config.setdefault("warn_minutes", 3)
    config.setdefault("lines", list(DEFAULT_LINES))
    config.setdefault("alerts_only", False)
    config.setdefault("overrides", {})
    config.setdefault("paused_until", None)
    return validate_patch(config, {})


# ---------------------------------------------------------------------------
# day gating and data
# ---------------------------------------------------------------------------

def bank_holidays() -> set:
    """England & Wales bank holidays, cached in Eshu's meta table for a day."""
    cache = get_meta_json(BANK_HOLIDAY_CACHE_KEY, {}) or {}
    dates = cache.get("dates") or []
    fetched = cache.get("fetched") or 0
    if dates and time.time() - float(fetched) < BANK_HOLIDAY_TTL:
        return set(dates)

    status, data, _error = train.http_json(BANK_HOLIDAYS_URL)
    if status == 200 and isinstance(data, dict):
        try:
            events = data["england-and-wales"]["events"]
            dates = sorted({event["date"] for event in events})
            set_meta_json(BANK_HOLIDAY_CACHE_KEY,
                          {"fetched": int(time.time()), "dates": dates})
            return set(dates)
        except (KeyError, TypeError):
            pass
    return set(dates)


def day_gate(profile: dict, day: _date, holidays: set) -> tuple[bool, str]:
    paused = profile.get("paused_until")
    if paused:
        try:
            if day <= _date.fromisoformat(str(paused)):
                return False, f"paused until {paused}"
        except ValueError:
            pass

    override = (profile.get("overrides") or {}).get(day.isoformat())
    if override == "skip":
        return False, "override: skip"
    if override == "office":
        return True, ""
    if isinstance(override, dict):
        if override.get("skip"):
            return False, "override: skip"
        if override.get("office"):
            return True, ""

    if day.isoformat() in holidays:
        return False, "bank holiday"

    if DAYS[day.weekday()] not in (profile.get("days") or []):
        return False, f"not a commute day ({DAYS[day.weekday()]})"
    return True, ""


def effective_profile(profile: dict, day: _date) -> dict:
    override = (profile.get("overrides") or {}).get(day.isoformat())
    if not isinstance(override, dict):
        return profile
    patched = dict(profile)
    for field in ("arrive_by", "usual_train", "notify_times"):
        if override.get(field):
            patched[field] = override[field]
    return patched


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def _status_bits(status: dict, line_key: str):
    current = (status or {}).get(line_key) or {}
    description = (current.get("description") or "").strip()
    raw_reason = (current.get("reason") or "").strip()
    links = re.findall(r"https?://\S+", raw_reason)
    reason_text = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", raw_reason)).strip(" .;:-")
    if len(reason_text) > 140:
        reason_text = reason_text[:137] + "..."
    severity = current.get("severity", 10) if current else 10
    return severity, (reason_text or description), (f"— Detail: {links[0]}" if links else "")


def _changes_phrase(count: int) -> str:
    return "direct" if count == 0 else (f"{count} change" if count == 1 else f"{count} changes")


def _find_usual(journeys: list, usual: str):
    for journey in journeys:
        legs = journey.get("legs") or []
        if legs and train.hhmm_from_iso(
                legs[0].get("scheduledDepartureTime") or legs[0].get("departureTime")) == usual:
            return journey
    return None


def _fastest_from(journeys: list, never_before: str):
    floor = parse_time(never_before)
    best = None
    for journey in journeys:
        candidate = train.candidate_from_journey(journey)
        if not candidate or candidate["depart_min"] < floor:
            continue
        if best is None:
            best = candidate
            continue
        if ((candidate["arrive_min"], candidate["changes"], candidate["duration"])
                < (best["arrive_min"], best["changes"], best["duration"])):
            best = candidate
    return best


def _slot_label(profile: dict, slot: str | None) -> str:
    if not slot:
        return ""
    times = profile.get("notify_times") or []
    if len(times) == 1:
        return f"Check ({slot})"
    if times and slot == times[0]:
        return f"Early check ({slot})"
    if times and slot == times[-1]:
        return f"Final check ({slot})"
    return f"Check ({slot})"


def _decorate(text: str, profile: dict, slot: str | None,
              preceding: dict | None) -> str:
    if not text:
        return text
    label = _slot_label(profile, slot)
    if label:
        text = f"{label}\n{text}"
    if preceding and preceding.get("delay_minutes"):
        text += (f"\nThe {preceding.get('scheduled_depart') or preceding['depart']} "
                 f"is running {preceding['delay_minutes']} min late.")
    return text


def classify(profile: dict, journeys: list, status: dict, now: datetime,
             day: _date, slot: str | None = None,
             preceding: dict | None = None) -> dict:
    name = profile.get("display_name") or "Commute"
    frm = profile["from"]["name"]
    to = profile["to"]["name"]
    usual = profile["usual_train"]
    arrive_by = profile["arrive_by"]
    never_before = profile.get("never_before") or usual
    warn_minutes = int(profile.get("warn_minutes", 3))
    lines = profile.get("lines") or DEFAULT_LINES
    line_key = lines[0]

    if not journeys:
        return {"level": "nodata", "text": (
            f"⚠️ {name} — I can't get live train data right now (TfL returned nothing).\n"
            f"I don't want to guess. Check nationalrail.co.uk before you leave."
        )}

    usual_journey = _find_usual(journeys, usual)
    severity, status_bit, status_detail = _status_bits(status, line_key)

    if usual_journey is None:
        if now.strftime("%H:%M") > usual:
            return {"level": "passed", "text": ""}
        best = _fastest_from(journeys, never_before)
        if not best:
            return {"level": "nodata", "text": (
                f"⚠️ {name} — the {usual} isn't showing and nothing later is either.\n"
                f"TfL has no usable plan right now. Check nationalrail.co.uk."
            )}
        late_by = parse_time(arrive_by) - best["arrive_min"]
        text = (
            f"⚠️ Morning {name} — the {usual} is disrupted. You'll be late today.\n"
            + (f"{line_key.title()}: {status_bit}\n" if status_bit else "")
            + f"Fastest from {never_before} onwards: {best['depart']} → {to} "
              f"{best['arrive']} ({late_by} min late), {best['duration']} min, "
              f"{_changes_phrase(best['changes'])}."
            + (f"\n{status_detail}" if status_detail else "")
        )
        return {"level": "late", "text": _decorate(text, profile, slot, preceding)}

    usual_candidate = train.candidate_from_journey(usual_journey)
    if not usual_candidate:
        return {"level": "nodata", "text": (
            f"⚠️ {name} — I can't read the {usual} service from TfL right now."
        )}

    scheduled = usual_candidate["scheduled_depart"]
    actual = usual_candidate["depart"]
    arrival = usual_candidate["arrive"]
    duration = usual_candidate["duration"]
    changes = usual_candidate["changes"]
    delay = max(parse_time(actual) - parse_time(scheduled), 0)

    if parse_time(arrival) > parse_time(arrive_by):
        best = _fastest_from(journeys, never_before)
        late_by = parse_time(arrival) - parse_time(arrive_by)
        alternative = ""
        if best and best["depart"] != actual:
            alternative = (f"\nFastest alternative: {best['depart']} → {to} "
                           f"{best['arrive']} "
                           f"({best['arrive_min'] - parse_time(arrive_by)} min late).")
        if delay > 0:
            lead = f"the {usual} is running {delay} min late. You'll be {late_by} min late."
        else:
            lead = f"the {usual} is on time, but that arrives {late_by} min after your {arrive_by}."
        text = (
            f"⚠️ Morning {name} — {lead}\n"
            + (f"{line_key.title()}: {status_bit}\n" if status_bit else "")
            + f"Expected {to} {arrival}.{alternative}"
            + (f"\n{status_detail}" if status_detail else "")
        )
        return {"level": "late", "text": _decorate(text, profile, slot, preceding)}

    spare = parse_time(arrive_by) - parse_time(arrival)

    if severity < 10:
        text = (
            f"🚆 Morning {name} — {line_key.title()}: {status_bit or 'disruption reported'}\n"
            f"Your {usual} is still showing: {actual or usual} {frm} → {arrival} {to} "
            f"({duration} min, {_changes_phrase(changes)}).\n"
            f"{spare} min spare against your {arrive_by}."
            + (f"\n{status_detail}" if status_detail else "")
        )
        return {"level": "warn", "text": _decorate(text, profile, slot, preceding)}

    if delay > warn_minutes:
        text = (
            f"🚆 Morning {name} — {usual} running ~{delay} min late.\n"
            f"Expected {to} {arrival}. Still inside {arrive_by}, but only {spare} min spare."
            + (f"\n{status_detail}" if status_detail else "")
        )
        return {"level": "warn", "text": _decorate(text, profile, slot, preceding)}

    text = (
        f"🚆 Morning {name} — all good.\n"
        f"{actual or usual} {frm} → {arrival} {to} ({duration} min, "
        f"{_changes_phrase(changes)})."
        + (f" {status_bit}." if status_bit else "")
        + f"\n{spare} min spare against your {arrive_by}."
    )
    return {"level": "normal", "text": _decorate(text, profile, slot, preceding)}


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def _status_map(lines: list) -> dict:
    try:
        rows = train.fetch_line_status(lines)
    except train.TrainError:
        return {}
    return {
        row.get("id", ""): {
            "severity": row.get("severity", 10),
            "description": row.get("description", "Unknown"),
            "reason": row.get("reason", ""),
        }
        for row in rows
    }


def _preceding_candidate(journeys: list, usual: str):
    floor = parse_time(usual) - 30
    best = None
    for journey in journeys:
        candidate = train.candidate_from_journey(journey)
        if not candidate:
            continue
        if floor <= candidate["depart_min"] < parse_time(usual):
            if best is None or candidate["depart_min"] > best["depart_min"]:
                best = candidate
    return best


def check(profile: dict, day: _date, now: datetime, holidays: set,
          slot: str | None = None, include_preceding: bool = False,
          suppress_normal: bool = True) -> dict:
    should, why = day_gate(profile, day, holidays)
    if not should:
        return {"level": "skip", "text": "", "reason": why}

    effective = effective_profile(profile, day)
    usual = effective["usual_train"]
    lookback = 30 if include_preceding else 10
    query_min = max(now.hour * 60 + now.minute, parse_time(usual) - lookback)

    try:
        journeys = train.fetch_journeys(
            effective["from"]["naptan"], effective["to"]["naptan"], day,
            fmt_time(query_min), arrive_by=False)
    except train.TrainError as exc:
        return {"level": "nodata", "text": (
            f"⚠️ {effective.get('display_name') or 'Commute'} — I can't get live "
            f"train data right now ({exc.message}).\n"
            f"I don't want to guess. Check nationalrail.co.uk before you leave."
        )}

    status_map = _status_map(effective.get("lines") or DEFAULT_LINES)
    preceding = _preceding_candidate(journeys, usual) if include_preceding else None
    result = classify(effective, journeys, status_map, now, day, slot, preceding)
    if suppress_normal and effective.get("alerts_only") and result.get("level") == "normal":
        result["text"] = ""
    return result


def chat_check(person: str, at: str | None = None) -> dict:
    profile = store.get_profile(person)
    if not profile:
        raise CommuteError("unknown_person", f"No commute profile for {person!r}.")
    now = now_london(at)
    day = now.date()
    result = check(profile, day, now, bank_holidays(), suppress_normal=False)
    if result["level"] == "skip":
        result["text"] = f"No commute alert today — {result.get('reason', 'not a commute day')}."
    elif result["level"] == "passed":
        effective = effective_profile(profile, day)
        result["text"] = (f"Your {effective.get('usual_train')} has already gone. "
                          "Use the train planner for the next options.")
    return {
        "ok": True,
        "person": person,
        "date": day.isoformat(),
        "level": result["level"],
        "text": result.get("text") or "",
        "send": bool(result.get("text")),
    }


def due(person: str, at: str | None = None, dry_run: bool = False) -> dict:
    """Scheduler path: decide whether a notify slot is due and compose it."""
    profile = store.get_profile(person)
    if not profile:
        raise CommuteError("unknown_person", f"No commute profile for {person!r}.")
    now = now_london(at)
    day = now.date()
    should, why = day_gate(profile, day, bank_holidays())
    if not should:
        return {"ok": True, "person": person, "date": day.isoformat(),
                "send": False, "reason": why}

    effective = effective_profile(profile, day)
    slots = effective.get("notify_times") or []
    now_min = now.hour * 60 + now.minute
    due_slots = []
    for slot in slots:
        try:
            slot_min = parse_time(slot)
        except CommuteError:
            continue
        if 0 <= now_min - slot_min <= SLOT_GRACE_MINUTES:
            if not store.was_sent(person, day.isoformat(), slot):
                due_slots.append(slot)
    if not due_slots:
        return {"ok": True, "person": person, "date": day.isoformat(),
                "send": False, "reason": "not due"}

    slot = due_slots[0]
    result = check(effective, day, now, set(), slot=slot,
                   include_preceding=(len(slots) > 1 and slot == slots[-1]))
    if not result.get("text"):
        return {"ok": True, "person": person, "date": day.isoformat(),
                "send": False, "slot": slot, "reason": "nothing to send"}
    if not dry_run:
        store.mark_sent(person, day.isoformat(), slot)
    return {
        "ok": True,
        "person": person,
        "date": day.isoformat(),
        "slot": slot,
        "level": result["level"],
        "text": result["text"],
        "send": True,
        "dry_run": bool(dry_run),
    }


# ---------------------------------------------------------------------------
# profile reads/writes
# ---------------------------------------------------------------------------

def summary(person: str, profile: dict) -> str:
    days = " ".join(day.title() for day in (profile.get("days") or []))
    times = ", ".join(profile.get("notify_times") or [])
    overrides = profile.get("overrides") or {}
    return (
        f"{profile.get('display_name') or person.title()} ({person})\n"
        f"  {profile['from']['name']} → {profile['to']['name']}\n"
        f"  usual train   {profile['usual_train']}   (never earlier than "
        f"{profile.get('never_before') or profile['usual_train']})\n"
        f"  arrive by     {profile['arrive_by']}   (warn at +"
        f"{profile.get('warn_minutes', 3)} min)\n"
        f"  alerts        {times or '(none)'}\n"
        f"  days          {days or '(none)'}\n"
        f"  overrides     {json.dumps(overrides, ensure_ascii=False)}\n"
        f"  paused_until  {profile.get('paused_until') or '-'}"
    )


def update_profile(person: str, patch: dict, agent: str = '') -> dict:
    current = store.get_profile(person)
    if not current:
        raise CommuteError("unknown_person", f"No commute profile for {person!r}.")
    merged = validate_patch(patch, current)
    saved = store.save_profile(person, merged, agent)
    return {"ok": True, "person": person, "text": summary(person, saved),
            "profile": saved}


def override(person: str, date_value: str, action: str, arrive_by: str | None,
             agent: str = '') -> dict:
    current = store.get_profile(person)
    if not current:
        raise CommuteError("unknown_person", f"No commute profile for {person!r}.")
    day = parse_date(date_value).isoformat()
    if action not in ("skip", "office", "clear"):
        raise CommuteError(
            "bad_action", "action: use 'skip', 'office' or 'clear'")
    overrides = dict(current.get("overrides") or {})
    if action == "clear":
        overrides.pop(day, None)
    else:
        entry = action
        if action == "office" and arrive_by:
            parse_time(arrive_by)
            entry = {"office": True, "arrive_by": arrive_by}
        overrides[day] = entry
    merged = validate_patch({"overrides": overrides}, current)
    saved = store.save_profile(person, merged, agent)
    return {"ok": True, "person": person, "text": summary(person, saved),
            "profile": saved}


def pause(person: str, until: str | None, agent: str = '') -> dict:
    current = store.get_profile(person)
    if not current:
        raise CommuteError("unknown_person", f"No commute profile for {person!r}.")
    if until is None or str(until).strip().lower() in ("", "clear", "none"):
        value = None
    else:
        value = parse_date(until, "until").isoformat()
    merged = validate_patch({"paused_until": value}, current)
    saved = store.save_profile(person, merged, agent)
    return {"ok": True, "person": person, "text": summary(person, saved),
            "profile": saved}


# ---------------------------------------------------------------------------
# tool handlers
# ---------------------------------------------------------------------------

def _person_arg(args: dict) -> str:
    person = str((args or {}).get("person") or "").strip().lower()
    if not person:
        raise CommuteError("missing_person", "Which person — Gui or Megan?")
    return person


def _agent(ctx: dict | None) -> str:
    return str((ctx or {}).get("agent") or "")


def _error(exc) -> str:
    if isinstance(exc, (CommuteError, train.TrainError)):
        return json.dumps(exc.as_dict(), ensure_ascii=False)
    return json.dumps(
        {"ok": False, "error": "commute_failed",
         "message": f"{type(exc).__name__}: {exc}"},
        ensure_ascii=False)


def handle_commute_check(integration, tool, args, ctx=None) -> str:
    try:
        person = _person_arg(args)
        return json.dumps(chat_check(person, (args or {}).get("at")),
                          ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - never leak a traceback
        return _error(exc)


def handle_commute_profile_get(integration, tool, args, ctx=None) -> str:
    try:
        person = _person_arg(args)
        profile = store.get_profile(person)
        if not profile:
            raise CommuteError("unknown_person", f"No commute profile for {person!r}.")
        return json.dumps({"ok": True, "person": person,
                           "text": summary(person, profile), "profile": profile},
                          ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def _parse_patch(args: dict) -> dict:
    patch = (args or {}).get("patch")
    if isinstance(patch, str):
        try:
            patch = json.loads(patch)
        except json.JSONDecodeError as exc:
            raise CommuteError("bad_patch", f"patch is not valid JSON ({exc})") from exc
    if not isinstance(patch, dict):
        raise CommuteError("bad_patch", "patch: give a JSON object")
    return patch


def handle_commute_profile_set(integration, tool, args, ctx=None) -> str:
    try:
        person = _person_arg(args)
        return json.dumps(
            update_profile(person, _parse_patch(args), _agent(ctx)),
            ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def handle_commute_override(integration, tool, args, ctx=None) -> str:
    try:
        person = _person_arg(args)
        return json.dumps(
            override(person, (args or {}).get("date"), (args or {}).get("action"),
                     (args or {}).get("arrive_by"), _agent(ctx)),
            ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def handle_commute_pause(integration, tool, args, ctx=None) -> str:
    try:
        person = _person_arg(args)
        until = (args or {}).get("until")
        if until is None or str(until).strip() == "":
            raise CommuteError(
                "missing_until", "until: give YYYY-MM-DD, or 'clear'")
        return json.dumps(pause(person, until, _agent(ctx)), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
