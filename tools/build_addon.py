"""Embed the standalone spatial HUD, then use BingusSharedLoader's archive packager."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import struct
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
LOADER_SCRIPTS = ROOT / '_vendor/BingusSharedLoader/scripts'
ENTRY = 'mods/engle/eagle_hud_probe'
GUID = 'a4601e99-90ac-41f8-89a7-6dcdce648678'
FAMILY = '9ba626afa44a3aa3'
def pair(english, chinese):
    return english + ' / ' + chinese


TITLE = pair('Eagle HUD Reference', '飞鹰参考')
TOGGLES = (
    ('Full Damage', '满伤内圈', 'Orange full-damage edge.', '橙色满伤边界。', 'no_inner', 80, 'inner'),
    ('Damage Edge', '伤害外圈', 'Red damage edge. Smoke coverage uses this too.', '红色伤害边缘。烟雾覆盖也走这项。', 'no_outer', 81, 'outer'),
    ('Shockwave', '冲击波圈', 'Pale blue stagger range.', '淡蓝色硬直范围。', 'no_shock', 82, 'shock'),
    ('Draw Rings', '绘制边界', 'The rings themselves. Off can leave the cross and text.', '范围圈本身。关掉后十字和文字可以单独留下。', 'no_dots', 83, 'dots'),
    ('Call Cross', '呼叫十字', 'Yellow cross on the call point.', '呼叫点上的黄色十字。', 'no_cross', 84, 'cross'),
    ('Type and Range', '型号与距离', 'Type, CALL REF, and BASELINE meters.', '型号、CALL REF 和 BASELINE 米数。', 'no_labels', 85, 'labels'),
    ('Squad Ranges', '队友范围', 'Off hides a call owned by another avatar. A call with no comms owner still stops at 80 m.', '关掉后，通讯记录属于别人的不画。没有归属时仍按 80 米。', 'no_squad', 96, 'squad'),
)
STYLES = (
    ('Square', '方点', None, None, None),
    ('Dash', '短划', 'mark_dash', 86, 2),
    ('Tick', '刻度', 'mark_tick', 87, 3),
    ('Cross', '十字', 'mark_plus', 88, 4),
)
SAMPLES = (64, 56, 48, 40, 32, 24, 16, 8)
RANGES = (120, 40, 80, 200, 300, 500)


def flag_entries():
    for _english, _chinese, _english_blurb, _chinese_blurb, folder, index, key in TOGGLES:
        yield folder, index, 'mods/engle/eagle_hud_' + folder, 'profile.' + key + ' = false'
    for _english, _chinese, folder, index, mark in STYLES:
        if folder:
            yield folder, index, 'mods/engle/eagle_hud_' + folder, 'profile.mark = ' + str(mark)
    for offset, count in enumerate(SAMPLES[1:]):
        folder = 'samples_' + str(count)
        yield folder, 89 + offset, 'mods/engle/eagle_hud_' + folder, 'profile.samples = ' + str(count)
    for offset, meters in enumerate(RANGES):
        if meters != 120:
            folder = 'range_' + str(meters)
            yield folder, 97 + offset, 'mods/engle/eagle_hud_' + folder, 'profile.range = ' + str(meters)


def flag_source(entry, statement):
    return ('-- HD2-Addon: ' + entry + '\n'
            'local profile = rawget(_G, "EngleEagleHudProfile")\n'
            'if type(profile) ~= "table" then profile = {} rawset(_G, "EngleEagleHudProfile", profile) end\n'
            + statement + '\n').encode('utf-8')


def sub_option(name, description, folder=None):
    row = {'Name': name, 'Description': description}
    if folder:
        row['Include'] = [folder]
    return row


def manager_options(description):
    options = [{'Name': TITLE, 'Description': description, 'Include': ['Addon']}]
    for english, chinese, english_blurb, chinese_blurb, folder, _index, _key in TOGGLES:
        options.append({'Name': pair(english, chinese),
                        'Description': pair(english_blurb + ' Leave unset or choose On to keep the default.',
                                            chinese_blurb + '不选或选择打开，保持默认。'),
                        'SubOptions': [sub_option(pair('On', '打开'), pair('Keep this on.', '保持这项打开。')),
                                       sub_option(pair('Off', '关闭'),
                                                  pair('Turn this off after deploy.', '部署后关掉这项。'), folder)]})
    options.append({'Name': pair('Marker Style', '边界样式'),
                    'Description': pair('Leave unset or choose Square to keep solid blocks.',
                                        '不选或选择方点，沿用实心小方块。'),
                    'SubOptions': [sub_option(pair(english, chinese),
                                              pair('Use this marker after deploy.', '部署后使用这种边界。'), folder)
                                   for english, chinese, folder, _index, _mark in STYLES]})
    options.append({'Name': pair('Point Count', '圆周点数'),
                    'Description': pair('Leave unset or choose 64 for the densest ring. Fewer is sparser.',
                                        '不选或选择 64，沿用最密的一圈。越少越稀。'),
                    'SubOptions': [sub_option(pair(str(count) + ' points', str(count) + ' 点'),
                                              pair('One ring uses ' + str(count) + ' points.',
                                                   '一圈 ' + str(count) + ' 个点。'),
                                              None if count == 64 else 'samples_' + str(count))
                                   for count in SAMPLES]})
    options.append({'Name': pair('Max Range', '最远距离'),
                    'Description': pair('Leave unset or choose 120 for the default, measured from the current view.',
                                        '不选或选择 120，沿用默认。从当前视角算起。'),
                    'SubOptions': [sub_option(pair(str(meters) + ' m', str(meters) + ' 米'),
                                              pair('Hide calls past ' + str(meters) + ' m.',
                                                   '超过 ' + str(meters) + ' 米不画。'),
                                              None if meters == 120 else 'range_' + str(meters))
                                   for meters in RANGES]})
    return options


def compose_source():
    reader = (ROOT / 'src/windows_readonly.lua').read_text(encoding='utf-8')
    probe = (ROOT / 'src/eagle_hud_probe.lua').read_text(encoding='utf-8')
    renderer = (ROOT / 'src/spatial_renderer.lua').read_text(encoding='utf-8')
    references = (ROOT / 'src/eagle_references.lua').read_text(encoding='utf-8')
    spec = importlib.util.spec_from_file_location('engle_catalog', ROOT / 'tools/eagle_catalog.py')
    normalizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(normalizer)
    catalog = normalizer.lua_literal(normalizer.load_catalog(ROOT / 'research/eagle_catalog.json'))
    marker = '-- HD2-Addon: ' + ENTRY + '\n'
    if not probe.startswith(marker):
        raise ValueError('Probe entry declaration does not match the packaged resource')
    return (marker + '-- Self-contained: only BingusSharedLoader is a mod dependency.\n'
            + 'local create_reader = (function()\n' + reader + '\nend)()\n'
            + 'local create_renderer = (function()\n' + renderer + '\nend)()\n'
            + 'local create_references = (function()\n' + references + '\nend)()\n'
            + 'local catalog = ' + catalog + '\n'
            + 'return (function(...)\n' + probe[len(marker):]
            + '\nend)(create_reader, create_renderer, create_references, catalog)\n').encode('utf-8')


def build(output: Path):
    source = compose_source()
    generated = ROOT / 'build/eagle_hud_probe.lua'
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_bytes(source)
    # The original packager owns the archive format and manifest construction.
    sys.path.insert(0, str(LOADER_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location('engle_upstream_build_addon', LOADER_SCRIPTS / 'build_addon.py')
        upstream = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(upstream)
        result = upstream.build_addon(ENTRY, source, GUID, output, TITLE)
    finally:
        sys.path.pop(0)
    # Customize only manager metadata; retain the official archive bytes verbatim.
    with zipfile.ZipFile(result) as package:
        members = [(info, package.read(info.filename)) for info in package.infolist()]
    description = pair(
        'Requires Bingus Shared Loader v18 API 1. Arsenal profile options deploy '
        'the non-default layers, boundary style, and point count. In-game Mod Options '
        'Menu v1.2 APPLY overrides a deployed choice. No HD2Runtime dependency.',
        '需要 Bingus Shared Loader v18 API 1。管理器选项部署非默认图层、边界样式和点数。'
        '游戏内 Mod Options Menu v1.2 的 APPLY 覆盖已部署的选择。不依赖 HD2Runtime。')
    files = {info.filename: content for info, content in members if info.filename != 'manifest.json'}
    for folder, index, entry, statement in flag_entries():
        body = upstream.entry_source(entry, flag_source(entry, statement))
        resource = struct.pack('<II', len(body), 2) + body
        archive = upstream.make_archive({upstream.resource_hash(entry): resource})
        base = folder + '/' + FAMILY + '.patch_' + str(index)
        files[base] = archive
        files[base + '.stream'] = b''
        files[base + '.gpu_resources'] = b''
    manifest = json.loads(members[[info.filename for info, _content in members].index('manifest.json')][1])
    manifest['Description'] = description
    manifest['Options'] = manager_options(description)
    files['manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode('utf-8')
    with zipfile.ZipFile(result, 'w') as package:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, files[name])
    patch_dir = ROOT / 'dist/Eagle-HUD-Probe-patch'
    patch_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(result) as package:
        for name in package.namelist():
            if name.startswith('Addon/'):
                target = patch_dir / Path(name).name
                target.write_bytes(package.read(name))
    report = {
        'entry': ENTRY, 'guid': GUID, 'archivePackager': 'BingusSharedLoader/scripts/build_addon.py',
        'runtimeModDependencies': ['BingusSharedLoader v18 / API 1'],
        'generatedSource': str(generated.relative_to(ROOT)),
        'sourceSha256': hashlib.sha256(source).hexdigest().upper(),
        'zip': str(result), 'zipSha256': hashlib.sha256(result.read_bytes()).hexdigest().upper(),
        'spatialHudImplemented': True,
        'spatialMode': 'current_call_coordinate_references',
        'predictedImpactsImplemented': False,
        'stableSortieIdentityClaimed': False,
        'inGameSpatialVerified': False,
        'catalog': 'research/eagle_catalog.json',
        'catalogSha256': hashlib.sha256((ROOT / 'research/eagle_catalog.json').read_bytes()).hexdigest().upper(),
    }
    (ROOT / 'build/build_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/Eagle-HUD-Probe.zip')
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))


if __name__ == '__main__':
    main()
