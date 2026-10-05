"""Normalize pinned snapshot baselines; never read live mission-host settings."""
from __future__ import annotations

import json
import math
from pathlib import Path


_SOURCE_VERSION = '0.28.1'
_EAGLE_TYPES = frozenset({3, 18, 30, 38, 65, 126, 133, 140})
_RADIUS_FIELDS = (('inner', 'innerRadius'), ('outer', 'outerRadius'),
                  ('shockwave', 'shockwaveRadius'))
# Wiki salvo counts for carpets released across the throw. Spacing is not published.
# The centerline is (count - 1) times twice the spacing outer, so those circles touch.
# Strafing stays a forward run. 500kg is one bomb. 110mm has no circle.
# Napalm is 5 bombs and gas is 4, observed in play. Smoke is 4 green circles
# around the beacon, also observed: an 8-circle chain overstated the cloud.
# None of these counts are published by the wiki or HD2Runtime.
_RUN_LIMIT = 168
_ACROSS = {18: {'count': 6, 'fragment': False},
           38: {'count': 4, 'fragment': False},
           65: {'count': 8, 'fragment': True},
           126: {'count': 4, 'fragment': False},
           133: {'count': 5, 'fragment': False}}


def _spacing_outer(phases, fragment):
    chosen = [phase['outerRadius'] for phase in phases
              if ('shrapnel' in phase['path']) == fragment and phase['outerRadius'] is not None]
    if len(chosen) != 1:
        raise ValueError('across-throw spacing needs exactly one outer radius')
    return chosen[0]

def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonempty string')
    return value


def _radius(value, field):
    if value is None:
        return None
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{field} must be finite and nonnegative, or null')
    return value


def _phase_label(path):
    parts = path.split('/')
    if parts[-1] in ('impact', 'expiry'):
        return ('fragment ' if 'shrapnel' in parts else '') + parts[-1]
    # Retain an unfamiliar graph path rather than guessing its physical meaning.
    return path


def _meters(value):
    return '?' if value is None else format(value, '.4g')


