"""Snapshot-reference behavior over the production collector in LuaJIT.

Run with the project's unittest discovery; no game process or assets are loaded.
"""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from lupa.luajit21 import LuaRuntime

from tools.eagle_catalog import load_catalog, lua_literal


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / 'research/eagle_catalog.json'
COLLECTOR_PATH = ROOT / 'src/eagle_references.lua'


def row(native_type=3, x=1, y=2, z=3, index=0, ax=None, ay=None, az=None):
    data = {'index': index, 'native_type_candidate': native_type, 'x': x, 'y': y, 'z': z}
    if ax is not None or ay is not None or az is not None:
        data.update(ax=ax, ay=ay, az=az)
    return data


class EagleReferences(unittest.TestCase):
    def setUp(self):
        self.vm = LuaRuntime(unpack_returned_tuples=True)
        self.catalog = self.vm.execute('return ' + lua_literal(load_catalog(CATALOG_PATH)))
        self.collector = self.vm.execute(COLLECTOR_PATH.read_text(encoding='utf-8'))(self.catalog)

    def collect(self, rows):
        return self.collector.collect(self.vm.table_from(rows, recursive=True))

    def test_mixed_rows_keep_only_catalog_types_and_plain_coordinates(self):
        models, reason = self.collect([
            row(124), row(3, 12, -8, 4, index=7), row(None), row('3'),
            row(140, -2, 5, 1, index=11), {'native_type_candidate': 999},
        ])
        self.assertIsNone(reason)
        self.assertEqual(len(models), 2)
        self.assertEqual(set(models[1].keys()), {'x', 'y', 'z', 'definition'})
        self.assertEqual((models[1].x, models[1].y, models[1].z), (12, -8, 4))
        self.assertEqual(models[1].definition.type, 3)
        self.assertEqual(models[2].definition.type, 140)
        self.assertFalse(models[2].definition.draw_rings)
        same = self.vm.eval('function(a,b) return a == b end')
        self.assertTrue(same(models[1].definition, self.catalog.entries[3]))

    def test_duplicates_and_compaction_never_establish_identity(self):
        rows = self.vm.table_from([row(3, 1, index=2), row(3, 1, index=3),
                                   row(18, 9, index=4)], recursive=True)
        before, reason = self.collector.collect(rows)
        self.assertIsNone(reason)
        self.assertEqual(len(before), 3, 'Same-type and same-position rows must not merge')
        rows[1].x = 44
        self.assertEqual(before[1].x, 1, 'Coordinates must be copied, not borrowed from mutable rows')
        after, reason = self.collect([row(18, 9, index=0), row(3, 7, index=1)])
        self.assertIsNone(reason)
        self.assertEqual(len(after), 2)
        self.assertEqual([after[i].definition.type for i in (1, 2)], [18, 3])
        self.assertEqual([after[i].x for i in (1, 2)], [9, 7])
        for model in after.values():
            self.assertEqual(set(model.keys()), {'x', 'y', 'z', 'definition'})
        empty, reason = self.collect([])
        self.assertIsNone(reason)
        self.assertEqual(len(empty), 0)

    def test_empty_unknown_and_non_eagle_snapshots_are_successfully_empty(self):
        for rows in ([], [row(124)], [{}, False, 'unknown', row('3'), row(None)]):
            with self.subTest(rows=rows):
                models, reason = self.collect(rows)
                self.assertIsNone(reason)
                self.assertEqual(len(models), 0)

    def test_unavailable_snapshot_does_not_reuse_prior_models(self):
        self.collect([row()])
        for unavailable in (None, False, 'unavailable', 42):
            with self.subTest(unavailable=unavailable):
                models, reason = self.collector.collect(unavailable)
                self.assertIsNone(models)
                self.assertEqual(reason, 'reference_snapshot_unavailable')
        models, reason = self.collect([])
        self.assertIsNone(reason)
        self.assertEqual(len(models), 0)

    def test_bad_matching_coordinates_fail_entire_snapshot(self):
        for field in ('x', 'y', 'z'):
            for bad in (None, False, '4', math.nan, math.inf, -math.inf, 100000.1, -100000.1):
                with self.subTest(field=field, bad=bad):
                    invalid = row(18)
                    invalid[field] = bad
                    models, reason = self.collect([row(3), invalid])
                    self.assertIsNone(models)
                    self.assertEqual(reason, 'reference_coordinates_invalid')
        models, reason = self.collect([row(3, -100000, 100000, 0), row(999, math.nan)])
        self.assertIsNone(reason)
        self.assertEqual(len(models), 1)

    def test_sparse_snapshot_never_silently_truncates(self):
        sparse = self.vm.table_from({1: row(), 3: row(18)}, recursive=True)
        models, reason = self.collector.collect(sparse)
        self.assertIsNone(models)
        self.assertEqual(reason, 'reference_snapshot_malformed')

    def test_strict_render_cap_counts_all_matches_including_duplicates(self):
        models, reason = self.collect([row() for _ in range(16)] + [row(999) for _ in range(32)])
        self.assertIsNone(reason)
        self.assertEqual(len(models), 16)
        models, reason = self.collect([row() for _ in range(17)])
        self.assertIsNone(models)
        self.assertEqual(reason, 'reference_render_limit:16')
        models, reason = self.collect([row(999), row()])
        self.assertIsNone(reason)
        self.assertEqual(len(models), 1)

    def test_strafing_axis_runs_from_anchor_through_the_beacon(self):
        models, reason = self.collect([row(30, 10, 0, 1, ax=0, ay=0, az=1)])
        self.assertIsNone(reason)
        self.assertAlmostEqual(models[1].axis.x, 1)
        self.assertAlmostEqual(models[1].axis.y, 0)
        self.assertEqual(models[1].runMeters, 50)
        models, reason = self.collect([row(30, 10, 0, 1, ax=20, ay=0, az=1)])
        self.assertAlmostEqual(models[1].axis.x, -1)
        self.assertAlmostEqual(models[1].axis.y, 0)
        for sample in (
            row(3, 10, 0, 1, ax=0, ay=0, az=1),
            row(30, 10, 0, 1, ax=9.5, ay=0, az=1),
            row(30, 0, 0, 1, ax=100, ay=0, az=1),
            row(30, 10, 0, 1),
        ):
            models, reason = self.collect([sample])
            self.assertIsNone(reason)
            self.assertIsNone(models[1].axis)
            self.assertIsNone(models[1].runMeters)
        models, reason = self.collect([row(30, 10, 0, 1, ax=1e9, ay=0, az=1)])
        self.assertIsNone(models)
        self.assertEqual(reason, 'reference_coordinates_invalid')

    def test_carpet_axis_is_across_the_throw_and_centered(self):
        for native_type, span in ((18, 100), (38, 72), (65, 84), (126, 72), (133, 80)):
            models, reason = self.collect([row(native_type, 10, 0, 1, ax=0, ay=0, az=1)])
            self.assertIsNone(reason)
            self.assertAlmostEqual(models[1].axis.x, 0)
            self.assertAlmostEqual(models[1].axis.y, 1)
            self.assertTrue(models[1].centered)
            self.assertEqual(models[1].runMeters, span)
