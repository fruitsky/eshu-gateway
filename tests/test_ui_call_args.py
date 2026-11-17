"""Approval cards show an integration call's nested arguments as JSON.

Before this, a ws_write card read `payload=[object Object]`, so the operator
approved a call without seeing what it did. The harness runs the shipped
`formatCallArgs` from app.js.
"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'ui', 'call_args.cjs')
APP_JS = os.path.join(HERE, '..', 'dashboard', 'static', 'app.js')
NODE = shutil.which('node')


class TestCallArgs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest('node is not installed')
        proc = subprocess.run([NODE, HARNESS], capture_output=True, text=True,
                              timeout=30)
        if proc.returncode == 2:
            raise unittest.SkipTest(proc.stderr.strip() or 'harness unavailable')
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout)
        cls.out = json.loads(proc.stdout)

    def test_nested_payload_is_json_not_object_object(self):
        text = self.out['nested']
        self.assertNotIn('[object Object]', text)
        self.assertEqual(
            text,
            'command=config/entity_registry/update, payload='
            '{"entity_id":"switch.example_plug","labels":["example_label"]}')

    def test_plain_strings_are_unchanged(self):
        self.assertEqual(self.out['plain'], 'domain=light, service=turn_on')

    def test_lists_numbers_booleans_and_null(self):
        self.assertEqual(self.out['list_and_number'],
                         'ids=[1,2], limit=5, flag=true, none=null')

    def test_empty_and_missing(self):
        self.assertEqual(self.out['empty'], '')
        self.assertEqual(self.out['missing'], '')


class TestNoRawConcatenation(unittest.TestCase):
    def test_cards_use_the_helper(self):
        with open(APP_JS, encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn("k + '=' + d.payload[k]", src)
        self.assertNotIn("k + '=' + c.payload[k]", src)
        self.assertGreaterEqual(src.count('formatCallArgs('), 3)


class TestSessionOrder(unittest.TestCase):
    def test_session_timeline_is_newest_first(self):
        with open(APP_JS, encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('items.sort(function (a, b) { return b.ts - a.ts; });', src)
        self.assertNotIn('items.sort(function (a, b) { return a.ts - b.ts; });', src)


if __name__ == '__main__':
    unittest.main()
