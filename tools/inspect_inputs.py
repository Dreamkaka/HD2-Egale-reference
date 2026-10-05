"""Inspect pinned Eagle HUD research inputs without modifying game/vendor files.

Usage: python -B tools/inspect_inputs.py --game-root PATH --out PATH [--probe-zip PATH]
The first successful input scan saves an immutable pre-deployment patch baseline.
Subsequent scans reject additions, removals, or changed main/sidecar files. Use the
same output directory until deployment; this is not a post-deployment verifier.
No game Lua is executed, no configuration is generated, and no releases are fetched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import struct
import sys
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / '_vendor'
FAMILY = '9ba626afa44a3aa3'
PATCH_RE = re.compile(r'([0-9a-fA-F]{16})\.patch_(\d+)(\.stream|\.gpu_resources)?')
LUA_TYPE = 0xA14E8DFA2CD117E2
BOOT_HASH = 0xF476DF93691895FA
REVIEWED_HUD_ARCHIVE_SHA = 'BB27A74E19DFB74A46308E26044E0BE40B4D50C7C6C29C37A03B6680359D1FCD'
EXPECTED_GAME = {
    'bin/helldivers2.exe': 'F5FEE03DCFDB2E553A4752C283590950AC13316B376D8196AA556FF0400D5F06',
    'data/game/game.dll': '2E2C3B7C2500646DADD5F2B4C6E0504DBB7E7896139F64CDDC0D1813C718F51E',
}
COMMITS = {
    'BingusSharedLoader': '3d7e3a120828178573ef1ee0a5c7eeae4a951865',
    'HD2Runtime': '96ab2d258d867a5df4f22bb7b3321d84d28de21d',
}
ADDON = 'mods/engle/eagle_hud_probe'
GUID = 'a4601e99-90ac-41f8-89a7-6dcdce648678'
WWISE = 'core/wwise/lua/wwise_flow_callbacks'
RUNTIME = 'mods/skyeshade/hd2runtime'
RESEARCH_JSON = VENDOR / 'HD2Runtime/research/offensive-stratagem-runtime-F5FEE03DCFDB.json'
SDK_JSON = VENDOR / 'HD2Runtime/sdk/StratagemAuthoringCapabilities.json'
MAX_LUA_BYTES = 64 * 1024 * 1024
MAX_ZIP_BYTES = 128 * 1024 * 1024


class InspectionError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InspectionError(message)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest().upper()


def sha_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest().upper()


def hex64(value):
    return f'0x{value:016X}'


def read_at(source, offset, size, total, label):
    require(0 <= offset <= total and 0 <= size <= total - offset,
            f'INVALID_ARCHIVE: {label} range {offset}+{size} exceeds {total}')
    source.seek(offset)
    data = source.read(size)
    require(len(data) == size, f'INPUT_CHANGED: short read of {label}')
    return data


def hash_range(source, offset, size):
    source.seek(offset)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = source.read(min(remaining, 1024 * 1024))
        require(bool(block), 'INPUT_CHANGED: truncated resource')
        digest.update(block)
        remaining -= len(block)
    return digest.hexdigest().upper()


def decode_lua(resource, label):
    require(len(resource) >= 8, f'INVALID_LUA: {label} missing envelope')
    length, version = struct.unpack_from('<II', resource)
    require(version == 2 and length == len(resource) - 8,
            f'INVALID_LUA: {label} envelope length/version mismatch')
    return resource[8:]


def parse_archive(source, total, sidecar_sizes, label):
    """Read the Loader archive.py layout; all ranges are checked before reading."""
    header = read_at(source, 0, 72, total, label + ' header')
    magic, type_count, count = struct.unpack_from('<III', header)
    require(magic == 0xF0000011, f'INVALID_ARCHIVE: {label} magic')
    table_end = 72 + 32 * type_count + 80 * count
    require(type_count > 0 and count > 0 and table_end <= total,
            f'INVALID_ARCHIVE: {label} type/resource tables')
    types = {}
    for index in range(type_count):
        entry = read_at(source, 72 + index * 32, 32, total, label + ' type')
        _, _, type_hash, members, _, _, _ = struct.unpack('<IIQIIII', entry)
        require(type_hash not in types, f'INVALID_ARCHIVE: {label} duplicate type')
        types[type_hash] = members
    resources, seen, spans = [], set(), []
    actual_types = Counter()
    for index in range(count):
        entry_offset = 72 + type_count * 32 + index * 80
        fields = struct.unpack('<7Q6I', read_at(source, entry_offset, 80, total, label + ' entry'))
        name, kind, main_at, stream_at, gpu_at = fields[:5]
        main_size, stream_size, gpu_size = fields[7:10]
        require(kind in types and (name, kind) not in seen,
                f'INVALID_ARCHIVE: {label} unknown type or duplicate resource')
        seen.add((name, kind))
        actual_types[kind] += 1
        for location, size, limit, storage in (
            (main_at, main_size, total, 'main'),
            (stream_at, stream_size, sidecar_sizes['stream'], 'stream'),
            (gpu_at, gpu_size, sidecar_sizes['gpu_resources'], 'gpu_resources'),
        ):
            require(size == 0 or (0 <= location <= limit and size <= limit - location),
                    f'INVALID_ARCHIVE: {label} {hex64(name)} {storage} range')
        require(main_size == 0 or main_at >= table_end,
                f'INVALID_ARCHIVE: {label} resource overlaps metadata')
        if main_size:
            spans.append((main_at, main_at + main_size))
        row = {
            'nameHash': hex64(name), 'typeHash': hex64(kind), 'index': index,
            'mainOffset': main_at, 'mainSize': main_size,
            'streamOffset': stream_at, 'streamSize': stream_size,
            'gpuOffset': gpu_at, 'gpuSize': gpu_size,
            'resourceSha256': hash_range(source, main_at, main_size),
            'extractionPath': None,
        }
        if kind == LUA_TYPE:
            require(stream_size == 0 and gpu_size == 0 and main_size <= MAX_LUA_BYTES,
                    f'INVALID_LUA: {label} sidecar-backed or oversized Lua')
            resource = read_at(source, main_at, main_size, total, label + ' Lua')
            body = decode_lua(resource, label + '/' + hex64(name))
            row.update(bodySha256=sha_bytes(body), envelopeVersion=2,
                       bodySize=len(body), encoding='luajit' if body.startswith(b'\x1b') else 'plaintext',
                       _body=body)
        resources.append(row)
    require(dict(actual_types) == types, f'INVALID_ARCHIVE: {label} type counts disagree')
    spans.sort()
    require(all(left[1] <= right[0] for left, right in zip(spans, spans[1:])),
            f'INVALID_ARCHIVE: {label} overlapping main resources')
    return resources


def load_archive_helpers():
    # Reuse the pinned Loader's resource hashing/canonical packaging, without
    # producing __pycache__ in the read-only research checkout.
    sys.dont_write_bytecode = True
    path = VENDOR / 'BingusSharedLoader/scripts/archive.py'
    spec = importlib.util.spec_from_file_location('engle_loader_archive', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patch_inventory(game_root):
    result = {}
    for path in sorted((game_root / 'data').iterdir()):
        match = PATCH_RE.fullmatch(path.name)
        if match and path.is_file():
            require(path.resolve().is_relative_to(game_root),
                    f'INPUT_OUTSIDE_GAME: {path}')
            result[path.name] = {'size': path.stat().st_size, 'sha256': sha_file(path)}
    require(bool(result), 'PATCH_INPUT_MISSING: no patch main/sidecar files')
    return result


def baseline_document(game_root, inventory):
    return {'schemaVersion': 1, 'purpose': 'immutable pre-deployment originals',
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'gameRoot': str(game_root), 'files': inventory}


def compare_baseline(document, game_root, current):
    require(document.get('schemaVersion') == 1 and document.get('gameRoot') == str(game_root),
            'BASELINE_MISMATCH: schema or game root differs; original baseline was not overwritten')
    original = document.get('files')
    require(isinstance(original, dict), 'BASELINE_INVALID: files must be an object')
    added = sorted(current.keys() - original.keys())
    removed = sorted(original.keys() - current.keys())
    changed = sorted(name for name in original.keys() & current.keys() if original[name] != current[name])
    require(not (added or removed or changed), 'ORIGINAL_PATCHES_CHANGED: ' + json.dumps(
        {'added': added, 'removed': removed, 'changed': changed}, ensure_ascii=False))


def eagle_catalog():
    research = json.loads(RESEARCH_JSON.read_text(encoding='utf-8'))
    sdk = json.loads(SDK_JSON.read_text(encoding='utf-8'))
    require(sdk.get('hd2RuntimeVersion') == '0.28.1', 'CATALOG_VERSION_MISMATCH')
    research_snapshot = research.get('source', {}).get('snapshot')
    sdk_snapshot = sdk.get('source', {}).get('snapshot')
    require(research_snapshot and research_snapshot == sdk_snapshot, 'CATALOG_SNAPSHOT_MISMATCH')
    native_rows = [row for row in research['stratagems'] if row.get('family', '').lower() == 'eagle']
    sdk_rows = [row for row in sdk['stratagems'] if row.get('family', '').lower() == 'eagle']
    native = {row['name']: row for row in native_rows}
    capabilities = {row['name']: row for row in sdk_rows}
    require(len(native_rows) == len(native) == len(sdk_rows) == len(capabilities) == 8
            and native.keys() == capabilities.keys(), 'CATALOG_INCOMPLETE: expected eight matching Eagles')
    entries = []
    for name, row in native.items():
        capability = capabilities[name]
        graph = row.get('nativeGraph')
        require(isinstance(graph, list), f'CATALOG_INVALID: {name} nativeGraph missing')
        phases = []
        for node in graph:
            if node.get('kind') != 'ExplosionSettings':
                continue
            fields = node.get('fields', {})
            phases.append({
                'path': node.get('path'), 'linkage': node.get('linkage'),
                'recordType': node.get('recordType'), 'damagePath': node.get('damage'),
                'radiiMeters': {'inner': fields.get('explosion.inner_radius'),
                                'outer': fields.get('explosion.outer_radius'),
                                'shockwave': fields.get('explosion.shockwave_radius')},
                'sourceSnapshot': research_snapshot, 'valueKind': 'baseline',
            })
        eagle_components = [component for payload in row.get('payloadReports', [])
                            for component in payload.get('components', [])
                            if component.get('name') == 'EagleComponentData']
        entries.append({
            'name': name, 'family': 'eagle', 'semanticId': capability.get('semanticId'),
            'nativeTypeValue': (capability.get('uiIcon') or {}).get('nativeTypeValue'),
            'nativeType': (capability.get('uiIcon') or {}).get('nativeType'),
            'sourceSnapshot': research_snapshot, 'valueKind': 'baseline',
            'attackRoles': capability.get('attackRoles'), 'phases': phases,
            'nativeGraph': graph,
            'spawnTimeRaw': (row.get('currentRoot') or {}).get('spawn_time'),
            'callInTime': capability.get('callInTime'),
            'usesPerRearm': capability.get('usesPerRearm'), 'rearmTime': capability.get('rearmTime'),
            'eagleReviewedScalars': [component.get('reviewedScalars') for component in eagle_components]
                                    if eagle_components else None,
        })
    return {
        'schemaVersion': 1, 'hd2RuntimeVersion': '0.28.1', 'sourceSnapshot': research_snapshot,
        'sourceFiles': [{'path': str(path.relative_to(ROOT)), 'sha256': sha_file(path)}
                        for path in (RESEARCH_JSON, SDK_JSON)],
        'valueKind': 'baseline',
        'limitations': [
            'Snapshot catalog, not live mission-host settings or kill/safety boundaries.',
            'Phases retain graph paths; impact, expiry and shrapnel are not merged.',
            'nativeTypeValue is SDK identity, not a proven active-array type association.',
            'spawnTimeRaw is not an observed call-in time or impact ETA.',
        ],
        'eagleCount': len(entries), 'explosionPhaseCount': sum(len(row['phases']) for row in entries),
        'stratagems': entries,
    }


def inspect_probe(path, helper):
    from tools.build_addon import TITLE, flag_entries, flag_source
    expected = {folder: (index, entry, statement) for folder, index, entry, statement in flag_entries()}
    with zipfile.ZipFile(path, 'r') as package:
        infos = package.infolist()
        names = [info.filename for info in infos]
        require(len(names) == len(set(names)), 'INVALID_PROBE: duplicate ZIP members')
        require(all(not info.is_dir() and not (info.flag_bits & 1) for info in infos)
                and sum(info.file_size for info in infos) <= MAX_ZIP_BYTES,
                'INVALID_PROBE: encrypted, directory, or oversized ZIP member')
        manifest = json.loads(package.read('manifest.json').decode('utf-8'))
        require(isinstance(manifest, dict) and manifest.get('Version') == 1,
                'INVALID_PROBE: manifest version')
        require(str(uuid.UUID(manifest.get('Guid', ''))) == GUID, 'INVALID_PROBE: GUID mismatch')
        options = manifest.get('Options')
        require(isinstance(options, list) and options and options[0].get('Name') == TITLE
                and options[0].get('Include') == ['Addon'], 'INVALID_PROBE: base option')
        includes = []
        for option in options[1:]:
            subs = option.get('SubOptions')
            require(isinstance(subs, list) and subs and 'Include' not in subs[0],
                    'INVALID_PROBE: first choice must keep the deployed default')
            for sub in subs[1:]:
                include = sub.get('Include')
                require(isinstance(include, list) and len(include) == 1, 'INVALID_PROBE: include')
                includes.append(include[0])
        require(set(includes) == set(expected), 'INVALID_PROBE: option folders')
        archive_name = 'Addon/' + helper.ARCHIVE
        for suffix in ('.stream', '.gpu_resources'):
            require(package.read(archive_name + suffix) == b'', 'INVALID_PROBE: nonempty sidecar')
        data = package.read(archive_name)
        flags = []
        seen = set()
        for name in names:
            if name in ('manifest.json', archive_name, archive_name + '.stream', archive_name + '.gpu_resources'):
                continue
            match = re.fullmatch(r'([A-Za-z0-9_]+)/' + FAMILY + r'\.patch_(\d+)(\.stream|\.gpu_resources)?', name)
            require(match, 'INVALID_PROBE: unexpected member ' + name)
            folder, index, suffix = match.group(1), int(match.group(2)), match.group(3)
            require(folder in expected and index == expected[folder][0], 'INVALID_PROBE: patch index')
            if suffix:
                require(package.read(name) == b'', 'INVALID_PROBE: nonempty sidecar')
            else:
                require(index not in seen, 'INVALID_PROBE: duplicate patch index')
                seen.add(index)
                flags.append((folder, package.read(name)))
    rows = parse_archive(io.BytesIO(data), len(data), {'stream': 0, 'gpu_resources': 0}, str(path))
    require(len(rows) == 1 and rows[0]['typeHash'] == hex64(LUA_TYPE)
            and rows[0]['nameHash'] == hex64(helper.resource_hash(ADDON)),
            'INVALID_PROBE: archive must contain only the declared addon Lua (no boot/Wwise/other resources)')
    body = rows[0]['_body']
    marker = ('-- HD2-Addon: ' + ADDON + '\n').encode('utf-8')
    require(body.startswith(marker) and not body.startswith((b'\xef\xbb\xbf', b'\x1b')) and b'\0' not in body,
            'INVALID_PROBE: entry declaration or plaintext format')
    body.decode('utf-8')
    resource = struct.pack('<II', len(body), 2) + body
    require(data == helper.make_archive({helper.resource_hash(ADDON): resource}),
            'INVALID_PROBE: noncanonical Loader archive header, entries, padding or trailing data')
    for folder, flag in flags:
        _index, entry, statement = expected[folder]
        flag_rows = parse_archive(io.BytesIO(flag), len(flag), {'stream': 0, 'gpu_resources': 0}, folder)
        require(len(flag_rows) == 1 and flag_rows[0]['nameHash'] == hex64(helper.resource_hash(entry)),
                'INVALID_PROBE: flag resource')
        flag_body = flag_rows[0]['_body']
        require(flag_body == flag_source(entry, statement), 'INVALID_PROBE: flag source')
        packed = struct.pack('<II', len(flag_body), 2) + flag_body
        require(flag == helper.make_archive({helper.resource_hash(entry): packed}),
                'INVALID_PROBE: flag archive')
    return {'status': 'valid', 'path': str(path.resolve()), 'sha256': sha_file(path),
            'guid': GUID, 'entry': ADDON, 'resourceNameHash': rows[0]['nameHash'],
            'resourceSha256': rows[0]['resourceSha256'], 'bodySha256': rows[0]['bodySha256'],
            'luaResources': 1, 'bootOrWwiseResources': 0, 'managerFlags': len(flags)}


def public_row(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}


def inspect_family(game_root, inventory, helper):
    mains = [name for name in inventory if (match := PATCH_RE.fullmatch(name))
             and match[1].lower() == FAMILY and match[3] is None]
    mains.sort(key=lambda name: (int(PATCH_RE.fullmatch(name)[2]), name))
    require(bool(mains), 'PATCH_INPUT_MISSING: no target-family main archives')
    priorities = [int(PATCH_RE.fullmatch(name)[2]) for name in mains]
    require(len(set(priorities)) == len(priorities), 'AMBIGUOUS_PATCH_PRIORITY')
    archives, winners = [], {}
    for name, priority in zip(mains, priorities):
        sides = {suffix: inventory.get(name + '.' + suffix, {}).get('size', 0)
                 for suffix in ('stream', 'gpu_resources')}
        with (game_root / 'data' / name).open('rb') as source:
            rows = parse_archive(source, inventory[name]['size'], sides, name)
        archive = {'path': 'data/' + name, 'priority': priority, **inventory[name], 'resources': rows}
        archives.append(archive)
        for row in rows:
            winners[(row['nameHash'], row['typeHash'])] = (archive, row)
    boot_pair = winners.get((hex64(BOOT_HASH), hex64(LUA_TYPE)))
    require(boot_pair is not None, 'HUD_BOOT_MISSING: no winning boot resource in target patch family')
    boot_archive, boot_row = boot_pair
    boot_body = boot_row['_body']
    require(not boot_body.startswith((b'\x1b', b'\xef\xbb\xbf')) and b'\0' not in boot_body,
            'HUD_BOOT_UNREADABLE: winning boot is not plaintext UTF-8')
    boot_body.decode('utf-8')
    boot_row['extractionPath'] = 'installed_hud_boot.lua'
    boot_status = 'reviewed' if boot_archive['sha256'] == REVIEWED_HUD_ARCHIVE_SHA else 'HUD_INPUT_CHANGED'
    dependencies = {}
    for label, resource_name in (('Loader', WWISE), ('Runtime', RUNTIME)):
        key = (hex64(helper.resource_hash(resource_name)), hex64(LUA_TYPE))
        pair = winners.get(key)
        occurrences = [{'archive': archive['path'], 'priority': archive['priority'], **public_row(row)}
                       for archive in archives for row in archive['resources']
                       if (row['nameHash'], row['typeHash']) == key]
        winner = None if pair is None else {'archive': pair[0]['path'], **public_row(pair[1])}
        # Wwise exists in vanilla too. An embedded marker is evidence of Loader
        # code, not proof of the installed release/API or successful execution.
        marker_present = bool(pair and b'CowboyBingusModLoader' in pair[1]['_body']) if label == 'Loader' else None
        dependencies[label] = {
            'resourcePath': resource_name, 'resourceNameHash': key[0],
            'resourcePresent': pair is not None, 'winningResource': winner, 'occurrences': occurrences,
            'loaderMarkerPresentInWinner': marker_present,
            'requiredAtRuntime': label == 'Loader',
            'requiredVersion': 'v18 / API 1' if label == 'Loader' else None,
            'installedVersion': None, 'versionStatus': 'unverified',
        }
    for archive in archives:
        archive['resources'] = [public_row(row) for row in archive['resources']]
    return archives, dependencies, {
        'status': boot_status, 'archive': boot_archive['path'],
        'archiveSha256': boot_archive['sha256'], 'reviewedArchiveSha256': REVIEWED_HUD_ARCHIVE_SHA,
        **public_row(boot_row),
        'candidateMappingAllowed': boot_status == 'reviewed',
    }, boot_body


def safe_outputs(out, game_root):
    protected = (game_root, VENDOR.resolve())
    paths = {name: out / name for name in (
        'original_patch_hashes.json', 'input_manifest.json', 'installed_hud_boot.lua', 'eagle_catalog.json')}
    for path in (out, *paths.values()):
        require(not any(path.resolve().is_relative_to(root) for root in protected),
                f'OUTPUT_INSIDE_READ_ONLY_INPUT: {path}')
        require(not path.is_symlink(), f'OUTPUT_SYMLINK_REFUSED: {path}')
        if path.exists() and path.is_file():
            require(path.stat().st_nlink == 1, f'OUTPUT_HARDLINK_REFUSED: {path}')
    return paths


def write_json(path, document, exclusive=False):
    with path.open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as target:
        json.dump(document, target, indent=2, ensure_ascii=False, allow_nan=False)
        target.write('\n')


def inspect(args):
    game_root = args.game_root.resolve()
    out = args.out.resolve()
    paths = safe_outputs(out, game_root)
    if args.probe_zip:
        require(args.probe_zip.resolve() not in {path.resolve() for path in paths.values()},
                'OUTPUT_OVERLAPS_PROBE_INPUT: choose a separate output directory')
    fingerprints = {name: {'expectedSha256': expected, 'sha256': sha_file(game_root / name)}
                    for name, expected in EXPECTED_GAME.items()}
    require(all(row['sha256'] == row['expectedSha256'] for row in fingerprints.values()),
            'UNSUPPORTED_BUILD: ' + json.dumps(fingerprints))
    inventory = patch_inventory(game_root)
    baseline_path = paths['original_patch_hashes.json']
    baseline = json.loads(baseline_path.read_text(encoding='utf-8')) if baseline_path.exists() else None
    if baseline is not None:
        compare_baseline(baseline, game_root, inventory)
    helper = load_archive_helpers()
    catalog = eagle_catalog()
    archives, dependencies, boot, boot_body = inspect_family(game_root, inventory, helper)
    probe = inspect_probe(args.probe_zip.resolve(), helper) if args.probe_zip else None
    # Detect changes during the scan, not just between invocations.
    compare_baseline(baseline_document(game_root, inventory), game_root, patch_inventory(game_root))
    require(all(sha_file(game_root / name) == row['sha256'] for name, row in fingerprints.items()),
            'INPUT_CHANGED: game fingerprint changed during inspection')
    manifest = {
        'schemaVersion': 1, 'createdAt': datetime.now(timezone.utc).isoformat(),
        'gameRoot': str(game_root), 'gameFingerprints': fingerprints,
        'sourceCommits': COMMITS, 'requiredRuntimeVersion': None, 'catalogSourceRuntimeVersion': '0.28.1',
        'requiredLoaderRelease': 'v18', 'requiredLoaderApi': 1,
        'archiveFamily': FAMILY, 'priorityRule': 'ascending numeric patch suffix; highest resource wins',
        'dependencyScanScope': 'target archive family only; resource/marker presence is not version or runtime proof',
        'patchBaseline': {'path': baseline_path.name, 'status': 'unchanged' if baseline else 'created',
                          'fileCount': len(inventory), 'scope': 'all data/*.patch_N main and known sidecars'},
        'archives': archives, 'winningBoot': boot, 'dependencies': dependencies,
        'catalogPath': 'eagle_catalog.json', 'probePackage': probe,
        'status': 'HUD_INPUT_CHANGED' if boot['status'] != 'reviewed' else 'supported_inputs',
        'deployableConfigurationGenerated': False,
    }
    out.mkdir(parents=True, exist_ok=True)
    if baseline is None:
        write_json(baseline_path, baseline_document(game_root, inventory), exclusive=True)
    paths['installed_hud_boot.lua'].write_bytes(boot_body)
    write_json(paths['eagle_catalog.json'], catalog)
    write_json(paths['input_manifest.json'], manifest)
    print(json.dumps({'status': manifest['status'], 'manifest': str(paths['input_manifest.json']),
                      'eagleCount': catalog['eagleCount'], 'explosionPhaseCount': catalog['explosionPhaseCount'],
                      'originalPatchFiles': len(inventory),
                      'dependencies': {name: {'resourcePresent': row['resourcePresent'],
                                             'loaderMarkerPresentInWinner': row['loaderMarkerPresentInWinner'],
                                             'versionStatus': row['versionStatus']}
                                       for name, row in dependencies.items()},
                      'probePackage': probe}, ensure_ascii=False, indent=2))
    if boot['status'] != 'reviewed':
        print('HUD_INPUT_CHANGED: re-read extracted HUD named regions before using candidate layouts.', file=sys.stderr)
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--probe-zip', type=Path)
    args = parser.parse_args()
    try:
        return inspect(args)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as error:
        print(f'INSPECTION_FAILED: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