class EagleCatalog(unittest.TestCase):
    def setUp(self):
        self.source = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))

    def normalize(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(source), encoding='utf-8')
            return load_catalog(path)

    def test_pinned_baselines_names_and_all_paths_survive(self):
        catalog = load_catalog(CATALOG_PATH)
        self.assertEqual(set(catalog), {'source_snapshot', 'source_version', 'entries'})
        self.assertEqual(catalog['source_version'], '0.28.1')
        self.assertEqual(catalog['source_snapshot'], self.source['sourceSnapshot'])
        self.assertEqual(set(catalog['entries']), {3, 18, 30, 38, 65, 126, 133, 140})
        phase_count = 0
        for original in self.source['stratagems']:
            definition = catalog['entries'][original['nativeTypeValue']]
            expected = {'name', 'type', 'caption', 'draw_rings', 'phases'}
            if original['nativeTypeValue'] == 30:
                expected.add('runMeters')
                self.assertEqual(definition['runMeters'], 50)
                self.assertIn('WIKI RUN ABOUT 50 m FROM BEACON PAST THE ANCHOR', definition['caption'])
            if original['nativeTypeValue'] == 18:
                expected.update({'runMeters', 'pattern'})
                self.assertEqual(definition['pattern'], 'across')
                self.assertEqual(definition['runMeters'], 100)
                self.assertIn('6 BURSTS ACROSS THE THROW', definition['caption'])
            if original['nativeTypeValue'] == 133:
                expected.update({'runMeters', 'pattern'})
                self.assertEqual(definition['pattern'], 'across')
                self.assertEqual(definition['runMeters'], 80)
                self.assertIn('5 BURSTS ACROSS THE THROW', definition['caption'])
            if original['nativeTypeValue'] == 38:
                expected.update({'runMeters', 'pattern', 'bursts', 'tint'})
                self.assertEqual(definition['pattern'], 'across')
                self.assertEqual(definition['runMeters'], 72)
                self.assertEqual(definition['bursts'], 4)
                self.assertEqual(definition['tint'], 'green')
                self.assertIn('4 BURSTS ACROSS THE THROW', definition['caption'])
            if original['nativeTypeValue'] == 126:
                expected.update({'runMeters', 'pattern'})
                self.assertEqual(definition['pattern'], 'across')
                self.assertEqual(definition['runMeters'], 72)
                self.assertIn('4 BURSTS ACROSS THE THROW', definition['caption'])
            if original['nativeTypeValue'] == 65:
                expected.update({'runMeters', 'pattern'})
                self.assertEqual(definition['pattern'], 'across')
                self.assertEqual(definition['runMeters'], 84)
                self.assertIn('8 BURSTS ACROSS THE THROW', definition['caption'])
            self.assertEqual(set(definition), expected)
            self.assertEqual(definition['name'], original['name'])
            self.assertEqual(definition['type'], original['nativeTypeValue'])
            self.assertIn('BASELINE I/O/S m:', definition['caption'])
            self.assertIn('NOT A KILL/SAFE BOUNDARY', definition['caption'])
            self.assertTrue(definition['caption'].isascii())
            self.assertEqual([phase['path'] for phase in definition['phases']],
                             [phase['path'] for phase in original['phases']])
            for normalized, phase in zip(definition['phases'], original['phases']):
                self.assertEqual([normalized[field] for field in
                                  ('innerRadius', 'outerRadius', 'shockwaveRadius')],
                                 [phase['radiiMeters'][field] for field in ('inner', 'outer', 'shockwave')])
            phase_count += len(definition['phases'])
        self.assertEqual(phase_count, 10)

    def test_500kg_and_cluster_phases_are_distinct_and_110mm_has_no_circle(self):
        entries = load_catalog(CATALOG_PATH)['entries']
        bomb = entries[3]['phases']
        self.assertEqual([phase['label'] for phase in bomb], ['impact', 'expiry'])
        self.assertEqual([(p['innerRadius'], p['outerRadius'], p['shockwaveRadius']) for p in bomb],
                         [(1, 3, 6), (10, 25, 35)])
        self.assertEqual([p['label'] for p in entries[65]['phases']], ['expiry', 'fragment impact'])
        self.assertFalse(entries[140]['draw_rings'])
        self.assertIn('TARGET UNKNOWN / NO IMPACT CIRCLE', entries[140]['caption'])
        self.assertEqual(entries[140]['phases'][0]['outerRadius'], 5)
        self.assertTrue(all(entry['draw_rings'] for key, entry in entries.items() if key != 140))

    def test_unknown_radii_remain_none_and_lua_nil_not_zero(self):
        rejected = copy.deepcopy(self.source)
        gas = next(row for row in rejected['stratagems'] if row['nativeTypeValue'] == 126)
        gas['phases'][0]['radiiMeters'] = {'inner': None, 'shockwave': 0}
        with self.assertRaises(ValueError):
            self.normalize(rejected)
        rocket = next(row for row in self.source['stratagems'] if row['nativeTypeValue'] == 140)
        rocket['phases'][0]['radiiMeters'] = {'inner': None, 'shockwave': 0}
        catalog = self.normalize(self.source)
        normalized = catalog['entries'][140]['phases'][0]
        self.assertIsNone(normalized['innerRadius'])
        self.assertIsNone(normalized['outerRadius'])
        self.assertEqual(normalized['shockwaveRadius'], 0)
        self.assertIn('?/?/0', catalog['entries'][140]['caption'])
        vm = LuaRuntime()
        lua_phase = vm.execute('return ' + lua_literal(catalog)).entries[140].phases[1]
        self.assertIsNone(lua_phase.innerRadius)
        self.assertIsNone(lua_phase.outerRadius)
        self.assertEqual(lua_phase.shockwaveRadius, 0)

    def test_invalid_version_count_types_family_or_provenance_are_rejected(self):
        changes = [
            lambda s: s.update(hd2RuntimeVersion='0.28.2'),
            lambda s: s.update(eagleCount=7),
            lambda s: s['stratagems'].pop(),
            lambda s: s['stratagems'][0].update(nativeTypeValue=30),
            lambda s: s['stratagems'][0].update(nativeTypeValue='126'),
            lambda s: s['stratagems'][0].update(nativeTypeValue=999),
            lambda s: s['stratagems'][0].update(family='orbital'),
            lambda s: s['stratagems'][0].update(name=s['stratagems'][1]['name']),
            lambda s: s.update(valueKind='live'),
            lambda s: s['stratagems'][0].update(sourceSnapshot='different'),
            lambda s: s['stratagems'][0]['phases'][0].update(sourceSnapshot='different'),
            lambda s: s.update(explosionPhaseCount=9),
            lambda s: s['stratagems'][-1]['phases'].pop(),
        ]
        for number, change in enumerate(changes):
            with self.subTest(change=number):
                source = copy.deepcopy(self.source)
                change(source)
                with self.assertRaises(ValueError):
                    self.normalize(source)

    def test_invalid_radii_are_rejected_not_coerced(self):
        for field in ('inner', 'outer', 'shockwave'):
            for bad in (-1, math.nan, math.inf, -math.inf, True, '12'):
                with self.subTest(field=field, bad=bad):
                    source = copy.deepcopy(self.source)
                    source['stratagems'][0]['phases'][0]['radiiMeters'][field] = bad
                    with self.assertRaises(ValueError):
                        self.normalize(source)


