"""Synthetic Home Assistant inventory for the headless UI check.

Only *.example.test names and no real place. Five rooms, about forty useful
devices and sixty settings/diagnostic/helper/add-on entities, so the Stage 4
useful-device filter and the room readings are visible in the harness.

The classification fields come from the same server function the real S1
response uses, so the synthetic data cannot drift from the rule under test.
"""
import copy

from core.ha_devices import classify_entity

LABELS = [
    {"id": "lbl-lights", "name": "Living room lights"},
    {"id": "lbl-plugs", "name": "Plugs"},
]


def _device(entity_id, name, area, domain, state, device_class="", unit="",
            labels=None, platform="demo", entity_category=None,
            disabled_by=None, hidden_by=None, risky=False, protected=False):
    reg = {
        "name": name,
        "original_name": name,
        "entity_category": entity_category,
        "disabled_by": disabled_by,
        "hidden_by": hidden_by,
        "platform": platform,
    }
    attrs = {"device_class": device_class, "unit_of_measurement": unit}
    cls = classify_entity(entity_id, reg, attrs, platform)
    return {
        "id": entity_id,
        "name": name,
        "area": area,
        "area_id": area.lower().replace(" ", "_"),
        "domain": domain,
        "state": state,
        "unit": unit,
        "device_class": device_class,
        "labels": labels or [],
        "category": cls["category"],
        "sensor_kind": cls["sensor_kind"],
        "useful": cls["useful"],
        "settings_entity": bool(entity_category),
        "disabled": bool(disabled_by),
        "risky": bool(risky or cls["risky"]),
        "protected": protected,
    }


def _lamp(entity_id, name, area, state="off", **kw):
    return _device(entity_id, name, area, "light", state, **kw)


def _plug(entity_id, name, area, state="off", **kw):
    return _device(entity_id, name, area, "switch", state, **kw)


def _sensor(entity_id, name, area, kind, state, unit="", **kw):
    return _device(entity_id, name, area, "sensor", state, kind, unit, **kw)


def _binary(entity_id, name, area, kind, state, **kw):
    return _device(entity_id, name, area, "binary_sensor", state, kind, **kw)


