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
                x = 0, y = 0, z = 10, aim = true, edge_only = true,
                definition = {name = "Eagle Airstrike", type = 18, caption = "unused",
                    draw_rings = true, phases = {{label = "impact", innerRadius = 1,
                        outerRadius = 6, shockwaveRadius = 12}}},
            }}
            fonts = h.fonts
        ''')
        visible = renderer.draw(vm.globals().models, vm.globals().fonts)
        self.assertEqual(visible, (True, 1))
        self.assertEqual(harness.count('texts'), 0)
        self.assertGreater(harness.count('rects', 951), 0)
        self.assertFalse(vm.execute('''return (function()
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 and r.color[3] == 176 then return true end
            end
            return false
        end)()'''))
        self.assertTrue(vm.execute('''return (function()
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 and r.color[3] == 48 then return true end
            end
            return false
        end)()'''))
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

    def test_held_ball_returns_a_landing_and_heading(self):
        vm = LuaRuntime(unpack_returned_tuples=True)
        create = vm.execute((ROOT / 'src/aim_preview.lua').read_text(encoding='utf-8'))
        flight = vm.execute('return {integrate = function() return {{0, 0, 1.6}, {10, 20, 0}} end}')
        ray = vm.execute('return {trim = function() return {10, 20, 0}, "hit" end, begin = function() end, height = function() end}')
        aim = create(flight, ray, vm.execute('return {right = function() return true end}'))
        vm.execute('''
            body, ball = {}, {}
            forward = {0, 1, 0}
            local function vec(x, y, z) return {x = x, y = y, z = z} end
            sr = {
                Vector3 = {x = function(v) return v.x end, y = function(v) return v.y end, z = function(v) return v.z end},
                Matrix4x4 = {
                    translation = function() return vec(0, 0, 0) end,
                    forward = function() return vec(forward[1], forward[2], forward[3]) end,
                    right = function() return vec(1, 0, 0) end,
                    up = function() return vec(0, 0, 1) end,
                },
                IdString64 = {from_hex = function() return "beacon" end},
                World = {
                    debug_camera_pose = function() return {} end,
                    units_by_resource = function(_, resource)
                        if resource == "content/fac_helldivers/cha_avatar/avatar_helldiver" then return {body} end
                        return {ball}
                    end,
                },
                Unit = {
                    alive = function() return true end,
                    has_node = function() return true end,
                    node = function(_, name) return name end,
                    world_position = function(unit, node)
                        if unit == ball then return vec(0, 0, 1.2) end
                        if node == "r_shoulder" then return vec(0, 0, 1.6) end
                        return vec(0, 0, 1.2)
                    end,
                },
            }
        ''')
        model, why = aim.predict(vm.globals().sr, True)
        self.assertIsNone(why)
        self.assertEqual((model.x, model.y, model.z, model.aim), (10, 20, 0, True))
        self.assertAlmostEqual(model.heading.x, 0)
        self.assertAlmostEqual(model.heading.y, 1)
        vm.execute('forward = {0, 0, 1}')
        model, why = aim.predict(vm.globals().sr, True)
        self.assertIsNone(why)
        self.assertIsNone(model.heading)

    def test_aim_without_a_type_still_draws_the_cross(self):
        vm = LuaRuntime(unpack_returned_tuples=True)
        harness = vm.execute((ROOT / 'tests/spatial_engine.lua').read_text(encoding='utf-8'))
        factory = vm.execute((ROOT / 'src/spatial_renderer.lua').read_text(encoding='utf-8'))
        renderer = factory(harness.sr)
        vm.globals().h = harness
        vm.execute('models = {{x = 0, y = 0, z = 10, aim = true, edge_only = true}} fonts = h.fonts')
        visible = renderer.draw(vm.globals().models, vm.globals().fonts)
        self.assertEqual(visible, (True, 1))
        self.assertGreater(harness.count('rects', 952), 0)
        self.assertEqual(harness.count('rects', 951), 0)
        self.assertEqual(harness.count('texts'), 0)