class LuaLiterals(unittest.TestCase):
    def test_literals_execute_in_lua51_with_numeric_keys_and_escaped_strings(self):
        vm = LuaRuntime(unpack_returned_tuples=True)
        text = 'quote" slash\\ newline\n carriage\r tab\t nul\x007 controls\x019 中文'
        value = {3: {'text': text, 'unknown': None, 'values': [0, 1.25, False, True]},
                 'end': -12.5, 2.5: 'fractional key'}
        result = vm.execute('return ' + lua_literal(value))
        self.assertEqual(result[3].text, text)
        self.assertIsNone(result['3'])
        self.assertIsNone(result[3].unknown)
        self.assertEqual([result[3]['values'][i] for i in range(1, 5)], [0, 1.25, False, True])
        self.assertEqual(result['end'], -12.5)
        self.assertEqual(result[2.5], 'fractional key')
        self.assertIsNone(vm.execute('return ' + lua_literal(None)))

    def test_nonfinite_numbers_and_unrepresentable_values_are_rejected(self):
        for value in (math.nan, math.inf, -math.inf, 10 ** 400, 2 ** 53 + 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    lua_literal(value)
        with self.assertRaises(ValueError):
            lua_literal({math.inf: 'invalid key'})
        with self.assertRaises(TypeError):
            lua_literal({None: 'invalid key'})
        with self.assertRaises(TypeError):
            lua_literal(object())


if __name__ == '__main__':
    unittest.main()