DEVICES = [
    # Living room — 12 useful devices.
    _lamp("light.ceiling", "Ceiling light", "Living room", "on",
          labels=[LABELS[0]]),
    _plug("switch.lamp", "Living room lamp", "Living room", "on",
          labels=[LABELS[0]]),
    _plug("switch.plug", "TV plug", "Living room", "off",
          labels=[LABELS[1]], protected=True),
    _device("media_player.living_tv", "Living room TV", "Living room",
            "media_player", "playing"),
    _device("climate.living_room", "Living room heating", "Living room",
            "climate", "heat", "temperature", "°C"),
    _device("cover.living_blinds", "Living room blinds", "Living room",
            "cover", "open"),
    _sensor("sensor.living_temp", "Living room temperature", "Living room",
            "temperature", "21.4", "°C"),
    _sensor("sensor.living_humidity", "Living room humidity", "Living room",
            "humidity", "48", "%"),
    _binary("binary_sensor.living_motion", "Living room motion",
            "Living room", "motion", "on"),
    _binary("binary_sensor.front_door", "Front door", "Living room",
            "door", "off"),
    _sensor("sensor.living_power", "Living room power", "Living room",
            "power", "84", "W"),
    _device("lock.front_door", "Front door lock", "Living room", "lock",
            "locked", "lock"),
    _device("button.doorbell", "Doorbell", "Living room", "button",
            "unknown"),
    _device("scene.movie_night", "Movie night", "Living room", "scene",
            "unknown"),
    _device("script.goodnight", "Goodnight", "Living room", "script",
            "off"),
    _device("vacuum.robot", "Robot vacuum", "Living room", "vacuum",
            "docked"),
    _lamp("light.joiner", "New ceiling light", "Living room", "off",
          labels=[LABELS[0]]),
    _plug("switch.joiner", "New lamp", "Living room", "off",
          labels=[LABELS[0]]),

    # Kitchen — 8 useful devices.
    _lamp("light.kitchen_ceiling", "Kitchen ceiling", "Kitchen", "on"),
    _plug("switch.kettle", "Kettle", "Kitchen", "off"),
    _plug("switch.coffee", "Coffee machine", "Kitchen", "off"),
    _sensor("sensor.kitchen_temp", "Kitchen temperature", "Kitchen",
            "temperature", "20.8", "°C"),
    _sensor("sensor.kitchen_humidity", "Kitchen humidity", "Kitchen",
            "humidity", "55", "%"),
    _binary("binary_sensor.kitchen_motion", "Kitchen motion", "Kitchen",
            "motion", "off"),
    _binary("binary_sensor.kitchen_window", "Kitchen window", "Kitchen",
            "door", "closed"),
    _sensor("sensor.kitchen_power", "Kitchen power", "Kitchen", "power",
            "12", "W"),

    # Bedroom — 8 useful devices.
    _lamp("light.bedroom_ceiling", "Bedroom ceiling", "Bedroom", "off"),
    _plug("switch.electric_blanket", "Electric blanket", "Bedroom", "off"),
    _device("cover.bedroom_blinds", "Bedroom blinds", "Bedroom", "cover",
            "closed"),
    _sensor("sensor.bedroom_temp", "Bedroom temperature", "Bedroom",
            "temperature", "18.2", "°C"),
    _sensor("sensor.bedroom_humidity", "Bedroom humidity", "Bedroom",
            "humidity", "51", "%"),
    _binary("binary_sensor.bedroom_motion", "Bedroom motion", "Bedroom",
            "motion", "off"),
    _sensor("sensor.bedroom_battery", "Bedroom sensor battery", "Bedroom",
            "battery", "82", "%"),
    _sensor("sensor.bedroom_power", "Bedroom power", "Bedroom", "power",
            "4", "W"),

    # Office — 8 useful devices.
    _lamp("light.office_desk", "Desk lamp", "Office", "on"),
    _plug("switch.office_heater", "Office heater", "Office", "on"),
    _plug("switch.office_monitor", "Monitor plug", "Office", "on"),
    _device("fan.office_fan", "Office fan", "Office", "fan", "off"),
    _sensor("sensor.office_temp", "Office temperature", "Office",
            "temperature", "22.6", "°C"),
    _sensor("sensor.office_humidity", "Office humidity", "Office",
            "humidity", "44", "%"),
    _binary("binary_sensor.office_motion", "Office motion", "Office",
            "motion", "on"),
    _sensor("sensor.office_power", "Office power", "Office", "power",
            "136", "W"),

    # Garage — 6 useful devices.
    _lamp("light.garage", "Garage light", "Garage", "off"),
    _plug("switch.garage_freezer", "Garage freezer", "Garage", "on"),
    _device("cover.garage", "Garage door", "Garage", "cover", "closed",
            "garage", risky=True),
    _device("cover.garage_side", "Side gate", "Garage", "cover", "closed",
            "gate", risky=True),
    _device("valve.water_main", "Water main", "Garage", "valve", "open"),
    _sensor("sensor.garage_temp", "Garage temperature", "Garage",
            "temperature", "9.8", "°C"),
    _binary("binary_sensor.garage_motion", "Garage motion", "Garage",
            "motion", "off"),

    # ── Noise: settings, diagnostics, add-on switches and helpers ───────
    # Entity-category config on five rooms' child locks.
    _plug("switch.lamp_child_lock", "Lamp child lock", "Living room",
          entity_category="config"),
    _plug("switch.kettle_child_lock", "Kettle child lock", "Kitchen",
          entity_category="config"),
    _plug("switch.blanket_child_lock", "Blanket child lock", "Bedroom",
          entity_category="config"),
    _plug("switch.heater_child_lock", "Heater child lock", "Office",
          entity_category="config"),
    _plug("switch.freezer_child_lock", "Freezer child lock", "Garage",
          entity_category="config"),

    # LEDs and auto-off switches: no category, caught by the suffix fallback.
    _plug("switch.lamp_led", "Lamp LED", "Living room"),
    _plug("switch.kettle_led", "Kettle LED", "Kitchen"),
    _plug("switch.blanket_led", "Blanket LED", "Bedroom"),
    _plug("switch.heater_led", "Heater LED", "Office"),
    _plug("switch.freezer_led", "Freezer LED", "Garage"),
    _plug("switch.tv_auto_off", "TV auto-off", "Living room"),
    _plug("switch.kettle_auto_off", "Kettle auto-off", "Kitchen"),
    _plug("switch.heater_auto_off", "Heater auto-off", "Office"),
    _plug("switch.blanket_auto_update", "Blanket auto-update", "Bedroom"),
    _plug("switch.freezer_auto_update", "Freezer auto-update", "Garage"),

    # Diagnostic sensors (signal, uptime, status) on the same rooms.
    _sensor("sensor.living_rssi", "Living room signal", "Living room",
            "other", "-61", "dBm", entity_category="diagnostic"),
    _sensor("sensor.kitchen_rssi", "Kitchen signal", "Kitchen", "other",
            "-58", "dBm", entity_category="diagnostic"),
    _sensor("sensor.bedroom_rssi", "Bedroom signal", "Bedroom", "other",
            "-70", "dBm", entity_category="diagnostic"),
    _sensor("sensor.office_rssi", "Office signal", "Office", "other",
            "-63", "dBm", entity_category="diagnostic"),
    _sensor("sensor.garage_rssi", "Garage signal", "Garage", "other",
            "-77", "dBm", entity_category="diagnostic"),
    _sensor("sensor.living_uptime", "Living room uptime", "Living room",
            "other", "12", "h", entity_category="diagnostic"),
    _sensor("sensor.kitchen_uptime", "Kitchen uptime", "Kitchen", "other",
            "9", "h", entity_category="diagnostic"),
    _sensor("sensor.bedroom_uptime", "Bedroom uptime", "Bedroom", "other",
            "31", "h", entity_category="diagnostic"),
    _sensor("sensor.office_uptime", "Office uptime", "Office", "other",
            "4", "h", entity_category="diagnostic"),
    _sensor("sensor.garage_uptime", "Garage uptime", "Garage", "other",
            "55", "h", entity_category="diagnostic"),

    # Add-on and system integrations.
    _plug("switch.hacs_update", "HACS update", "Office", platform="hacs"),
    _plug("switch.hassio_addon_update", "Add-on update", "Office",
          platform="hassio"),
    _plug("switch.browser_mod_screen", "Browser screen", "Living room",
          platform="browser_mod"),
    _plug("switch.browser_mod_kiosk", "Browser kiosk", "Kitchen",
          platform="browser_mod"),
    _plug("switch.supervisor_backup", "Supervisor backup", "Office",
          platform="hassio"),
    _sensor("sensor.hacs_pending", "HACS pending", "Office", "other", "2",
            platform="hacs"),
    _sensor("sensor.supervisor_version", "Supervisor version", "Office",
            "other", "2026.11", platform="hassio"),
    _device("update.core", "Core update", "Office", "update", "on",
            platform="update"),
    _device("update.addons", "Add-on updates", "Office", "update", "off",
            platform="update"),
    _device("update.hacs", "HACS update", "Office", "update", "on",
            platform="hacs"),

    # Disabled and hidden entities.
    _plug("switch.old_sensor", "Old sensor plug", "Office", "unavailable",
          disabled_by="user"),
    _sensor("sensor.old_temp", "Old temperature", "Kitchen", "temperature",
            "unknown", "°C", disabled_by="integration"),
    _binary("binary_sensor.old_door", "Old door", "Garage", "door",
            "unavailable", disabled_by="user"),
    _plug("switch.hidden_plug", "Hidden plug", "Bedroom", hidden_by="user"),
    _binary("binary_sensor.hidden_motion", "Hidden motion", "Office",
            "motion", "off", hidden_by="integration"),

    # Helpers (input_boolean / input_number / counter / timer).
    _device("input_boolean.guest_mode", "Guest mode", "Living room",
            "input_boolean", "off"),
    _device("input_boolean.holiday", "Holiday mode", "Living room",
            "input_boolean", "off"),
    _device("input_number.target_temp", "Target temperature", "Living room",
            "input_number", "21", "°C"),
    _device("counter.door_opens", "Door opens", "Living room", "counter",
            "7"),
    _device("timer.coffee", "Coffee timer", "Kitchen", "timer", "idle"),

    # A few more diagnostic switches with the category set, to make the
    # hidden count comfortably above the useful count.
    _plug("switch.living_led_night", "Living room night LED", "Living room",
          entity_category="diagnostic"),
    _plug("switch.kitchen_led_night", "Kitchen night LED", "Kitchen",
          entity_category="diagnostic"),
    _plug("switch.bedroom_led_night", "Bedroom night LED", "Bedroom",
          entity_category="diagnostic"),
    _plug("switch.office_led_night", "Office night LED", "Office",
          entity_category="diagnostic"),
    _plug("switch.garage_led_night", "Garage night LED", "Garage",
          entity_category="diagnostic"),

    # More browser/add-on rows.
    _plug("switch.browser_mod_display", "Browser display", "Bedroom",
          platform="browser_mod"),
    _plug("switch.browser_mod_theme", "Browser theme", "Office",
          platform="browser_mod"),
    _plug("switch.hassio_auto_update", "Add-on auto-update", "Office",
          platform="hassio"),
    _plug("switch.hacs_auto_update", "HACS auto-update", "Office",
          platform="hacs"),
    _sensor("sensor.browser_mod_battery", "Browser battery", "Living room",
            "battery", "100", "%", platform="browser_mod"),
    _sensor("sensor.hassio_cpu", "Supervisor CPU", "Office", "other", "4",
            "%", platform="hassio"),
    _sensor("sensor.hacs_rate", "HACS rate", "Office", "other", "0",
            platform="hacs"),
    _binary("binary_sensor.browser_mod_connected", "Browser connected",
            "Kitchen", "other", "on", platform="browser_mod"),
]


def devices():
    return copy.deepcopy(DEVICES)


def labels():
    return copy.deepcopy(LABELS)


def registry():
    return {
        row["id"]: {"id": "reg-" + row["id"].split(".", 1)[1],
                    "name": row["name"],
                    "entity_category": "config" if row.get("settings_entity") else None,
                    "disabled_by": "user" if row.get("disabled") else None,
                    "labels": [x["id"] for x in row["labels"]]}
        for row in DEVICES
    }


def risky_classes(entity_ids):
    wanted = set(entity_ids or [])
    return ([{"entity_id": "cover.garage", "device_class": "garage"}]
            if "cover.garage" in wanted else [])
