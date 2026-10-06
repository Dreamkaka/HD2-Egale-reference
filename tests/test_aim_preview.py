"""Beacon arc clipping and the separate aim marker. No game process."""
from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class AimPreview(unittest.TestCase):
    def flight(self):
        vm = LuaRuntime(unpack_returned_tuples=True)
        loaded = vm.execute((ROOT / 'src/beacon_flight.lua').read_text(encoding='utf-8'))
        return vm, loaded

    def test_beacon_arc_falls_and_clip_keeps_the_surface_point(self):
        vm, flight = self.flight()
        points = flight.integrate(
            vm.execute('return {0, 0, 1.5}'), vm.execute('return {0, 1, 0}'),
            vm.execute('return {1, 0, 0}'), vm.execute('return {0, 0, 1}'))
        self.assertGreaterEqual(len(points), 2)
        self.assertLess(points[len(points)][3], points[1][3])
        self.assertGreater(points[len(points)][2], points[1][2])
        hit, path = flight.clip(points, vm.execute('''return function(ax, ay, az, bx, by, bz)
            if az > 0 and bz <= 0 then return az / (az - bz) end
        end'''))
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[3], 0, places=4)
        self.assertEqual(path[len(path)][3], hit[3])

    def test_clear_ray_does_not_invent_a_landing(self):
        vm, flight = self.flight()
        empty = vm.execute('return {{0, 0, 2}, {0, 10, 2}}')
        hit, why = flight.clip(empty, vm.execute('return function() return nil end'))
        self.assertIsNone(hit)
        self.assertEqual(why, 'clear')

    def test_aim_marker_is_a_short_arc_and_cross_without_call_text(self):
        vm = LuaRuntime(unpack_returned_tuples=True)
        harness = vm.execute((ROOT / 'tests/spatial_engine.lua').read_text(encoding='utf-8'))
        factory = vm.execute((ROOT / 'src/spatial_renderer.lua').read_text(encoding='utf-8'))
        renderer = factory(harness.sr)
        vm.globals().h = harness
        vm.execute('''
            models = {{
                x = 0, y = 8, z = 10, aim = true, heading = {x = 0, y = 1},
                path = {{0, 0, 12}, {0, 4, 11}, {0, 8, 10}},
                definition = {name = "Eagle Airstrike", type = 18, caption = "unused",
                    draw_rings = true, phases = {{label = "impact", outerRadius = 6}}},
            }}
            fonts = h.fonts
        ''')
        visible = renderer.draw(vm.globals().models, vm.globals().fonts)
        self.assertEqual(visible, (True, 1))
        self.assertEqual(harness.count('texts'), 0)
        self.assertEqual(harness.temp, 100)

    def test_range_dots_use_sampled_ground_height(self):
        vm = LuaRuntime(unpack_returned_tuples=True)
        harness = vm.execute((ROOT / 'tests/spatial_engine.lua').read_text(encoding='utf-8'))
        factory = vm.execute((ROOT / 'src/spatial_renderer.lua').read_text(encoding='utf-8'))
        renderer = factory(harness.sr)
        vm.globals().h = harness
        vm.globals().renderer = renderer
        vm.execute('''
            h.record_z = true
            models = {h.model(0, 3)}
            fonts = h.fonts
            options = {surface = function(x, y, z) return z + 15 end}
        ''')
        visible = renderer.draw(vm.globals().models, vm.globals().fonts, None, vm.globals().options)
        self.assertEqual(visible[1], True)
        heights = vm.execute('return h.ground_z')
        self.assertGreater(len(heights), 0)
        for index in range(1, len(heights) + 1):
            self.assertAlmostEqual(heights[index], 25, places=3)
        self.assertEqual(harness.temp, 100)