def load_catalog(path: Path) -> dict:
    """Return eight validated Eagle definitions from the fixed 0.28.1 catalog.

    Phase paths and unknown radii survive normalization. Captions are rounded
    display text only; the numeric phase data retains the source precision.
    """
    source = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(source, dict) or source.get('schemaVersion') != 1:
        raise ValueError('Unsupported Eagle catalog schema')
    if source.get('hd2RuntimeVersion') != _SOURCE_VERSION:
        raise ValueError('Eagle catalog requires HD2Runtime snapshot version 0.28.1')
    if source.get('valueKind') != 'baseline':
        raise ValueError('Eagle catalog must contain snapshot baselines')
    snapshot = _text(source.get('sourceSnapshot'), 'sourceSnapshot')
    rows = source.get('stratagems')
    if not isinstance(rows, list) or len(rows) != 8 or source.get('eagleCount') != 8:
        raise ValueError('Eagle catalog must contain exactly eight definitions')

    entries, names = {}, set()
    phase_count = 0
    for row in rows:
        if not isinstance(row, dict) or row.get('family') != 'eagle':
            raise ValueError('Every catalog definition must belong to the Eagle family')
        native_type = row.get('nativeTypeValue')
        if type(native_type) is not int or native_type not in _EAGLE_TYPES or native_type in entries:
            raise ValueError('Eagle nativeTypeValue must be a unique pinned numeric type')
        name = _text(row.get('name'), 'name')
        if name in names:
            raise ValueError('Eagle definition names must be unique')
        names.add(name)
        if row.get('sourceSnapshot') != snapshot or row.get('valueKind') != 'baseline':
            raise ValueError('Eagle definition provenance does not match the snapshot baseline')
        raw_phases = row.get('phases')
        if not isinstance(raw_phases, list) or not raw_phases:
            raise ValueError('Eagle definition must retain its phase array')
        phases, paths = [], set()
        for raw in raw_phases:
            if not isinstance(raw, dict):
                raise ValueError('Eagle phase must be an object')
            phase_path = _text(raw.get('path'), 'phase path')
            if phase_path in paths:
                raise ValueError('Duplicate Eagle phase path')
            paths.add(phase_path)
            if raw.get('sourceSnapshot') != snapshot or raw.get('valueKind') != 'baseline':
                raise ValueError('Eagle phase provenance does not match the snapshot baseline')
            radii = raw.get('radiiMeters')
            if radii is None:
                radii = {}
            if not isinstance(radii, dict):
                raise ValueError('radiiMeters must be an object or null')
            phase = {'path': phase_path, 'label': _phase_label(phase_path)}
            for source_field, target_field in _RADIUS_FIELDS:
                phase[target_field] = _radius(radii.get(source_field), f'{phase_path}/{source_field}')
            phases.append(phase)
        if native_type == 3 and not {
                'delivery:1/projectile/impact', 'delivery:1/projectile/expiry'} <= paths:
            raise ValueError('500kg impact and expiry must remain separate phases')
        baseline = '; '.join(
            phase['label'] + ' ' + '/'.join(_meters(phase[field]) for _, field in _RADIUS_FIELDS)
            for phase in phases)
        caption = 'BASELINE I/O/S m: ' + baseline + ' | NOT A KILL/SAFE BOUNDARY'
        if native_type == 140:
            caption = 'TARGET UNKNOWN / NO IMPACT CIRCLE | ' + caption
        # Wiki tactical text calls the strafing run about 50 m. It is not a measured impact line.
        if native_type == 30:
            caption += ' | WIKI RUN ABOUT 50 m FROM BEACON PAST THE ANCHOR; WIDTH IS PER SHELL'
        across = _ACROSS.get(native_type)
        spacing_outer = _spacing_outer(phases, across['fragment']) if across else None
        if across:
            caption += (' | %d BURSTS ACROSS THE THROW; LENGTH ASSUMES TOUCHING %s m OUTERS, NOT MEASURED'
                        % (across['count'], _meters(spacing_outer)))
        # Captions must be suitable for the built-in ASCII HUD font.
        caption = caption.encode('ascii', errors='backslashreplace').decode('ascii')
        entry = {
            'name': name, 'type': native_type, 'caption': caption,
            'draw_rings': native_type != 140, 'phases': phases,
        }
        if native_type == 30:
            entry['runMeters'] = 50
        if across:
            span = (across['count'] - 1) * (spacing_outer * 2)
            if not 0 < span <= _RUN_LIMIT:
                raise ValueError('across-throw centerline is outside the render limit')
            entry['runMeters'] = span
            entry['pattern'] = 'across'
            if native_type == 38:
                entry['bursts'] = across['count']
                entry['tint'] = 'green'
        entries[native_type] = entry
        phase_count += len(phases)
    if set(entries) != _EAGLE_TYPES:
        raise ValueError('Eagle catalog type set does not match the pinned snapshot')
    if source.get('explosionPhaseCount') != phase_count:
        raise ValueError('Eagle catalog phase count does not match its phase arrays')
    return {'source_snapshot': snapshot, 'source_version': _SOURCE_VERSION, 'entries': entries}


def lua_literal(value) -> str:
    """Encode plain data as Lua 5.1 source, including numeric table keys.

    UTF-8 bytes use three-digit decimal escapes where needed, avoiding Lua 5.2
    escape syntax and ambiguity when a control byte precedes a decimal digit.
    Python None becomes nil (therefore a missing Lua table field).
    """
    if value is None:
        return 'nil'
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) in (int, float):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError('Lua numbers must be finite')
        if type(value) is int and int(float(value)) != value:
            raise ValueError('Integer cannot be represented exactly by a Lua 5.1 number')
        return repr(value)
    if isinstance(value, str):
        escaped = []
        for byte in value.encode('utf-8'):
            if byte in (34, 92):
                escaped.append('\\' + chr(byte))
            elif 32 <= byte <= 126:
                escaped.append(chr(byte))
            else:
                escaped.append(f'\\{byte:03d}')
        return '"' + ''.join(escaped) + '"'
    if isinstance(value, (list, tuple)):
        return '{' + ','.join(lua_literal(item) for item in value) + '}'
    if isinstance(value, dict):
        fields = []
        for key, item in value.items():
            if type(key) not in (str, int, float, bool):
                raise TypeError('Lua dictionary keys must be strings, numbers or booleans')
            fields.append('[' + lua_literal(key) + ']=' + lua_literal(item))
        return '{' + ','.join(fields) + '}'
    raise TypeError(f'Unsupported Lua data value: {type(value).__name__}')
