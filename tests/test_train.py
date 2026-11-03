"""Unit tests for the Eshu-owned TfL train capability (no network)."""
import json
from datetime import date, datetime, timedelta

import pytest

from core import train
from core.seeds import seed_for_kind
from core.train_seed import TRAIN_SEED_TOOLS, ensure_train_integration
from db.integrations import get_integration, get_tools


def _journey(depart, arrive, duration, rides=1, modes=None, disrupted=False,
             actual_depart=None, scheduled_arrive=None, actual_arrive=None):
    modes = modes or ["national-rail"] * rides
    actual_depart = actual_depart or depart
    scheduled_arrive = scheduled_arrive or arrive
    actual_arrive = actual_arrive or arrive
    legs = []
    for index, mode in enumerate(modes):
        is_first = index == 0
        is_last = index == len(modes) - 1
        legs.append({
            "mode": {"name": mode},
            "instruction": {"summary": f"Southeastern to {mode}"},
            "scheduledDepartureTime": f"2026-11-03T{depart}:00",
            "departureTime": f"2026-11-03T{actual_depart if is_first else depart}:00",
            "scheduledArrivalTime": f"2026-11-03T{scheduled_arrive if is_last else arrive}:00",
            "arrivalTime": f"2026-11-03T{actual_arrive if is_last else arrive}:00",
            "isDisrupted": disrupted,
            "disruptions": [{"summary": "late"}] if disrupted else [],
        })
    return {
        "duration": duration,
        "arrivalDateTime": f"2026-11-03T{actual_arrive}:00",
        "legs": legs,
    }


def _station(name, naptan):
    return {"name": name, "naptan": naptan}


def test_parse_hhmm_and_format():
    assert train.parse_hhmm("06:16") == 376
    assert train.fmt_hhmm(376) == "06:16"
    with pytest.raises(train.TrainError):
        train.parse_hhmm("6.50am")


def test_normalize_date():
    assert train.normalize_date("2026-11-03").isoformat() == "2026-11-03"
    assert train.normalize_date("today") == train.today_london()
    assert train.normalize_date("tomorrow") == train.today_london() + timedelta(days=1)
    with pytest.raises(train.TrainError):
        train.normalize_date("next tuesday")


def test_validate_request_time_rejects_past_arrive_by():
    now = datetime(2026, 11, 3, 14, 0, tzinfo=train.LONDON)
    with pytest.raises(train.TrainError) as exc:
        train.validate_request_time(date(2026, 11, 3), 13 * 60 + 30, True, now=now)
    assert exc.value.code == "time_passed"


def test_validate_request_time_allows_past_depart_after():
    now = datetime(2026, 11, 3, 14, 0, tzinfo=train.LONDON)
    train.validate_request_time(date(2026, 11, 3), 13 * 60 + 30, False, now=now)


def test_validate_request_time_rejects_past_date():
    now = datetime(2026, 11, 3, 14, 0, tzinfo=train.LONDON)
    with pytest.raises(train.TrainError) as exc:
        train.validate_request_time(date(2026, 11, 2), 13 * 60 + 30, True, now=now)
    assert exc.value.code == "date_passed"


def test_resolve_station_ambiguous(monkeypatch):
    def fake_http(url, timeout=25):
        return 200, {"matches": [
            {"name": "Bromley Cross Rail Station", "id": "910GBRMCRSS", "modes": ["national-rail"]},
            {"name": "Bromley North Rail Station", "id": "910GBROMLYN", "modes": ["national-rail"]},
            {"name": "Bromley South Rail Station", "id": "910GBROMLYS", "modes": ["national-rail"]},
        ]}, None

    monkeypatch.setattr(train, "http_json", fake_http)
    with pytest.raises(train.TrainError) as exc:
        train.resolve_station("Bromley")
    assert exc.value.code == "station_ambiguous"
    assert len(exc.value.extra["candidates"]) == 3


def test_resolve_station_not_found(monkeypatch):
    monkeypatch.setattr(train, "http_json", lambda *a, **k: (200, {"matches": []}, None))
    with pytest.raises(train.TrainError) as exc:
        train.resolve_station("Nowhere")
    assert exc.value.code == "station_not_found"


def test_resolve_station_resolves_hub_to_rail_child(monkeypatch):
    def fake_http(url, timeout=25):
        if "/StopPoint/Search/" in url:
            return 200, {"matches": [{"name": "Cannon Street", "id": "HUBCST", "modes": []}]}, None
        if "/StopPoint/HUBCST" in url:
            return 200, {"commonName": "Cannon Street", "children": [
                {"id": "910GCANONST", "commonName": "London Cannon Street Rail Station",
                 "modes": ["national-rail"]},
                {"id": "940GZZLUCST", "commonName": "Cannon Street Underground Station",
                 "modes": ["tube"]},
            ]}, None
        return 500, None, "unexpected"

    monkeypatch.setattr(train, "http_json", fake_http)
    resolved = train.resolve_station("Cannon Street")
    assert resolved["naptan"] == "910GCANONST"
    assert "Cannon Street" in resolved["name"]


