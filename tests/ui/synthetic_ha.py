"""Synthetic Home Assistant inventory for the headless UI check.

Only *.example.test ids and 192.0.2.x addresses. Nothing here is read by the
dashboard unless tests/ui/serve_ui.py is started with ESHU_UI_SYNTHETIC_HA=1.
"""
import copy

LABELS = [
    {"id": "lbl-lights", "name": "Living room lights"},
    {"id": "lbl-plugs", "name": "Plugs"},
]

DEVICES = [
    {"id": "switch.lamp", "name": "Living room lamp", "area": "Living room",
     "area_id": "living", "domain": "switch", "state": "on",
     "device_class": "", "labels": [LABELS[0]], "settings_entity": False,
     "disabled": False, "risky": False, "protected": False},
    {"id": "switch.plug", "name": "TV plug", "area": "Living room",
     "area_id": "living", "domain": "switch", "state": "off",
     "device_class": "", "labels": [LABELS[1]], "settings_entity": False,
     "disabled": False, "risky": False, "protected": True},
    {"id": "switch.kettle", "name": "Kettle", "area": "Kitchen",
     "area_id": "kitchen", "domain": "switch", "state": "off",
     "device_class": "", "labels": [], "settings_entity": False,
     "disabled": False, "risky": False, "protected": False},
    {"id": "switch.office_heater", "name": "Office heater", "area": "Office",
     "area_id": "office", "domain": "switch", "state": "on",
     "device_class": "", "labels": [], "settings_entity": False,
     "disabled": False, "risky": False, "protected": False},
    {"id": "switch.joiner", "name": "New lamp", "area": "Living room",
     "area_id": "living", "domain": "switch", "state": "off",
     "device_class": "", "labels": [LABELS[0]], "settings_entity": False,
     "disabled": False, "risky": False, "protected": False},
    {"id": "light.joiner", "name": "New ceiling light", "area": "Living room",
     "area_id": "living", "domain": "light", "state": "off",
     "device_class": "", "labels": [LABELS[0]], "settings_entity": False,
     "disabled": False, "risky": False, "protected": False},
    {"id": "light.ceiling", "name": "Ceiling light", "area": "Living room",
     "area_id": "living", "domain": "light", "state": "on",
     "device_class": "", "labels": [LABELS[0]], "settings_entity": False,
     "disabled": False, "risky": False, "protected": False},
    {"id": "cover.garage", "name": "Garage door", "area": "Garage",
     "area_id": "garage", "domain": "cover", "state": "closed",
     "device_class": "garage", "labels": [], "settings_entity": False,
     "disabled": False, "risky": True, "protected": False},
    {"id": "switch.lamp_child_lock", "name": "Lamp child lock",
     "area": "Living room", "area_id": "living", "domain": "switch",
     "state": "off", "device_class": "", "labels": [],
     "settings_entity": True, "disabled": False, "risky": False,
     "protected": False},
    {"id": "switch.old_sensor", "name": "Old sensor plug", "area": "Office",
     "area_id": "office", "domain": "switch", "state": "unavailable",
     "device_class": "", "labels": [], "settings_entity": False,
     "disabled": True, "risky": False, "protected": False},
]


def devices():
    return copy.deepcopy(DEVICES)


def labels():
    return copy.deepcopy(LABELS)


def registry():
    return {
        row["id"]: {"id": "reg-" + row["id"].split(".", 1)[1],
                    "labels": [x["id"] for x in row["labels"]]}
        for row in DEVICES
    }


def risky_classes(entity_ids):
    wanted = set(entity_ids or [])
    return ([{"entity_id": "cover.garage", "device_class": "garage"}]
            if "cover.garage" in wanted else [])
