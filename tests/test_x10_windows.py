"""X10: the Approved Windows modal's preset maths and preview text.

The browser code is loaded by the Node harness (the same `appjs_loader` the
other UI tests use), so the assertions are against the shipped helpers, not a
reimplementation. Run the harness under two zones: London (GMT in November)
and New York (EST), which makes the local -> UTC conversion visible.
"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'ui', 'windows_modal.cjs')
NODE = shutil.which('node')


def _run(tz):
    if not NODE:
        raise unittest.SkipTest('node is not installed')
    env = dict(os.environ)
    env['TZ'] = tz
    proc = subprocess.run([NODE, HARNESS], capture_output=True, text=True,
                          env=env, timeout=30)
    if proc.returncode == 2:
        raise unittest.SkipTest(proc.stderr.strip() or 'node harness unavailable')
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


class TestWindowsModalMaths(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.london = _run('Europe/London')
        cls.new_york = _run('America/New_York')

    def test_presets_are_15_and_60_minutes(self):
        for key, minutes in (('preset15', 15), ('preset60', 60)):
            p = self.london[key]
            self.assertEqual(p['duration_minutes'], minutes)
            self.assertEqual(p['end'] - p['start'], minutes * 60)

    def test_until_end_of_today_stops_at_2359(self):
        p = self.london['endOfDay']
        import datetime
        end = datetime.datetime.fromtimestamp(p['end'])
        self.assertEqual((end.hour, end.minute), (23, 59))
        self.assertLessEqual(p['start'], p['end'])

    def test_a_once_window_crosses_midnight(self):
        c = self.london['crossing']
        self.assertEqual(c['end'] - c['start'], 30 * 60)
        self.assertEqual(
            self.london['previews']['once'],
            'Runs once, today 23:50 to tomorrow 00:20 your time, '
            'at most 1 execution, on gateway-one.')

    def test_the_once_preview_is_the_requested_sentence(self):
        self.assertEqual(
            self.london['previews']['once_same_day'],
            'Runs once, today 22:50-23:05 your time, at most 1 execution, '
            'on gateway-one.')

    def test_daily_local_time_is_stored_as_utc_minutes(self):
        # 22:50 in London in November is 22:50 UTC; in New York it is 03:50 UTC.
        self.assertEqual(self.london['daily']['execution_time'], 22 * 60 + 50)
        self.assertEqual(self.new_york['daily']['execution_time'], 3 * 60 + 50)
        self.assertIn('22:50 UTC', self.london['previews']['daily'])
        self.assertIn('22:50 your time', self.new_york['previews']['daily'])
        self.assertIn('03:50 UTC', self.new_york['previews']['daily'])
        self.assertIn('within \u00b12 minutes', self.london['previews']['daily'])

    def test_an_old_open_ended_window_has_no_2100_range(self):
        preview = self.london['previews']['open_ended']
        self.assertEqual(
            preview,
            'Runs once, from today 22:50 your time, at most 1 execution, '
            'on gateway-one.')
        self.assertNotIn('2100', preview)

    def test_a_weekday_mask_is_spelled_out(self):
        preview = self.london['previews']['weekly']
        self.assertIn('every Mo, Tu, We, Th, Fr', preview)
        self.assertIn('at most 2 executions', preview)
        self.assertEqual(self.london['dailyWeekdays']['days_of_week'], 31)