def test_resolve_station_prefers_exact_name_hub(monkeypatch):
    def fake_http(url, timeout=25):
        if "/StopPoint/Search/" in url:
            return 200, {"matches": [
                {"name": "Waterloo", "id": "HUBWAT", "modes": []},
                {"name": "London Waterloo East Rail Station", "id": "910GWLOE", "modes": ["national-rail"]},
                {"name": "Waterloo (Merseyside) Rail Station", "id": "910GWLOO", "modes": ["national-rail"]},
            ]}, None
        if "/StopPoint/HUBWAT" in url:
            return 200, {"children": [
                {"id": "910GWATRLMN", "commonName": "London Waterloo Rail Station",
                 "modes": ["national-rail"]},
            ]}, None
        return 500, None, "unexpected"

    monkeypatch.setattr(train, "http_json", fake_http)
    resolved = train.resolve_station("Waterloo")
    assert resolved["naptan"] == "910GWATRLMN"


def test_resolve_station_accepts_tube_station(monkeypatch):
    monkeypatch.setattr(
        train, "http_json",
        lambda *a, **k: (200, {"matches": [{
            "name": "Bromley-by-Bow Underground Station",
            "id": "940GZZLUBBB",
            "modes": ["tube"],
        }]}, None))
    resolved = train.resolve_station("Bromley-by-Bow")
    assert resolved["naptan"] == "940GZZLUBBB"


def test_resolve_station_hub_without_usable_child(monkeypatch):
    def fake_http(url, timeout=25):
        if "/StopPoint/Search/" in url:
            return 200, {"matches": [{"name": "Somewhere", "id": "HUBXXX", "modes": []}]}, None
        return 200, {"children": [
            {"id": "490G000001", "commonName": "Somewhere Bus Station", "modes": ["bus"]},
        ]}, None

    monkeypatch.setattr(train, "http_json", fake_http)
    with pytest.raises(train.TrainError) as exc:
        train.resolve_station("Somewhere")
    assert exc.value.code == "station_hub"


def test_arrive_by_prefers_fewest_changes_then_latest(monkeypatch):
    stations = {
        "Chelsfield": _station("Chelsfield Rail Station", "910GCHLSFLD"),
        "Petts Wood": _station("Petts Wood Rail Station", "910GPETSWD"),
    }
    monkeypatch.setattr(train, "resolve_station", lambda name: stations[name])
    monkeypatch.setattr(train, "fetch_journeys", lambda *a, **k: [
        _journey("12:44", "13:08", 24, rides=3, modes=["bus", "national-rail", "national-rail"]),
        _journey("13:01", "13:11", 10, rides=1),
        _journey("13:04", "13:27", 23, rides=3, modes=["bus", "national-rail", "national-rail"]),
    ])

    result = train.plan("Chelsfield", "Petts Wood", "13:30", arrive_by=True,
                        date_value="2026-11-03")
    assert result["ok"] is True
    assert result["meets_deadline"] is True
    assert [o["depart"] for o in result["options"]] == ["13:01", "13:04", "12:44"]
    assert result["options"][0]["changes"] == 0
    assert result["options"][1]["note"] == "latest that makes it"
    assert "arriving by 13:30" in result["text"]
    assert "direct" in result["text"]


def test_depart_after_prefers_earliest_arrival(monkeypatch):
    stations = {
        "Chelsfield": _station("Chelsfield Rail Station", "910GCHLSFLD"),
        "Petts Wood": _station("Petts Wood Rail Station", "910GPETSWD"),
    }
    monkeypatch.setattr(train, "resolve_station", lambda name: stations[name])
    monkeypatch.setattr(train, "fetch_journeys", lambda *a, **k: [
        _journey("13:44", "14:08", 24, rides=3, modes=["bus", "national-rail", "national-rail"]),
        _journey("13:31", "13:41", 10, rides=1),
        _journey("14:01", "14:11", 10, rides=1),
    ])

    result = train.plan("Chelsfield", "Petts Wood", "13:30", arrive_by=False,
                        date_value="2026-11-03")
    assert result["mode"] == "depart_after"
    assert [o["depart"] for o in result["options"]] == ["13:31", "13:44", "14:01"]
    assert "leaving after 13:30" in result["text"]


def test_delayed_service_uses_actual_times(monkeypatch):
    stations = {
        "Chelsfield": _station("Chelsfield Rail Station", "910GCHLSFLD"),
        "Petts Wood": _station("Petts Wood Rail Station", "910GPETSWD"),
    }
    monkeypatch.setattr(train, "resolve_station", lambda name: stations[name])
    monkeypatch.setattr(train, "fetch_journeys", lambda *a, **k: [
        _journey("13:31", "13:41", 6, rides=1,
                 actual_depart="13:39", actual_arrive="13:45"),
    ])

    result = train.plan("Chelsfield", "Petts Wood", "13:30", arrive_by=False,
                        date_value="2026-11-03")
    option = result["options"][0]
    assert option["depart"] == "13:39"
    assert option["scheduled_depart"] == "13:31"
    assert option["delay_minutes"] == 8
    assert "13:39" in result["text"]
    assert "8 min late (was 13:31)" in result["text"]


