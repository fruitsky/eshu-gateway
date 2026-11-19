"""X11 Stage 4: the server's category, sensor kind and useful-device rules.

These are pure-function tests over a synthetic registry, so every hidden
reason and every category is covered without a Home Assistant instance.
"""
import pytest

from core.ha_devices import classify_entity


def _reg(**over):
    row = {'name': '', 'original_name': '', 'entity_category': None,
           'disabled_by': None, 'hidden_by': None, 'platform': 'demo'}
    row.update(over)
    return row


def _attrs(**over):
    row = {'device_class': '', 'unit_of_measurement': ''}
    row.update(over)
    return row


@pytest.mark.parametrize('entity_id, expected', [
    ('switch.plug', 'switch'),
    ('input_boolean.helper', 'switch'),
    ('light.ceiling', 'light'),
    ('cover.garage', 'cover'),
    ('climate.hall', 'climate'),
    ('fan.bedroom', 'fan'),
    ('media_player.tv', 'media'),
    ('sensor.temperature', 'sensor'),
    ('binary_sensor.motion', 'sensor'),
])
def test_each_category(entity_id, expected):
    assert classify_entity(entity_id, _reg(), _attrs())['category'] == expected


@pytest.mark.parametrize('entity_id, attrs, expected', [
    ('sensor.outside', _attrs(device_class='temperature'), 'temperature'),
    ('sensor.outside', _attrs(unit_of_measurement='°C'), 'temperature'),
    ('sensor.bathroom', _attrs(device_class='humidity'), 'humidity'),
    ('binary_sensor.hall', _attrs(device_class='motion'), 'motion'),
    ('binary_sensor.occupancy', _attrs(device_class='occupancy'), 'motion'),
    ('binary_sensor.door', _attrs(device_class='door'), 'door'),
    ('binary_sensor.window', _attrs(device_class='window'), 'door'),
    ('sensor.washer', _attrs(device_class='power'), 'power'),
    ('sensor.washer', _attrs(unit_of_measurement='W'), 'power'),
    ('sensor.battery', _attrs(device_class='battery'), 'battery'),
    ('sensor.unknown', _attrs(device_class='illuminance'), 'other'),
])
def test_each_sensor_kind(entity_id, attrs, expected):
    assert classify_entity(entity_id, _reg(), attrs)['sensor_kind'] == expected


@pytest.mark.parametrize('entity_id, reg, platform', [
    ('switch.lamp_child_lock', _reg(name='Lamp child lock'), 'demo'),
    ('switch.lamp_led', _reg(name='Lamp LED'), 'demo'),
    ('switch.plug_auto_off', _reg(name='Plug auto-off'), 'demo'),
    ('switch.plug_auto_update', _reg(name='Plug auto-update'), 'demo'),
    ('switch.thing', _reg(name='Thing', entity_category='config'), 'demo'),
    ('sensor.thing', _reg(name='Thing', entity_category='diagnostic'), 'demo'),
    ('switch.thing', _reg(name='Thing', disabled_by='user'), 'demo'),
    ('switch.thing', _reg(name='Thing', hidden_by='integration'), 'demo'),
    ('switch.thing', _reg(name='Thing'), 'hacs'),
    ('switch.thing', _reg(name='Thing'), 'hassio'),
    ('switch.thing', _reg(name='Thing'), 'browser_mod'),
    ('update.core', _reg(name='Core update'), 'update'),
    ('input_boolean.guest', _reg(name='Guest mode'), 'demo'),
])
def test_each_hidden_reason(entity_id, reg, platform):
    row = classify_entity(entity_id, reg, _attrs(), platform)
    assert row['useful'] is False, (entity_id, reg, platform)


def test_entity_category_wins_over_name_suffix():
    """A diagnostic switch with a friendly name is still hidden."""
    row = classify_entity('switch.air_filter', _reg(
        name='Air filter', entity_category='diagnostic'), _attrs(), 'demo')
    assert row['useful'] is False


def test_ordinary_devices_are_useful():
    for entity_id in ('switch.plug', 'light.ceiling', 'cover.garage',
                      'climate.hall', 'fan.bedroom', 'media_player.tv',
                      'sensor.outside', 'binary_sensor.motion'):
        assert classify_entity(entity_id, _reg(name='Thing'), _attrs(),
                               'demo')['useful'] is True


def test_unknown_domain_is_not_useful():
    row = classify_entity('lock.front_door', _reg(name='Front door'),
                          _attrs(), 'demo')
    assert row['category'] == ''
    assert row['useful'] is False


def test_helper_domain_is_hidden_even_though_it_maps_to_switch():
    row = classify_entity('input_boolean.guest_mode', _reg(name='Guest mode'),
                          _attrs(), 'demo')
    assert row['category'] == 'switch'
    assert row['useful'] is False
