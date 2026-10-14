"""Jet heading policy. The memory chain is covered by the HUD integration test."""
import unittest
from pathlib import Path

from lupa.luajit21 import LuaRuntime

from tools.eagle_catalog import load_catalog, lua_literal
from tests.test_eagle_references import row


ROOT = Path(__file__).resolve().parents[1]


class EagleJets(unittest.TestCase):
    def setUp(self):
        self.vm = LuaRuntime(unpack_returned_tuples=True)
        catalog = self.vm.execute('return ' + lua_literal(load_catalog(ROOT / 'research/eagle_catalog.json')))
        self.collector = self.vm.execute((ROOT / 'src/eagle_references.lua').read_text(encoding='utf-8'))(catalog)
        self.jets = self.vm.execute((ROOT / 'src/eagle_jets.lua').read_text(encoding='utf-8'))()

    def collect(self, rows):
        return self.collector.collect(self.vm.table_from(rows, recursive=True))
    def aircraft(self, rows):
        return self.vm.table_from(rows, recursive=True)

    def test_one_jet_in_front_replaces_the_anchor_axis(self):
        models, reason = self.collect([row(30, 0, 0, 1, ax=0, ay=-20, az=1)])
        self.assertIsNone(reason)
        matched = self.jets.apply(models, self.aircraft([
            {'type': 30, 'x': -40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0}]))
        self.assertEqual(matched, 1)
        self.assertAlmostEqual(models[1].axis.x, 1)
        self.assertAlmostEqual(models[1].axis.y, 0)
        self.assertTrue(models[1].jet_heading)
        self.assertIsNone(models[1].centered)
        self.assertIsNone(models[1].track_x)
        self.assertEqual((models[1].x, models[1].y), (0, 0))

    def test_two_jets_or_a_jet_behind_leave_the_anchor_axis(self):
        models, reason = self.collect([row(30, 0, 0, 1, ax=0, ay=-20, az=1)])
        self.assertIsNone(reason)
        self.assertEqual(self.jets.apply(models, self.aircraft([
            {'type': 30, 'x': -40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0},
            {'type': 30, 'x': -30, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0},
        ])), 0)
        self.assertAlmostEqual(models[1].axis.y, 1)
        self.assertIsNone(models[1].jet_heading)
        self.assertEqual(self.jets.apply(models, self.aircraft([
            {'type': 30, 'x': 40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0},
        ])), 0)
        self.assertAlmostEqual(models[1].axis.y, 1)

    def test_a_call_off_the_nose_moves_the_run_onto_the_track(self):
        models, reason = self.collect([row(30, 0, 20, 1, ax=0, ay=0, az=1)])
        self.assertIsNone(reason)
        matched = self.jets.apply(models, self.aircraft([
            {'type': 30, 'x': -40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0}]))
        self.assertEqual(matched, 1)
        self.assertAlmostEqual(models[1].track_x, 0)
        self.assertAlmostEqual(models[1].track_y, 0)
        self.assertEqual((models[1].x, models[1].y), (0, 20))
        self.assertAlmostEqual(models[1].axis.x, 1)

    def test_an_airstrike_uses_the_nose_and_stays_centered(self):
        models, reason = self.collect([row(18, 0, 0, 1, ax=-10, ay=0, az=1)])
        self.assertIsNone(reason)
        matched = self.jets.apply(models, self.aircraft([
            {'type': 18, 'x': 0, 'y': -30, 'z': 8, 'fx': 0, 'fy': 1}]))
        self.assertEqual(matched, 1)
        self.assertAlmostEqual(models[1].axis.x, 0)
        self.assertAlmostEqual(models[1].axis.y, 1)
        self.assertTrue(models[1].centered)

    def test_a_point_blast_is_not_dragged_onto_the_jet(self):
        models, reason = self.collect([row(3, 0, 0, 1)])
        self.assertIsNone(reason)
        self.assertEqual(self.jets.apply(models, self.aircraft([
            {'type': 3, 'x': -40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0}])), 0)
        self.assertIsNone(models[1].axis)
        self.assertIsNone(models[1].jet_heading)

    def test_several_calls_turn_only_the_one_on_the_nose(self):
        models, reason = self.collect([
            row(30, 0, 0, 1, ax=0, ay=-20, az=1),
            row(30, 0, 30, 1, ax=0, ay=10, az=1),
        ])
        self.assertIsNone(reason)
        matched = self.jets.apply(models, self.aircraft([
            {'type': 30, 'x': -40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0}]))
        self.assertEqual(matched, 1)
        self.assertTrue(models[1].jet_heading)
        self.assertAlmostEqual(models[1].axis.x, 1)
        self.assertIsNone(models[2].jet_heading)
        self.assertAlmostEqual(models[2].axis.y, 1)

    def test_two_calls_on_the_same_line_keep_their_own_axes(self):
        models, reason = self.collect([
            row(30, 0, 0, 1, ax=0, ay=-20, az=1),
            row(30, 80, 0, 1, ax=80, ay=-20, az=1),
        ])
        self.assertIsNone(reason)
        self.assertEqual(self.jets.apply(models, self.aircraft([
            {'type': 30, 'x': -40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0}])), 0)
        self.assertIsNone(models[1].jet_heading)
        self.assertIsNone(models[2].jet_heading)
        self.assertAlmostEqual(models[1].axis.y, 1)
        self.assertAlmostEqual(models[2].axis.y, 1)

    def test_different_stratagems_follow_their_own_jets(self):
        models, reason = self.collect([
            row(30, 0, 0, 1, ax=0, ay=-20, az=1),
            row(18, 0, 0, 1, ax=-10, ay=0, az=1),
        ])
        self.assertIsNone(reason)
        matched = self.jets.apply(models, self.aircraft([
            {'type': 30, 'x': -40, 'y': 0, 'z': 8, 'fx': 1, 'fy': 0},
            {'type': 18, 'x': 0, 'y': -30, 'z': 8, 'fx': 0, 'fy': 1},
        ]))
        self.assertEqual(matched, 2)
        self.assertAlmostEqual(models[1].axis.x, 1)
        self.assertAlmostEqual(models[2].axis.y, 1)
        self.assertTrue(models[2].centered)


if __name__ == '__main__':
    unittest.main()