def test_arrive_by_no_option_meets_deadline(monkeypatch):
    stations = {
        "Chelsfield": _station("Chelsfield Rail Station", "910GCHLSFLD"),
        "Petts Wood": _station("Petts Wood Rail Station", "910GPETSWD"),
    }
    monkeypatch.setattr(train, "resolve_station", lambda name: stations[name])
    monkeypatch.setattr(train, "fetch_journeys", lambda *a, **k: [
        _journey("13:01", "13:41", 40, rides=1),
    ])

    result = train.plan("Chelsfield", "Petts Wood", "13:30", arrive_by=True,
                        date_value="2026-11-03")
    assert result["meets_deadline"] is False
    assert "No option arrives by 13:30" in result["text"]
    assert result["options"][0]["note"] == "earliest available"


def test_handler_returns_structured_error_for_missing_origin():
    out = json.loads(train.handle_train_plan(None, None, {"destination": "Petts Wood", "time": "13:30"}))
    assert out["ok"] is False
    assert out["error"] == "missing_origin"
    assert "starting" in out["message"].lower()


def test_handler_returns_structured_error_for_missing_time():
    out = json.loads(train.handle_train_plan(None, None, {"origin": "Chelsfield", "destination": "Petts Wood"}))
    assert out["ok"] is False
    assert out["error"] == "missing_time"
    assert "what time" in out["message"].lower()


def test_handler_success(monkeypatch):
    stations = {
        "Chelsfield": _station("Chelsfield Rail Station", "910GCHLSFLD"),
        "Petts Wood": _station("Petts Wood Rail Station", "910GPETSWD"),
    }
    monkeypatch.setattr(train, "resolve_station", lambda name: stations[name])
    monkeypatch.setattr(train, "fetch_journeys", lambda *a, **k: [
        _journey("13:01", "13:11", 10, rides=1),
    ])
    out = json.loads(train.handle_train_plan(None, None, {
        "origin": "Chelsfield", "destination": "Petts Wood", "time": "13:30", "date": "tomorrow",
    }))
    assert out["ok"] is True
    assert "13:01" in out["text"]


def test_status(monkeypatch):
    monkeypatch.setattr(train, "http_json", lambda *a, **k: (200, [{
        "id": "southeastern",
        "name": "Southeastern",
        "lineStatuses": [{
            "statusSeverity": 10,
            "statusSeverityDescription": "Good Service",
            "reason": "",
        }],
    }], None))
    result = train.status("southeastern")
    assert result["ok"] is True
    assert result["text"] == "Southeastern: Good Service"


def test_status_moves_url_to_detail_line(monkeypatch):
    monkeypatch.setattr(train, "http_json", lambda *a, **k: (200, [{
        "id": "southeastern",
        "name": "Southeastern",
        "lineStatuses": [{
            "statusSeverity": 6,
            "statusSeverityDescription": "Severe Delays",
            "reason": "Signal failure at Lewisham https://nationalrail.co.uk/x",
        }],
    }], None))
    result = train.status("southeastern")
    assert result["text"].startswith("Southeastern: Severe Delays — Signal failure at Lewisham")
    assert "\n— Detail: https://nationalrail.co.uk/x" in result["text"]
    assert "Severe Delays — https" not in result["text"]


def test_status_handler(monkeypatch):
    monkeypatch.setattr(train, "fetch_line_status", lambda lines: [{
        "id": "southeastern", "name": "Southeastern", "severity": 10,
        "description": "Good Service", "reason": "",
    }])
    out = json.loads(train.handle_train_status(None, None, {"lines": "southeastern"}))
    assert out["ok"] is True
    assert "Good Service" in out["text"]


def test_ensure_train_integration_and_seed():
    first = ensure_train_integration()
    second = ensure_train_integration()
    assert first["name"] == "tfl"
    assert second["id"] == first["id"]
    created, updated = seed_for_kind(get_integration("tfl"))
    assert created == len(TRAIN_SEED_TOOLS)
    assert updated == 0
    tools = {tool["name"]: tool for tool in get_tools(first["id"])}
    assert set(tools) == {"train_plan", "train_status"}
    assert tools["train_plan"]["handler"] == "train_plan"
    assert tools["train_plan"]["read_only"] == 1
    assert tools["train_status"]["handler"] == "train_status"
    # MCP tool signatures are generated from param names; Python keywords
    # (notably `from`) produce a SyntaxError and silently drop the tool.
    import keyword
    for tool in tools.values():
        for param in tool["params"]:
            assert not keyword.iskeyword(param["name"]), param["name"]
    # Built-in native capability: no generic passthrough floor.
    assert "read" not in tools and "write" not in tools
    # Re-seeding updates in place rather than duplicating.
    created, updated = seed_for_kind(get_integration("tfl"))
    assert created == 0
    assert updated == len(TRAIN_SEED_TOOLS)
