"""Current native snapshots -> stateless Eagle models -> retained spatial GUI.

Synthetic native bytes and engine contracts; no game process or Runtime module.
"""
import importlib.util
import json
from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('integration_catalog', ROOT / 'tools/eagle_catalog.py')
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)
CATALOG = normalizer.lua_literal(normalizer.load_catalog(ROOT / 'research/eagle_catalog.json'))
MEMORY = (ROOT / 'tests/standalone_memory.lua').read_text(encoding='utf-8')
ENGINE = (ROOT / 'tests/spatial_engine.lua').read_text(encoding='utf-8')
CONTROLLER = (ROOT / 'src/eagle_hud_probe.lua').read_text(encoding='utf-8')
RENDERER = (ROOT / 'src/spatial_renderer.lua').read_text(encoding='utf-8')
COLLECTOR = (ROOT / 'src/eagle_references.lua').read_text(encoding='utf-8')


class SpatialHudIntegration(unittest.TestCase):
    def start(self, setup=None):
        vm = LuaRuntime(unpack_returned_tuples=True)
        native = vm.execute(MEMORY)
        engine = vm.execute(ENGINE)
        vm.globals().stingray = engine.sr
        vm.globals().native_fixture = native
        vm.globals().engine_fixture = engine
        if setup:
            vm.execute(setup)
        state = vm.execute(CONTROLLER, native.factory, vm.execute(RENDERER),
                           vm.execute(COLLECTOR), vm.execute('return ' + CATALOG))
        native.tick()
        return vm, native, engine, state

    def records(self, native, event):
        parsed = [json.loads(line) for line in native.logs.values() if line.startswith('{')]
        return [record for record in parsed if record['event'] == event]

    def text(self, engine):
        return '\n'.join(part['value'] for part in engine.gui.texts.values())

    def test_projected_call_is_not_a_static_square_and_expires_with_native_record(self):
        vm, n, e, state = self.start()
        self.assertAlmostEqual(e.cross_x(), 972.5)
        self.assertIn('Eagle Strafing Run / CALL REF', self.text(e))
        self.assertIn('BASELINE', self.text(e))
        self.assertEqual(e.count('rects', 951), 192)
        n.set_active_count(0)
        n.tick()
        self.assertEqual(e.destroyed, 1)
        self.assertEqual(e.count('rects'), 0)
        self.assertEqual(e.count('texts'), 0)
        n.tick()
        self.assertEqual(e.created, 1, 'No fixed probe rectangle may return when idle')

    def test_camera_updates_between_native_polls(self):
        vm, n, e, state = self.start()
        reads, before = n.candidate_reads, e.cross_x()
        e.camera_x = 2
        n.tick(0.01)
        self.assertEqual(n.candidate_reads, reads)
        self.assertAlmostEqual(e.cross_x(), before - 20)
        self.assertEqual(e.temp, 100)

    def test_zero_game_delta_does_not_freeze_native_reference_expiry(self):
        vm, n, e, state = self.start()
        n.set_active_count(0)
        n.now += 0.2  # Real elapsed time can advance while the game's dt is zero.
        vm.execute('update(0)')
        self.assertEqual(e.destroyed, 1)
        self.assertEqual(e.count('rects'), 0)

    def test_ship_and_native_mission_identity_do_not_retain_old_markers(self):
        vm, n, e, state = self.start('native_fixture.set_state(3)')
        self.assertEqual(n.candidate_reads, 0)
        self.assertEqual(e.created, 0)
        n.begin_mission(301)
        self.assertEqual(e.created, 1)
        n.set_x(12)
        n.begin_mission(302)  # No sampled ship frame between these missions.
        self.assertEqual(e.destroyed, 1)
        self.assertAlmostEqual(e.cross_x(), 1080)
        self.assertEqual(state.epoch, 2)
        n.end_mission()
        reads = n.candidate_reads
        n.tick()
        self.assertEqual(n.candidate_reads, reads)
        self.assertEqual(e.destroyed, 2)
        self.assertEqual(e.count('rects'), 0)

    def test_death_and_new_avatar_reset_references(self):
        vm, n, e, state = self.start()
        reads = n.candidate_reads
        n.set_lifecycle(2, 32767)
        n.tick()
        self.assertEqual(n.candidate_reads, reads)
        self.assertEqual(e.destroyed, 1)
        n.set_x(8)
        n.set_lifecycle(3, 11)
        n.tick()
        self.assertEqual(e.created, 2)
        self.assertAlmostEqual(e.cross_x(), 1040)

    def test_read_failure_clears_geometry_but_font_failure_keeps_markers(self):
        vm, n, e, state = self.start()
        n.fail_active(True)
        n.tick()
        self.assertEqual(e.destroyed, 1)
        self.assertEqual(e.count('rects'), 0)
        n.fail_active(False)
        n.fail_fonts(True)
        n.tick()
        self.assertEqual(e.created, 2)
        self.assertFalse(e.gui.dead)
        self.assertGreater(e.count('rects', 952), 0)
        self.assertEqual(self.records(n, 'font_status')[-1]['data']['reason'],
                         'fallback:unreadable_or_short_read')
        self.assertTrue(all(part['font'] == 'core/performance_hud/debug'
                            for part in e.gui.texts.values()))
        n.fail_fonts(False)
        n.tick()
        self.assertEqual(e.created, 2)
        self.assertEqual(self.records(n, 'font_status')[-1]['data']['reason'], 'live')
        self.assertTrue(all(part['font'] == '1111111111111111' for part in e.gui.texts.values()))
        n.fail_state(True)
        n.tick()
        self.assertEqual(e.destroyed, 2)
        n.fail_state(False)
        n.set_player_count(5)
        n.tick()
        self.assertEqual(e.created, 2)
        self.assertEqual(e.count('rects'), 0)

    def test_squad_off_keeps_a_far_own_call_and_hides_another_avatar(self):
        vm, n, e, _ = self.start('''
            option_values = {["engle.eagle_hud.squad"] = false}
            ModOptionsMenu = {
                api = 1, version = 3,
                register_option = function(id, spec)
                    if option_values[id] == nil then option_values[id] = spec.default end
                    return true
                end,
                get = function(id) return option_values[id] end,
            }
        ''')
        self.assertGreater(e.count('rects', 951), 0)
        n.set_z(100)
        n.tick()
        self.assertEqual(e.count('rects'), 0)
        n.mark_thrower(900, 900)
        n.tick()
        self.assertGreater(e.count('rects', 951), 0)
        n.set_z(3.75)
        n.mark_thrower(900, 901)
        n.tick()
        self.assertEqual(e.count('rects'), 0)
    def test_unaligned_material_owner_keeps_the_live_font(self):
        vm, n, e, state = self.start()
        vm.execute('native_fixture.use_unaligned_font_owner()')
        n.tick()
        self.assertEqual([record['data']['reason'] for record in self.records(n, 'font_status')], ['live'])
        self.assertGreater(e.count('rects', 952), 0)
        self.assertTrue(all(part['font'] == '1111111111111111' and part['material'] == '2222222222222222'
                            for part in e.gui.texts.values()))

    def test_null_material_owner_keeps_markers_on_the_debug_font(self):
        vm, n, e, state = self.start()
        n.set_font_owner(0)
        n.tick()
        self.assertFalse(e.gui.dead)
        self.assertGreater(e.count('rects', 952), 0)
        self.assertEqual(self.records(n, 'font_status')[-1]['data']['reason'],
                         'fallback:null_pointer:0000000000000000')
        self.assertTrue(all(part['font'] == 'core/performance_hud/debug'
                            and part['material'] == 'core/performance_hud/debug'
                            for part in e.gui.texts.values()))

    def test_world_switch_between_polls_rejects_the_old_snapshot(self):
        vm, n, e, state = self.start()
        before = e.projection_calls
        vm.execute('''
            local old_overlay = engine_fixture.worlds[2]
            engine_fixture.main = {name='new_main'}
            engine_fixture.worlds = {engine_fixture.main, old_overlay}
        ''')
        n.tick(0.01)
        self.assertEqual(e.projection_calls, before)
        self.assertEqual(e.destroyed, 1)
        self.assertEqual(e.count('rects'), 0)
        n.tick(0.11)
        self.assertEqual(e.created, 2)

    def test_110mm_has_a_call_reference_but_no_impact_ring(self):
        vm, n, e, state = self.start('native_fixture.set_type(140)')
        self.assertEqual(e.count('rects', 952), 2)
        self.assertEqual(e.count('rects', 951), 0)
        self.assertIn('TARGET UNKNOWN', self.text(e))
        self.assertIn('NO IMPACT CIRCLE', self.text(e))

    def test_500kg_keeps_two_distinct_outer_radius_rings(self):
        vm, n, e, state = self.start('native_fixture.set_type(3)')
        self.assertEqual(e.count('rects', 951), 384)
        self.assertIn('impact 1/3/6', self.text(e))
        self.assertIn('expiry 10/25/35', self.text(e))

    def test_strafing_anchor_draws_a_forward_corridor(self):
        vm, n, e, state = self.start('''
            native_fixture.set_row(0, 30, 1.25, -40, 3.75)
            native_fixture.set_anchor(1.25, -60, 3.75)
        ''')
        self.assertIn('WIKI RUN ABOUT 50 m', self.text(e))
        self.assertEqual(e.count('rects', 951), 234)
        self.assertAlmostEqual(e.cross_x(), 972.5)
        self.assertTrue(vm.eval('''(function()
            local far, backward = false, false
            for _, r in pairs(engine_fixture.gui.rects) do
                if r.position[3] == 951 and r.color[2] == 255 and r.color[3] == 48 then
                    local x = r.position[1] + r.size[1] / 2
                    local y = r.position[2] + r.size[2] / 2
                    local wy = (y - 540) / 10
                    if math.abs(x - 972.5) < 0.6 and math.abs(y - 690) < 0.6 then far = true end
                    if wy < -40 - 6.5 - 0.05 then backward = true end
                end
            end
            return far and not backward
        end)()'''))
    def test_bad_build_never_reads_native_state_or_recovers_silently(self):
        vm, n, e, state = self.start('native_fixture.bad_hash=true')
        self.assertEqual(n.reads, 0)
        self.assertEqual(e.created, 0)
        n.bad_hash = False
        n.tick()
        self.assertEqual(n.reads, 0)
        self.assertEqual(len(self.records(n, 'stopped')), 1)

    def test_shutdown_cleans_only_our_gui_and_preserves_later_mod_chain(self):
        vm, n, e, state = self.start()
        vm.execute('''
            local owned_update = update
            later_mod_update = function(...) return owned_update(...) end
            update = later_mod_update
            shutdown()
        ''')
        reads = n.reads
        n.tick()
        self.assertEqual(n.reads, reads)
        self.assertEqual(e.destroyed, 1)
        self.assertTrue(n.log_closed)
        self.assertTrue(vm.eval('update == later_mod_update'))

    def test_menu_toggle_hides_one_range_without_hiding_the_rest(self):
        vm, n, e, _ = self.start('''
            option_values = {["engle.eagle_hud.inner"] = false}
            option_specs = {}
            ModOptionsMenu = {
                api = 1, version = 3,
                register_option = function(id, spec)
                    option_specs[id] = spec
                    if option_values[id] == nil then option_values[id] = spec.default end
                    return true
                end,
                get = function(id) return option_values[id] end,
            }
        ''')
        self.assertEqual(self.records(n, 'options_status')[-1]['data']['reason'], 'registered')
        self.assertEqual(vm.eval('option_specs["engle.eagle_hud.dots"].mod_id'), 'engle.eagle_hud')
        self.assertEqual(vm.eval('option_specs["engle.eagle_hud.inner"].mod'), 'Eagle HUD Reference / 飞鹰参考')
        self.assertEqual(vm.eval('option_specs["engle.eagle_hud.inner"].label'), 'Full Damage / 满伤内半径')
        self.assertTrue(vm.eval('option_specs["engle.eagle_hud.dots"].gap'))
        self.assertEqual(vm.eval('option_specs["engle.eagle_hud.mark"].type'), 'choice')
        self.assertEqual(vm.eval('option_specs["engle.eagle_hud.mark"].choices[2]'), 'Dash / 短划线')
        self.assertEqual(vm.eval('option_specs["engle.eagle_hud.samples"].min'), 8)
        self.assertEqual(vm.eval('option_specs["engle.eagle_hud.samples"].max'), 64)
        self.assertIsNone(vm.eval('option_specs["engle.eagle_hud.spread"]'))
        self.assertFalse(vm.eval('''(function()
            for _, r in pairs(engine_fixture.gui.rects) do
                if r.position[3] == 951 and r.color[3] == 176 then return true end
            end
            return false
        end)()'''))
        self.assertGreater(e.count('rects', 951), 0)
        vm.execute('option_values["engle.eagle_hud.cross"] = false')
        n.tick(0.01)
        self.assertEqual(e.count('rects', 952), 0)
        self.assertGreater(e.count('texts'), 0)
        before = e.count('rects', 951)
        vm.execute('option_values["engle.eagle_hud.samples"] = 8')
        n.tick(0.01)
        self.assertLess(e.count('rects', 951), before)
        self.assertGreater(e.count('rects', 951), 0)

    def test_manager_profile_applies_until_the_menu_value_overrides_it(self):
        vm, n, e, _ = self.start('EngleEagleHudProfile = {inner = false, mark = 4, samples = 8}')
        self.assertEqual(self.records(n, 'profile_status')[-1]['data']['reason'], 'manager')
        self.assertEqual(e.count('rects', 951), 32)
        self.assertFalse(vm.eval('''(function()
            for _, r in pairs(engine_fixture.gui.rects) do
                if r.position[3] == 951 and r.color[3] == 176 then return true end
            end
            return false
        end)()'''))
        vm.execute('''
            option_values = {}
            ModOptionsMenu = {
                api = 1, version = 3,
                register_option = function(id, spec)
                    option_values[id] = spec.default
                    return true
                end,
                get = function(id) return option_values[id] end,
            }
        ''')
        n.tick(0.01)
        self.assertEqual(vm.eval('option_values["engle.eagle_hud.inner"]'), False)
        self.assertEqual(vm.eval('option_values["engle.eagle_hud.mark"]'), 4)
        self.assertEqual(vm.eval('option_values["engle.eagle_hud.samples"]'), 8)
        self.assertEqual(e.count('rects', 951), 32)
        vm.execute('''
            option_values["engle.eagle_hud.inner"] = true
            option_values["engle.eagle_hud.mark"] = 1
            option_values["engle.eagle_hud.samples"] = 64
        ''')
        n.tick(0.01)
        self.assertEqual(e.count('rects', 951), 192)
        self.assertTrue(vm.eval('''(function()
            for _, r in pairs(engine_fixture.gui.rects) do
                if r.position[3] == 951 and r.color[3] == 176 and r.size[1] == r.size[2] then
                    return true
                end
            end
            return false
        end)()'''))

    def test_menu_that_appears_later_registers_once(self):
        vm, n, e, _ = self.start()
        self.assertEqual(self.records(n, 'options_status'), [])
        before = e.count('rects', 951)
        vm.execute('''
            ModOptionsMenu = {
                api = 1, version = 3,
                register_option = function(id, spec)
                    if id == "engle.eagle_hud.shock" then return false, "mod already has 32 options" end
                    return true
                end,
                get = function() return nil end,
            }
        ''')
        n.tick(0.01)
        self.assertEqual(self.records(n, 'options_status')[-1]['data']['reason'],
                         'refused:mod already has 32 options')
        self.assertEqual(e.count('rects', 951), before)
        n.tick(0.01)
        self.assertEqual(len(self.records(n, 'options_status')), 1)


if __name__ == '__main__':
    unittest.main()
