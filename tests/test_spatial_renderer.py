"""Consumer-level spatial rendering regressions, with no game process access.

Requires lupa.luajit21. The Lua fixture enforces temporary native lifetimes,
GUI ownership and the evidenced camera/material call signatures.
"""
from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class SpatialRenderer(unittest.TestCase):
    def start(self, setup=None):
        vm = LuaRuntime(unpack_returned_tuples=True)
        h = vm.execute((ROOT / 'tests/spatial_engine.lua').read_text(encoding='utf-8'))
        vm.globals().h = h
        if setup:
            vm.execute(setup)
        factory = vm.execute((ROOT / 'src/spatial_renderer.lua').read_text(encoding='utf-8'))
        renderer = factory(h.sr)
        self.assertNotIsInstance(renderer, tuple, 'Fixture must provide the required API')
        vm.globals().renderer = renderer
        vm.execute('models = {h.model()}; fonts = h.fonts')
        return vm, h, renderer

    def draw(self, vm, visible=1):
        result = vm.eval('renderer.draw(models, fonts)')
        self.assertEqual(result, (True, visible))
        self.assertEqual(vm.globals().h.temp, 100)
        return result

    def failed(self, vm, reason=None):
        result = vm.eval('renderer.draw(models, fonts)')
        self.assertIsNone(result[0])
        if reason is not None:
            self.assertIn(reason, result[1])
        self.assertEqual(vm.globals().h.temp, 100)
        self.assertTrue(vm.globals().h.gui.dead)

    def test_seven_worlds_select_first_nonmain_and_retain_owned_gui(self):
        vm, h, _ = self.start()
        self.draw(vm)
        self.assertTrue(vm.eval('h.gui.world == h.worlds[2]'))
        self.assertEqual(h.created, 1)
        self.draw(vm)
        self.assertEqual(h.created, 1)
        vm.execute('h.worlds[1], h.worlds[3] = h.worlds[3], h.worlds[1]')
        self.draw(vm)
        self.assertTrue(vm.eval('h.gui.world == h.worlds[1]'))
        self.assertEqual(h.destroyed, 1)

    def test_camera_reacquired_and_center_moves_without_world_change(self):
        vm, h, _ = self.start()
        self.draw(vm)
        self.assertEqual(h.cross_x(), 960)
        vm.execute('h.camera_x = 8')
        self.draw(vm)
        self.assertEqual(h.cross_x(), 880)
        self.assertEqual(h.camera_reads, 2)
        self.assertEqual(h.created, 1)
        vm.execute('models[1].x = 5')
        self.draw(vm)
        self.assertEqual(h.cross_x(), 930)

    def test_front_offscreen_and_near_clipping_clear_previous_markers(self):
        for change in ('models[1].z = -10', 'models[1].z = 0.5',
                       'models[1].x = 1000', 'models[1].y = -1000'):
            with self.subTest(change=change):
                vm, h, _ = self.start()
                self.draw(vm)
                before = h.projection_calls
                vm.execute(change)
                self.draw(vm, 0)
                self.assertTrue(h.gui.dead)
                self.assertEqual(h.count('rects'), 0)
                self.assertEqual(h.count('texts'), 0)
                if '.z' in change:
                    self.assertEqual(h.projection_calls, before)
                vm.execute('models = {h.model()}')
                self.draw(vm)
                self.assertEqual(h.created, 2)

    def test_near_plane_just_in_front_is_visible(self):
        vm, _, _ = self.start()
        vm.execute('models[1].z = 0.5001')
        self.draw(vm)


    def test_beacon_past_120m_from_the_viewpoint_is_omitted(self):
        vm, h, _ = self.start()
        vm.execute('models[1].z = 120')
        self.draw(vm)
        vm.execute('models[2] = h.model(); models[2].z = 120.1; models[1].z = 10')
        self.draw(vm, 1)
        self.assertEqual(h.cross_x(), 960)
        vm.execute('models = {h.model()}; models[1].z = 120.1')
        before = h.projection_calls
        self.draw(vm, 0)
        self.assertTrue(h.gui.dead)
        self.assertEqual(h.projection_calls, before)

    def test_configured_range_and_hidden_squad_change_the_cutoff(self):
        vm, _, _ = self.start()
        vm.execute('models[1].z = 100; options = {range = 80, squad = true}')
        self.assertEqual(vm.eval('renderer.draw(models, fonts, nil, options)'), (True, 0))
        vm.execute('options = {range = 500, squad = false}; models[1].z = 90')
        self.assertEqual(vm.eval('renderer.draw(models, fonts, nil, options)'), (True, 0))
        vm.execute('models[1].z = 70')
        self.draw_options = None
        result = vm.eval('renderer.draw(models, fonts, nil, options)')
        self.assertEqual(result, (True, 1))
        self.assertEqual(vm.eval('h.cross_x()'), 960)

    def test_an_owned_call_uses_the_configured_range_when_squad_marks_are_off(self):
        vm, _, _ = self.start()
        vm.execute('models[1].z = 90; models[1].thrower = 7; options = {range = 120, squad = false, avatar = 7}')
        self.assertEqual(vm.eval('renderer.draw(models, fonts, nil, options)'), (True, 1))

    def test_another_avatars_call_stays_hidden_beside_the_viewpoint(self):
        vm, _, _ = self.start()
        vm.execute('models[1].z = 10; models[1].thrower = 8; options = {range = 120, squad = false, avatar = 7}')
        self.assertEqual(vm.eval('renderer.draw(models, fonts, nil, options)'), (True, 0))

    def test_squad_marks_still_draw_another_avatars_nearby_call(self):
        vm, _, _ = self.start()
        vm.execute('models[1].z = 10; models[1].thrower = 8; options = {squad = true, avatar = 7}')
        self.assertEqual(vm.eval('renderer.draw(models, fonts, nil, options)'), (True, 1))
    def test_same_type_rows_remain_separate_and_shrink_deletes_surplus(self):
        vm, h, _ = self.start()
        vm.execute('models[2] = h.model(20)')
        self.draw(vm, 2)
        self.assertEqual(h.count('rects', 952), 4)
        self.assertEqual(h.count('rects', 951), 384)
        self.assertEqual(h.count('texts', 954), 4)
        vm.execute('models[1] = models[2]; models[2] = nil')
        self.draw(vm)
        self.assertEqual(h.cross_x(), 1160)
        self.assertEqual(h.count('rects'), 194)
        self.assertEqual(h.count('texts'), 10)
        self.assertEqual(h.deleted_rects, 194)
        self.assertEqual(h.deleted_texts, 10)
        self.assertEqual(h.created, 1)

    def test_110mm_has_reference_cross_but_no_impact_circle(self):
        vm, h, _ = self.start()
        self.draw(vm)
        vm.execute('''
            models[1].definition.name = 'Eagle 110mm Rocket Pods'
            models[1].definition.draw_rings = false
            models[1].definition.caption = 'TARGET UNKNOWN / NO IMPACT CIRCLE'
        ''')
        self.draw(vm)
        self.assertEqual(h.count('rects', 951), 0)
        self.assertEqual(h.count('rects', 952), 2)
        self.assertEqual(h.deleted_rects, 192)
        self.assertTrue(vm.eval('''(function()
            local header, caption = false, false
            for _, t in pairs(h.gui.texts) do
                header = header or t.value == 'Eagle 110mm Rocket Pods / CALL REF'
                caption = caption or t.value == 'TARGET UNKNOWN / NO IMPACT CIRCLE'
            end
            return header and caption
        end)()'''))

    def test_500kg_draws_inner_outer_and_shockwave(self):
        vm, h, _ = self.start()
        vm.execute('''
            models[1].definition.name = 'Eagle 500kg Bomb'
            models[1].definition.caption = 'BASELINE impact I/O/S 1/3/6 m; expiry I/O/S 10/25/35 m'
            models[1].definition.phases[2] = {label = 'expiry', innerRadius = 10,
                outerRadius = 25, shockwaveRadius = 35}
        ''')
        self.draw(vm)
        self.assertEqual(h.count('rects', 951), 384)
        self.assertTrue(vm.eval('''(function()
            local counts = {}
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 then
                    local x = r.position[1] + r.size[1] / 2 - 960
                    local y = r.position[2] + r.size[2] / 2 - 540
                    local radius = math.floor(math.sqrt(x*x + y*y) + 0.5)
                    counts[radius] = (counts[radius] or 0) + 1
                end
            end
            return counts[10] == 64 and counts[30] == 64 and counts[60] == 64
                and counts[100] == 64 and counts[250] == 64 and counts[350] == 64
        end)()'''))


    def test_strafing_corridor_extends_past_the_beacon_not_back_to_the_anchor(self):
        vm, h, _ = self.start()
        vm.execute('''
            models[1].x, models[1].y = 0, -20
            models[1].axis = {x = 0, y = 1}
            models[1].runMeters = 50
            models[1].definition.name = 'Eagle Strafing Run'
            models[1].definition.phases = {{label = 'impact', innerRadius = 2.5,
                outerRadius = 5, shockwaveRadius = 6.5}}
        ''')
        self.draw(vm)
        self.assertEqual(h.count('rects', 951), 234)
        self.assertTrue(vm.eval('''(function()
            local far, backward = false, false
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 and r.color[2] == 255 and r.color[3] == 48 then
                    local x = r.position[1] + r.size[1] / 2
                    local y = r.position[2] + r.size[2] / 2
                    local wx, wy = (x - 960) / 10, (y - 540) / 10
                    if math.abs(wx) < 0.05 and math.abs(wy - 35) < 0.05 then far = true end
                    if wy < -20 - 6.5 - 0.05 then backward = true end
                end
            end
            return far and not backward
        end)()'''))

    def test_smoke_draws_separate_green_circles_not_a_filled_capsule(self):
        vm, h, _ = self.start()
        vm.execute('''
            models[1].axis = {x = 1, y = 0}
            models[1].centered = true
            models[1].runMeters = 168
            models[1].definition.name = 'Eagle Smoke Strike'
            models[1].definition.type = 38
            models[1].definition.bursts = 8
            models[1].definition.tint = 'green'
            models[1].definition.phases = {{label = 'impact', innerRadius = 12,
                outerRadius = 12, shockwaveRadius = 0}}
        ''')
        self.draw(vm)
        self.assertTrue(vm.eval('''(function()
            local on_bomb, in_gap, red = false, false, false
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 then
                    if r.color[2] == 255 and r.color[3] == 48 then red = true end
                    if r.color[2] == 80 and r.color[3] == 220 and r.color[4] == 70 then
                        local x = r.position[1] + r.size[1] / 2
                        local y = r.position[2] + r.size[2] / 2
                        local wx, wy = (x - 960) / 10, (y - 540) / 10
                        if math.abs(wx - 12) < 0.2 and math.abs(wy - 12) < 0.2 then on_bomb = true end
                        if math.abs(wx) < 0.2 and math.abs(wy - 12) < 0.2 then in_gap = true end
                    end
                end
            end
            return on_bomb and not in_gap and not red
        end)()'''))
    def test_marker_stays_on_the_world_point_when_subtraction_reuses_the_vector(self):
        vm, h, _ = self.start()
        vm.execute('''
            h.clobber_left_on_sub = true
            h.camera_x, h.camera_y, h.camera_z = 100, -40, 2
            models[1].x, models[1].y, models[1].z = 100, -40, 12
        ''')
        self.draw(vm)
        self.assertEqual(h.cross_x(), 960)
        self.assertTrue(vm.eval('''(function()
            local spread = false
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 then
                    local x = r.position[1] + r.size[1] / 2 - 960
                    local y = r.position[2] + r.size[2] / 2 - 540
                    if math.floor(math.sqrt(x * x + y * y) + 0.5) == 30 then spread = true end
                end
            end
            return spread
        end)()'''))

    def test_center_stuck_camera_loses_to_the_pose_matched_camera(self):
        vm, h, _ = self.start()
        vm.execute('''
            h.dummy_center_camera = true
            h.camera_x = 30
            models[1].x = 30
        ''')
        self.draw(vm)
        self.assertEqual(h.cross_x(), 960)
        self.assertTrue(vm.eval('''(function()
            local spread = false
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 then
                    local x = r.position[1] + r.size[1] / 2 - 960
                    local y = r.position[2] + r.size[2] / 2 - 540
                    if math.floor(math.sqrt(x * x + y * y) + 0.5) == 30 then spread = true end
                end
            end
            return spread
        end)()'''))

    def test_transient_camera_failure_clears_and_recovers(self):
        vm, h, _ = self.start()
        self.draw(vm)
        vm.execute('h.fail_camera = true')
        self.failed(vm, 'camera_unavailable')
        vm.execute('h.fail_camera = false')
        self.draw(vm)
        self.assertEqual(h.created, 2)

    def test_font_failures_keep_geometry_and_recover_labels(self):
        for change, undo in (
            ('fonts = nil', 'fonts = h.fonts'),
            ("fonts.font = '0000000000000000'", "fonts.font = '1111111111111111'"),
            ('h.fail_font = true', 'h.fail_font = false'),
        ):
            with self.subTest(change=change):
                vm, h, _ = self.start()
                self.draw(vm)
                vm.execute(change)
                self.draw(vm)
                self.assertFalse(h.gui.dead)
                self.assertGreater(h.count('rects', 952), 0)
                self.assertEqual(h.count('texts'), 0)
                vm.execute(undo)
                self.draw(vm)
                self.assertGreater(h.count('texts'), 0)
                self.assertEqual(h.created, 1)
    def test_temp_values_expire_each_frame_and_font_changes_rebind(self):
        vm, h, _ = self.start()
        for _ in range(3):
            self.draw(vm)
        self.assertEqual(h.bound, 1)
        vm.execute("fonts.font = '4444444444444444'; fonts.atlas = '5555555555555555'")
        self.draw(vm)
        self.assertEqual(h.bound, 2)
        self.assertEqual(h.bound_atlas, '5555555555555555')
        self.assertTrue(vm.eval('''(function()
            for _, t in pairs(h.gui.texts) do
                if t.font ~= fonts.font or t.material ~= fonts.material then return false end
            end
            return true
        end)()'''))
        vm.execute('h.fail_update = true')
        self.failed(vm, 'primitive update failed')
        vm.execute('h.fail_update = false')
        self.draw(vm)

    def test_dead_world_handles_discarded_without_native_access(self):
        vm, h, renderer = self.start()
        self.draw(vm)
        vm.execute('old_gui = h.gui; h.worlds = {h.main, {}}')
        self.draw(vm)
        self.assertEqual(h.destroyed, 0)
        self.assertEqual(h.created, 2)
        self.assertTrue(vm.eval('h.gui ~= old_gui'))
        vm.execute('h.worlds = {}')
        self.assertTrue(renderer.clear())
        self.assertEqual(h.destroyed, 0)
        self.assertEqual(h.temp, 100)

    def test_main_world_change_reacquires_camera_and_overlay(self):
        vm, h, _ = self.start()
        self.draw(vm)
        vm.execute('h.main = {}; h.worlds = {h.main, {}}; h.camera_x = -9')
        self.draw(vm)
        self.assertEqual(h.cross_x(), 1050)
        self.assertEqual(h.camera_reads, 2)
        self.assertEqual(h.destroyed, 0)

    def test_menu_empty_models_and_explicit_clear_release_gui(self):
        vm, h, renderer = self.start()
        self.draw(vm)
        vm.execute('h.menu = true')
        self.draw(vm, 0)
        self.assertTrue(h.gui.dead)
        vm.execute('h.menu = false')
        self.draw(vm)
        vm.execute('models = {}')
        self.draw(vm, 0)
        self.assertTrue(h.gui.dead)
        self.assertTrue(renderer.clear())
        self.assertEqual(h.destroyed, 2)
        vm.execute('models = {h.model()}')
        self.draw(vm)
        self.assertTrue(renderer.clear())
        self.assertEqual(h.destroyed, 3)
        self.assertTrue(renderer.clear())
        self.assertEqual(h.temp, 100)

    def test_stamped_snapshot_cannot_cross_main_world_transition(self):
        vm, h, _ = self.start()
        vm.execute('snapshot_world = h.main')
        self.assertEqual(vm.eval('renderer.draw(models, fonts, snapshot_world)'), (True, 1))
        projections = h.projection_calls
        vm.execute('h.main = {}; h.worlds[1] = h.main')
        result = vm.eval('renderer.draw(models, fonts, snapshot_world)')
        self.assertIsNone(result[0])
        self.assertIn('main_world_changed', result[1])
        self.assertTrue(h.gui.dead)
        self.assertEqual(h.projection_calls, projections)
        self.assertEqual(h.destroyed, 1)
        self.assertEqual(h.temp, 100)
        vm.execute('snapshot_world = h.main; models = {h.model(8)}')
        self.assertEqual(vm.eval('renderer.draw(models, fonts, snapshot_world)'), (True, 1))
        self.assertEqual(h.cross_x(), 1040)
        self.assertEqual(h.created, 2)

    def test_optional_menu_api_absence_does_not_disable_rendering(self):
        vm, _, _ = self.start('h.sr.Window = nil')
        self.draw(vm)

    def test_missing_projection_api_reports_unavailable_without_engine_calls(self):
        vm = LuaRuntime(unpack_returned_tuples=True)
        h = vm.execute((ROOT / 'tests/spatial_engine.lua').read_text(encoding='utf-8'))
        h.sr.Camera.world_to_screen = None
        factory = vm.execute((ROOT / 'src/spatial_renderer.lua').read_text(encoding='utf-8'))
        renderer, reason = factory(h.sr)
        self.assertIsNone(renderer)
        self.assertIn('Camera.world_to_screen', reason)
        self.assertEqual(h.created, 0)
        self.assertEqual(h.temp, 100)

    def test_bad_constructor_restores_pool_and_destroys_partial_gui(self):
        vm, h, _ = self.start('h.sr.Vector2 = {}')
        self.failed(vm, 'spatial_unavailable:')
        self.assertEqual(h.created, 1)
        self.assertEqual(h.destroyed, 1)

    def test_sixteen_records_supported_but_seventeen_fail_closed(self):
        vm, h, _ = self.start()
        vm.execute('for n = 1, 16 do models[n] = h.model(n) end')
        self.draw(vm, 16)
        self.assertEqual(h.count('rects', 952), 32)
        vm.execute('models[17] = h.model(17)')
        self.failed(vm, 'render_limit:16')

    def test_invalid_geometry_and_projection_fail_closed(self):
        for change in ('models[1].x = 0/0', 'models[1].z = math.huge',
                       'models[1].definition.phases[1].outerRadius = math.huge',
                       'models[1].definition.phases[1].shockwaveRadius = 0/0',
                       'models[1].definition.phases[1].outerRadius = -1',
                       'h.bad_projection = true'):
            with self.subTest(change=change):
                vm, _, _ = self.start()
                self.draw(vm)
                vm.execute(change)
                self.failed(vm)

    def test_labels_and_ring_dots_stay_inside_screen_at_edges(self):
        vm, _, _ = self.start()
        vm.execute('h.width = 640; h.height = 360; models[1].x = 31.9; models[1].y = 17.9')
        self.draw(vm)
        self.assertTrue(vm.eval('''(function()
            for _, t in pairs(h.gui.texts) do
                if t.position[1] < 0 or t.position[2] < 0
                    or t.position[1] + #t.value * t.size * 0.6 > h.width
                    or t.position[2] + t.size > h.height then return false end
            end
            for _, r in pairs(h.gui.rects) do
                if r.position[1] < 0 or r.position[2] < 0
                    or r.position[1] + r.size[1] > h.width
                    or r.position[2] + r.size[2] > h.height then return false end
            end
            return true
        end)()'''))

    def test_temporary_pool_failure_clears_gui_and_stops_further_native_drawing(self):
        for operation in ('temp_byte_count', 'set_temp_byte_count'):
            with self.subTest(operation=operation):
                vm, h, _ = self.start()
                self.draw(vm)
                vm.execute('h.sr.Script.' + operation + ' = function() error("pool unavailable") end')
                failed = vm.eval('renderer.draw(models, fonts)')
                self.assertIsNone(failed[0])
                self.assertTrue(h.gui.dead)
                projections, created = h.projection_calls, h.created
                failed = vm.eval('renderer.draw(models, fonts)')
                self.assertIsNone(failed[0])
                self.assertEqual(h.projection_calls, projections)
                self.assertEqual(h.created, created)

    def radii_at(self, vm):
        return vm.eval('''(function()
            local counts = {}
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 then
                    local x = r.position[1] + r.size[1] / 2 - 960
                    local y = r.position[2] + r.size[2] / 2 - 540
                    local radius = math.floor(math.sqrt(x * x + y * y) + 0.5)
                    counts[radius] = (counts[radius] or 0) + 1
                end
            end
            return counts
        end)()''')

    def test_each_radius_toggle_removes_only_that_circle(self):
        vm, h, _ = self.start()
        vm.execute('''
            models[1].definition.phases[2] = {label = 'expiry', innerRadius = 10,
                outerRadius = 25, shockwaveRadius = 35}
            options = {inner = true, outer = true, shock = true}
        ''')
        self.draw(vm)
        self.assertEqual(h.count('rects', 951), 384)
        for key, gone in (('inner', (10, 100)), ('outer', (30, 250)), ('shock', (60, 350))):
            with self.subTest(key=key):
                vm.execute(f'options.{key} = false')
                result = vm.eval('renderer.draw(models, fonts, nil, options)')
                self.assertEqual(result, (True, 1))
                counts = self.radii_at(vm)
                for radius in gone:
                    self.assertIsNone(counts[radius])
                self.assertEqual(h.count('rects', 951), 384 - 128)
                vm.execute(f'options.{key} = true')

    def test_style_toggles_remove_dots_cross_or_labels(self):
        vm, h, _ = self.start()
        vm.execute('options = {dots = false, cross = true, labels = true}')
        result = vm.eval('renderer.draw(models, fonts, nil, options)')
        self.assertEqual(result, (True, 1))
        self.assertEqual(h.count('rects', 951), 0)
        self.assertEqual(h.count('rects', 952), 2)
        self.assertGreater(h.count('texts'), 0)
        vm.execute('options = {dots = true, cross = false, labels = true}')
        result = vm.eval('renderer.draw(models, fonts, nil, options)')
        self.assertEqual(result, (True, 1))
        self.assertEqual(h.count('rects', 952), 0)
        self.assertGreater(h.count('rects', 951), 0)
        self.assertGreater(h.count('texts'), 0)
        vm.execute('options = {dots = true, cross = true, labels = false}')
        result = vm.eval('renderer.draw(models, fonts, nil, options)')
        self.assertEqual(result, (True, 1))
        self.assertEqual(h.count('texts'), 0)
        self.assertEqual(h.count('rects', 952), 2)
        vm.execute('options = {dots = false, cross = false, labels = false, inner = false, outer = false, shock = false}')
        result = vm.eval('renderer.draw(models, fonts, nil, options)')
        self.assertEqual(result, (True, 0))
        self.assertTrue(h.gui.dead)
        self.assertEqual(h.count('rects'), 0)

    def test_boundary_styles_change_the_outer_mark(self):
        vm, h, _ = self.start()
        for mark, wide in ((2, False), (3, True), (4, None)):
            with self.subTest(mark=mark):
                vm.execute(f'options = {{mark = {mark}}}')
                result = vm.eval('renderer.draw(models, fonts, nil, options)')
                self.assertEqual(result, (True, 1))
                shape = vm.eval('''(function()
                    local best, wide_bar, tall_bar, crosses = nil, false, false, 0
                    for _, r in pairs(h.gui.rects) do
                        if r.position[3] == 951 and r.color[2] == 255 and r.color[3] == 48 then
                            local cx = r.position[1] + r.size[1] / 2
                            if not best or cx > best then best = cx end
                        end
                    end
                    for _, r in pairs(h.gui.rects) do
                        if r.position[3] == 951 and r.color[2] == 255 and r.color[3] == 48 then
                            local cx = r.position[1] + r.size[1] / 2
                            if math.abs(cx - best) < 0.6 then
                                crosses = crosses + 1
                                if r.size[1] > r.size[2] then wide_bar = true end
                                if r.size[2] > r.size[1] then tall_bar = true end
                            end
                        end
                    end
                    return wide_bar, tall_bar, crosses
                end)()''')
                wide_bar, tall_bar, crosses = shape
                if mark == 4:
                    self.assertEqual(h.count('rects', 951), 384)
                    self.assertGreaterEqual(crosses, 2)
                elif wide:
                    self.assertTrue(wide_bar)
                    self.assertFalse(tall_bar)
                    self.assertEqual(h.count('rects', 951), 192)
                else:
                    self.assertTrue(tall_bar)
                    self.assertFalse(wide_bar)
                    self.assertEqual(h.count('rects', 951), 192)

    def test_fewer_samples_keep_the_strafing_reach_and_thin_the_marks(self):
        vm, h, _ = self.start()
        vm.execute('''
            models[1].x, models[1].y = 0, -20
            models[1].axis = {x = 0, y = 1}
            models[1].runMeters = 50
            models[1].definition.phases = {{label = 'impact', innerRadius = 2.5,
                outerRadius = 5, shockwaveRadius = 6.5}}
            options = {samples = 16}
        ''')
        result = vm.eval('renderer.draw(models, fonts, nil, options)')
        self.assertEqual(result, (True, 1))
        self.assertEqual(h.count('rects', 951), 54)
        self.assertTrue(vm.eval('''(function()
            local far = false
            for _, r in pairs(h.gui.rects) do
                if r.position[3] == 951 and r.color[2] == 255 and r.color[3] == 48 then
                    local x = r.position[1] + r.size[1] / 2
                    local y = r.position[2] + r.size[2] / 2
                    local wx, wy = (x - 960) / 10, (y - 540) / 10
                    if math.abs(wx) < 0.05 and math.abs(wy - 35) < 0.05 then far = true end
                end
            end
            return far
        end)()'''))
        vm.execute('options = {samples = 0/0}')
        result = vm.eval('renderer.draw(models, fonts, nil, options)')
        self.assertEqual(result, (True, 1))
        self.assertEqual(h.count('rects', 951), 234)


if __name__ == '__main__':
    unittest.main()
