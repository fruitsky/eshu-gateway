"""Unit tests for the Eshu-owned commute capability (no network)."""
import json
from datetime import date, datetime

import pytest

from core import commute, train
from core.commute_seed import COMMUTE_SEED_TOOLS, ensure_commute_integration
from core.seeds import seed_for_kind
from db import commute as store
from db.agent_tokens import create_agent_token
from db.integrations import get_integration, get_tools

LONDON = commute.LONDON
DAY = date(2026, 11, 2)          # Monday
NOW = datetime(2026, 11, 2, 5, 25, tzinfo=LONDON)


def fake_now(at=None):
    if not at:
        return NOW
    minutes = commute.parse_time(at)
    return NOW.replace(hour=minutes // 60, minute=minutes % 60)


def _journey(depart, arrive, duration, rides=1, modes=None, disrupted=False,
             actual_depart=None, actual_arrive=None):
    modes = modes or ["national-rail"] * rides
    actual_depart = actual_depart or depart
    actual_arrive = actual_arrive or arrive
    legs = []
    for index, mode in enumerate(modes):
        legs.append({
            "mode": {"name": mode},
            "instruction": {"summary": f"Southeastern to {mode}"},
            "scheduledDepartureTime": f"2026-11-02T{depart}:00",
            "departureTime": f"2026-11-02T{actual_depart if index == 0 else depart}:00",
            "scheduledArrivalTime": f"2026-11-02T{arrive}:00",
            "arrivalTime": f"2026-11-02T{actual_arrive if index == len(modes) - 1 else arrive}:00",
            "isDisrupted": disrupted,
            "disruptions": [{"summary": "late"}] if disrupted else [],
        })
    return {
        "duration": duration,
        "arrivalDateTime": f"2026-11-02T{actual_arrive}:00",
        "legs": legs,
    }


PROFILE = {
    "display_name": "Gui",
    "from": {"name": "Chelsfield Rail Station", "naptan": "910GCHLSFLD"},
    "to": {"name": "Cannon Street", "naptan": "910GCANONST"},
    "days": ["mon", "wed", "fri"],
    "arrive_by": "06:50",
    "usual_train": "06:16",
    "never_before": "06:16",
    "notify_times": ["05:25"],
    "warn_minutes": 3,
    "lines": ["southeastern"],
    "alerts_only": False,
    "overrides": {},
    "paused_until": None,
}

GOOD = {"southeastern": {"severity": 10, "description": "Good Service", "reason": ""}}
BAD = {"southeastern": {"severity": 6, "description": "Severe Delays",
                        "reason": "Signal failure at Lewisham"}}


class TestValidation:
    def test_sorts_notify_times(self):
        merged = commute.validate_patch({"notify_times": ["06:00", "05:30"]}, PROFILE)
        assert merged["notify_times"] == ["05:30", "06:00"]

    def test_maps_legacy_notify_at(self):
        merged = commute.validate_patch({"notify_at": "06:00"}, PROFILE)
        assert merged["notify_times"] == ["06:00"]

    def test_rejects_notify_after_usual(self):
        with pytest.raises(commute.CommuteError):
            commute.validate_patch({"notify_times": ["06:30"]}, PROFILE)

    def test_rejects_outside_window(self):
        with pytest.raises(commute.CommuteError):
            commute.validate_patch({"notify_times": ["03:30"]}, PROFILE)

    def test_rejects_duplicate_times(self):
        with pytest.raises(commute.CommuteError):
            commute.validate_patch({"notify_times": ["05:30", "05:30"]}, PROFILE)

    def test_rejects_unknown_field(self):
        with pytest.raises(commute.CommuteError):
            commute.validate_patch({"chat_id": "x"}, PROFILE)

    def test_rejects_bad_day(self):
        with pytest.raises(commute.CommuteError):
            commute.validate_patch({"days": ["mon", "funday"]}, PROFILE)

    def test_rejects_never_before_after_usual(self):
        with pytest.raises(commute.CommuteError):
            commute.validate_patch({"never_before": "06:30"}, PROFILE)

    def test_rejects_arrive_before_usual(self):
        with pytest.raises(commute.CommuteError):
            commute.validate_patch({"arrive_by": "06:00"}, PROFILE)

    def test_resolves_station_string(self, monkeypatch):
        monkeypatch.setattr(
            train, "resolve_station",
            lambda name: {"name": "Petts Wood Rail Station", "naptan": "910GPETSWD"})
        merged = commute.validate_patch(
            {"to": "Petts Wood"}, PROFILE)
        assert merged["to"]["naptan"] == "910GPETSWD"
        assert "Petts Wood" in merged["to"]["name"]

    def test_normalize_for_import_strips_chat_id(self):
        raw = dict(PROFILE, chat_id="123@lid", notify_at="05:25")
        raw.pop("notify_times")
        merged = commute.normalize_for_import(raw)
        assert "chat_id" not in merged
        assert merged["notify_times"] == ["05:25"]


class TestGate:
    def test_commute_day(self):
        assert commute.day_gate(PROFILE, DAY, set())[0]

    def test_non_commute_day(self):
        ok, why = commute.day_gate(PROFILE, date(2026, 11, 3), set())
        assert not ok
        assert "tue" in why

    def test_bank_holiday(self):
        ok, why = commute.day_gate(PROFILE, DAY, {"2026-11-02"})
        assert not ok
        assert why == "bank holiday"

    def test_override_skip(self):
        profile = dict(PROFILE, overrides={"2026-11-02": "skip"})
        assert not commute.day_gate(profile, DAY, set())[0]

    def test_override_office_dict_beats_non_commute_day(self):
        profile = dict(PROFILE, overrides={"2026-11-03": {"office": True}})
        assert commute.day_gate(profile, date(2026, 11, 3), set())[0]

    def test_override_office_dict_with_arrive_by(self):
        profile = dict(PROFILE, overrides={
            "2026-11-03": {"office": True, "arrive_by": "08:15"}})
        assert commute.day_gate(profile, date(2026, 11, 3), set())[0]
        assert commute.effective_profile(profile, date(2026, 11, 3))["arrive_by"] == "08:15"

    def test_paused(self):
        profile = dict(PROFILE, paused_until="2026-11-05")
        assert not commute.day_gate(profile, DAY, set())[0]
        assert commute.day_gate(profile, date(2026, 11, 6), set())[0]


class TestClassify:
    def test_normal(self):
        result = commute.classify(PROFILE, [_journey("06:16", "06:42", 26)],
                                  GOOD, NOW, DAY)
        assert result["level"] == "normal"
        assert "8 min spare" in result["text"]

    def test_warn_delay(self):
        result = commute.classify(
            PROFILE, [_journey("06:16", "06:46", 26, actual_depart="06:20")],
            GOOD, NOW, DAY)
        assert result["level"] == "warn"
        assert "~4 min late" in result["text"]

    def test_late(self):
        result = commute.classify(
            PROFILE, [_journey("06:16", "06:56", 26, actual_depart="06:30")],
            BAD, NOW, DAY)
        assert result["level"] == "late"
        assert "14 min late" in result["text"]

    def test_nodata(self):
        result = commute.classify(PROFILE, [], GOOD, NOW, DAY)
        assert result["level"] == "nodata"
        assert "can't get live train data" in result["text"]

    def test_passed_is_silent(self):
        later = datetime(2026, 11, 2, 7, 30, tzinfo=LONDON)
        result = commute.classify(PROFILE, [_journey("07:06", "07:32", 26)],
                                  GOOD, later, DAY)
        assert result["level"] == "passed"
        assert result["text"] == ""

    def test_slot_label_and_preceding(self):
        profile = dict(PROFILE, notify_times=["05:25", "06:00"])
        result = commute.classify(
            profile, [_journey("06:16", "06:42", 26)], GOOD, NOW, DAY,
            slot="05:25",
            preceding={"scheduled_depart": "06:31", "depart": "06:36",
                       "delay_minutes": 5})
        assert result["text"].startswith("Early check (05:25)")
        assert "The 06:31 is running 5 min late" in result["text"]


class TestCheck:
    def test_skip(self):
        profile = dict(PROFILE, days=["tue"])
        result = commute.check(profile, DAY, NOW, set())
        assert result["level"] == "skip"

    def test_fetch_failure_is_nodata(self, monkeypatch):
        def boom(*args, **kwargs):
            raise train.TrainError("tfl_unavailable", "no data")
        monkeypatch.setattr(train, "fetch_journeys", boom)
        result = commute.check(PROFILE, DAY, NOW, set())
        assert result["level"] == "nodata"
        assert "can't get live train data" in result["text"]

    def test_chat_check_skip_has_text(self, monkeypatch):
        monkeypatch.setattr(commute.store, "get_profile", lambda person: PROFILE)
        monkeypatch.setattr(commute, "bank_holidays", lambda: set())
        monkeypatch.setattr(commute, "check", lambda *a, **k: {
            "level": "skip", "text": "", "reason": "not a commute day (tue)"})
        result = commute.chat_check("gui", at="05:25")
        assert result["text"].startswith("No commute alert today")
        assert result["send"] is True

    def test_chat_check_unknown_person(self, monkeypatch):
        monkeypatch.setattr(commute.store, "get_profile", lambda person: None)
        with pytest.raises(commute.CommuteError):
            commute.chat_check("nobody")


class TestDue:
    def test_not_due(self, monkeypatch):
        monkeypatch.setattr(commute.store, "get_profile", lambda person: PROFILE)
        monkeypatch.setattr(commute, "now_london", fake_now)
        monkeypatch.setattr(commute, "bank_holidays", lambda: set())
        result = commute.due("gui", at="06:00")
        assert result["send"] is False

    def test_sends_and_marks(self, monkeypatch):
        monkeypatch.setattr(commute.store, "get_profile", lambda person: PROFILE)
        monkeypatch.setattr(commute, "now_london", fake_now)
        monkeypatch.setattr(commute, "bank_holidays", lambda: set())
        monkeypatch.setattr(commute, "check", lambda *a, **k: {
            "level": "normal", "text": "hello"})
        store.clear_sent("gui", "2026-11-02")
        result = commute.due("gui", at="05:25")
        assert result["send"] is True
        assert result["slot"] == "05:25"
        assert store.was_sent("gui", "2026-11-02", "05:25")
        second = commute.due("gui", at="05:25")
        assert second["send"] is False

    def test_dry_run_does_not_mark(self, monkeypatch):
        monkeypatch.setattr(commute.store, "get_profile", lambda person: PROFILE)
        monkeypatch.setattr(commute, "now_london", fake_now)
        monkeypatch.setattr(commute, "bank_holidays", lambda: set())
        monkeypatch.setattr(commute, "check", lambda *a, **k: {
            "level": "normal", "text": "hello"})
        store.clear_sent("gui", "2026-11-02")
        result = commute.due("gui", at="05:25", dry_run=True)
        assert result["send"] is True
        assert not store.was_sent("gui", "2026-11-02", "05:25")

    def test_day_gate_blocks(self, monkeypatch):
        tue_profile = dict(PROFILE, days=["tue"])
        monkeypatch.setattr(commute.store, "get_profile", lambda person: tue_profile)
        monkeypatch.setattr(commute, "now_london", fake_now)
        monkeypatch.setattr(commute, "bank_holidays", lambda: set())
        result = commute.due("gui", at="05:25")
        assert result["send"] is False


class TestStore:
    def test_save_get_audit(self):
        saved = store.save_profile("gui", PROFILE, agent="test")
        assert saved == PROFILE
        assert store.get_profile("gui") == PROFILE
        updated = dict(PROFILE, notify_times=["05:30", "06:00"])
        store.save_profile("gui", updated, agent="test")
        assert store.get_profile("gui")["notify_times"] == ["05:30", "06:00"]
        audit = store.list_audit("gui")
        assert audit and audit[0]["summary"].startswith("changed:")

    def test_state(self):
        store.clear_sent("gui", "2026-11-02")
        assert not store.was_sent("gui", "2026-11-02", "05:25")
        store.mark_sent("gui", "2026-11-02", "05:25")
        assert store.was_sent("gui", "2026-11-02", "05:25")


class TestHandlers:
    def test_profile_set_and_read_back(self):
        store.save_profile("gui", PROFILE)
        out = json.loads(commute.handle_commute_profile_set(
            None, None, {"person": "gui", "patch": {
                "notify_times": ["05:30", "06:00"]}}, {"agent": "test"}))
        assert out["ok"] is True
        assert store.get_profile("gui")["notify_times"] == ["05:30", "06:00"]
        assert "05:30" in out["text"]

    def test_bad_patch_writes_nothing(self):
        store.save_profile("gui", PROFILE)
        before = store.get_profile("gui")
        out = json.loads(commute.handle_commute_profile_set(
            None, None, {"person": "gui", "patch": {"days": ["funday"]}}))
        assert out["ok"] is False
        assert store.get_profile("gui") == before

    def test_override(self):
        store.save_profile("gui", PROFILE)
        out = json.loads(commute.handle_commute_override(
            None, None, {"person": "gui", "date": "2026-11-04", "action": "skip"}))
        assert out["ok"] is True
        assert store.get_profile("gui")["overrides"]["2026-11-04"] == "skip"
        assert store.get_profile("gui")["days"] == PROFILE["days"]

    def test_pause_requires_until(self):
        store.save_profile("gui", PROFILE)
        out = json.loads(commute.handle_commute_pause(
            None, None, {"person": "gui"}))
        assert out["ok"] is False
        assert out["error"] == "missing_until"

    def test_override_clear(self):
        store.save_profile("gui", PROFILE)
        commute.handle_commute_override(
            None, None, {"person": "gui", "date": "2026-11-04", "action": "skip"})
        out = json.loads(commute.handle_commute_override(
            None, None, {"person": "gui", "date": "2026-11-04", "action": "clear"}))
        assert out["ok"] is True
        assert "2026-11-04" not in store.get_profile("gui")["overrides"]

    def test_pause_and_clear(self):
        store.save_profile("gui", PROFILE)
        out = json.loads(commute.handle_commute_pause(
            None, None, {"person": "gui", "until": "2026-11-07"}))
        assert out["ok"] is True
        assert store.get_profile("gui")["paused_until"] == "2026-11-07"
        out = json.loads(commute.handle_commute_pause(
            None, None, {"person": "gui", "until": "clear"}))
        assert out["ok"] is True
        assert store.get_profile("gui")["paused_until"] is None

    def test_missing_person(self):
        out = json.loads(commute.handle_commute_check(None, None, {}))
        assert out["ok"] is False
        assert out["error"] == "missing_person"


class TestSeed:
    def test_integration_and_tools(self):
        first = ensure_commute_integration()
        second = ensure_commute_integration()
        assert first["name"] == "commute"
        assert second["id"] == first["id"]
        created, updated = seed_for_kind(get_integration("commute"))
        assert created == len(COMMUTE_SEED_TOOLS)
        assert updated == 0
        tools = {tool["name"]: tool for tool in get_tools(first["id"])}
        assert set(tools) == {
            "check", "profile_get", "profile_set", "override", "pause"}
        assert tools["check"]["read_only"] == 1
        assert tools["profile_set"]["read_only"] == 0
        assert tools["profile_set"]["always_gate"] == 0
        assert tools["profile_set"]["principal_allow"] == ["rowana-household"]
        assert "read" not in tools and "write" not in tools
        import keyword
        for tool in tools.values():
            for param in tool["params"]:
                assert not keyword.iskeyword(param["name"]), param["name"]
        created, updated = seed_for_kind(get_integration("commute"))
        assert created == 0
        assert updated == len(COMMUTE_SEED_TOOLS)


class TestDueEndpoint:
    def test_requires_agent_token(self, client):
        response = client.post("/api/commute/due", json={"person": "gui"})
        assert response.status_code == 401

    def test_wrong_agent_forbidden(self, client):
        raw, _agent_id = create_agent_token("someone-else")
        response = client.post(
            "/api/commute/due", json={"person": "gui"},
            headers={"Authorization": f"Bearer {raw}"})
        assert response.status_code == 403

    def test_dedicated_scheduler_token(self, client, monkeypatch):
        raw, _agent_id = create_agent_token("commute-cron")
        store.save_profile("gui", PROFILE)
        monkeypatch.setattr(commute, "now_london", lambda at=None: NOW)
        monkeypatch.setattr(commute, "bank_holidays", lambda: set())
        monkeypatch.setattr(commute, "check", lambda *a, **k: {
            "level": "normal", "text": "hello"})
        store.clear_sent("gui", "2026-11-02")
        response = client.post(
            "/api/commute/due", json={"person": "gui", "at": "05:25"},
            headers={"Authorization": f"Bearer {raw}"})
        assert response.status_code == 200
        assert response.json()["send"] is True

    def test_due_endpoint(self, client, monkeypatch):
        raw, _agent_id = create_agent_token("rowana-household")
        store.save_profile("gui", PROFILE)
        monkeypatch.setattr(commute, "now_london", fake_now)
        monkeypatch.setattr(commute, "bank_holidays", lambda: set())
        monkeypatch.setattr(commute, "check", lambda *a, **k: {
            "level": "normal", "text": "hello"})
        store.clear_sent("gui", "2026-11-02")
        response = client.post(
            "/api/commute/due", json={"person": "gui", "at": "05:25"},
            headers={"Authorization": f"Bearer {raw}"})
        assert response.status_code == 200
        body = response.json()
        assert body["send"] is True
        assert body["text"] == "hello"
