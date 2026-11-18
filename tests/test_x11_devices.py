"""X11/S1: the operator-only HA device inventory."""
import pytest

from core import allowances
from core import ha_devices
from db.agent_tokens import create_agent_token
from db.integrations import create_integration


ENTITIES = [
    {'entity_id': 'light.lamp', 'name': 'Lamp', 'original_name': 'Lamp',
     'entity_category': None, 'disabled_by': None,
     'labels': ['lbl-evening'], 'area_id': 'kitchen', 'device_id': 'dev-lamp'},
    {'entity_id': 'switch.child_lock', 'name': 'Child lock',
     'original_name': 'Child lock', 'entity_category': 'config',
     'disabled_by': 'user', 'labels': [], 'area_id': '',
     'device_id': 'dev-child'},
    {'entity_id': 'cover.garage', 'name': 'Garage door',
     'original_name': 'Garage door', 'entity_category': None,
     'disabled_by': None, 'labels': [], 'area_id': '',
     'device_id': 'dev-garage'},
    {'entity_id': 'sensor.battery', 'name': 'Battery',
     'original_name': 'Battery', 'entity_category': 'diagnostic',
     'disabled_by': None, 'labels': [], 'area_id': 'kitchen',
     'device_id': 'dev-lamp'},
]
DEVICES = [
    {'id': 'dev-lamp', 'area_id': 'kitchen'},
    {'id': 'dev-child', 'area_id': 'kitchen'},
    {'id': 'dev-garage', 'area_id': 'outside'},
]
AREAS = [{'area_id': 'kitchen', 'name': 'Kitchen'},
         {'area_id': 'outside', 'name': 'Outside'}]
LABELS = [{'label_id': 'lbl-evening', 'name': 'Evening lights'}]
STATES = [
    {'entity_id': 'light.lamp', 'state': 'on',
     'attributes': {'friendly_name': 'Reading lamp'}},
    {'entity_id': 'switch.child_lock', 'state': 'off', 'attributes': {}},
    {'entity_id': 'cover.garage', 'state': 'closed',
     'attributes': {'device_class': 'garage'}},
    {'entity_id': 'sensor.battery', 'state': '87',
     'attributes': {'device_class': 'battery'}},
]


@pytest.fixture
def inventory(monkeypatch):
    create_integration('Home Assistant', 'http://192.0.2.50:8123/api',
                       'none', 'topsecret', kind='ha')
    calls = []

    def fake(integration, command, payload, **kwargs):
        calls.append(command)
        return {
            'config/entity_registry/list': ENTITIES,
            'config/device_registry/list': DEVICES,
            'config/area_registry/list': AREAS,
            'config/label_registry/list': LABELS,
            'get_states': STATES,
        }[command]

    import core.ha_ws as ha_ws
    monkeypatch.setattr(ha_ws, 'ha_ws_request', fake)
    ha_devices.clear_cache()
    yield calls
    ha_devices.clear_cache()


def test_the_inventory_has_names_flags_and_real_areas(auth_client, inventory):
    allowances.set_protected_entities('operator', ['switch.child_lock'])
    r = auth_client.get('/api/ha/devices')
    assert r.status_code == 200, r.text
    body = r.json()['devices']
    by_id = {d['id']: d for d in body}
    assert by_id['light.lamp']['name'] == 'Reading lamp'
    assert by_id['light.lamp']['area'] == 'Kitchen'
    assert by_id['light.lamp']['labels'] == [
        {'id': 'lbl-evening', 'name': 'Evening lights'}]
    assert by_id['switch.child_lock']['settings_entity'] is True
    assert by_id['switch.child_lock']['disabled'] is True
    assert by_id['switch.child_lock']['area'] == 'Kitchen'
    assert by_id['switch.child_lock']['protected'] is True
    assert by_id['cover.garage']['risky'] is True
    assert by_id['cover.garage']['area'] == 'Outside'
    assert by_id['sensor.battery']['settings_entity'] is True
    # Names come first, then real area, then id.
    assert [d['name'] for d in body] == ['Battery', 'Child lock',
                                        'Reading lamp', 'Garage door']
    # Nothing secret is exposed.
    assert 'topsecret' not in r.text
    assert 'token' not in r.text.lower()


def test_the_labels_endpoint_and_cache(auth_client, inventory):
    labels = auth_client.get('/api/ha/labels').json()['labels']
    assert labels == [{'id': 'lbl-evening', 'name': 'Evening lights'}]
    assert len(inventory) == 5
    auth_client.get('/api/ha/devices')
    auth_client.get('/api/ha/labels')
    assert len(inventory) == 5


def test_the_device_inventory_is_operator_only(client, inventory):
    r = client.get('/api/ha/devices')
    assert r.status_code == 401
    raw, _aid = create_agent_token('household')
    r = client.get('/api/ha/devices',
                   headers={'Authorization': 'Bearer ' + raw})
    assert r.status_code in (401, 403)
