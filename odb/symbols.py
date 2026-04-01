"""odb/symbols.py — Symbol table parsing, scale detection, user symbols."""

import os
import re
from typing import Optional

from odb.constants import MILS_TO_MM, MIL_DETECT_RATIO, COORD_NONZERO_EPSILON, DEFAULT_CORNER_RADIUS
from odb.models import _ODBSymbol


def parse_symbol_descriptor(desc: str) -> _ODBSymbol:
    """
    Parse a single symbol descriptor string into an _ODBSymbol.
    Handles all known ODB++ aperture types; falls back gracefully for unknowns.
    """
    desc = desc.strip().lower()

    if desc.startswith('special_') or desc.startswith('sc_join'):
        return _ODBSymbol('skip', 0.0, 0.0, desc)

    # round: r<diameter>
    if desc.startswith('r') and not desc.startswith('rect') and not desc.startswith('rounded') and not desc.startswith('rr'):
        try:
            return _ODBSymbol('round', float(desc[1:]), float(desc[1:]), desc)
        except ValueError:
            pass

    # rect: rect<w>x<h>  or  rect<w>x<h>xr<corner_r>  (InCAM inline rounded-rect)
    elif desc.startswith('rect'):
        try:
            parts = desc[4:].split('x')
            w, h = float(parts[0]), float(parts[1])
            if len(parts) >= 3 and parts[2].startswith('r'):
                r = float(parts[2][1:])
                return _ODBSymbol('rounded_rect', w, h, desc, corner_r=r)
            return _ODBSymbol('rect', w, h, desc)
        except (IndexError, ValueError):
            pass

    # donut_r: donut_r<outer>x<inner>
    elif desc.startswith('donut_r'):
        try:
            parts = desc[7:].split('x')
            return _ODBSymbol('donut', float(parts[0]), float(parts[1]), desc)
        except (IndexError, ValueError):
            pass

    # oval / oblong: oval<w>x<h> or oblong<w>x<h>
    elif desc.startswith('oval') or desc.startswith('oblong'):
        try:
            offset = 4 if desc.startswith('oval') else 6
            parts = desc[offset:].split('x')
            return _ODBSymbol('oval', float(parts[0]), float(parts[1]), desc)
        except (IndexError, ValueError):
            pass

    # rounded_rect / rr: rr<w>x<h>x<r> or rounded_rect<w>x<h>x<r>
    elif desc.startswith('rr') or desc.startswith('rounded_rect'):
        try:
            offset = 2 if desc.startswith('rr') else 12
            parts = desc[offset:].split('x')
            w, h = float(parts[0]), float(parts[1])
            r = float(parts[2]) if len(parts) > 2 else min(w, h) * DEFAULT_CORNER_RADIUS
            return _ODBSymbol('rounded_rect', w, h, desc, corner_r=r)
        except (IndexError, ValueError):
            pass

    # octagon / oct
    elif desc.startswith('octagon') or desc.startswith('oct'):
        try:
            offset = 7 if desc.startswith('octagon') else 3
            d = float(desc[offset:])
            return _ODBSymbol('octagon', d, d, desc)
        except ValueError:
            pass

    # hexagon / hex
    elif desc.startswith('hexagon') or desc.startswith('hex'):
        try:
            offset = 7 if desc.startswith('hexagon') else 3
            d = float(desc[offset:])
            return _ODBSymbol('hexagon', d, d, desc)
        except ValueError:
            pass

    # triangle / tri
    elif desc.startswith('triangle') or desc.startswith('tri'):
        try:
            offset = 8 if desc.startswith('triangle') else 3
            d = float(desc[offset:])
            return _ODBSymbol('triangle', d, d, desc)
        except ValueError:
            pass

    # thermal: thermal<outer>x<inner>
    elif desc.startswith('thermal'):
        try:
            parts = desc[7:].split('x')
            outer = float(parts[0])
            inner = float(parts[1]) if len(parts) > 1 else outer * 0.6
            return _ODBSymbol('thermal', outer, inner, desc)
        except (IndexError, ValueError):
            pass

    # cross: cross<w>x<h>
    elif desc.startswith('cross'):
        try:
            parts = desc[5:].split('x')
            w = float(parts[0])
            h = float(parts[1]) if len(parts) > 1 else w
            return _ODBSymbol('cross', w, h, desc)
        except (IndexError, ValueError):
            pass

    # ellipse: ellipse<w>x<h>
    elif desc.startswith('ellipse'):
        try:
            parts = desc[7:].split('x')
            return _ODBSymbol('ellipse', float(parts[0]), float(parts[1]), desc)
        except (IndexError, ValueError):
            pass

    # square / sq: sq<d> or square<d>
    elif desc.startswith('sq') or desc.startswith('square'):
        try:
            offset = 2 if desc.startswith('sq') else 6
            d = float(desc[offset:])
            return _ODBSymbol('square', d, d, desc)
        except ValueError:
            pass

    # s<d> — square shorthand (must come after 'sq'/'square'/'special' checks)
    elif desc.startswith('s') and not desc.startswith('special') and not desc.startswith('sc_join'):
        try:
            d = float(desc[1:])
            return _ODBSymbol('square', d, d, desc)
        except ValueError:
            pass

    # diamond: di<d>
    elif desc.startswith('di'):
        try:
            d = float(desc[2:])
            return _ODBSymbol('diamond', d, d, desc)
        except ValueError:
            pass

    # Fallback: unrecognised descriptor (e.g. 'fiducial_swiss1000um_board1').
    # Return 'unknown' so _parse_layer_to_gerbonara can substitute the correct
    # bounding-box size from user_sym_map.  Number-extraction is intentionally
    # avoided — names like 'fiducial_swiss1000um_board1' would yield 1000 mm.
    return _ODBSymbol('unknown', 0.0, 0.0, desc)


def parse_symbol_table(lines: list) -> dict:
    """
    Parse the $ lines at the top of a features file into a symbol dict.

    Each $ line format: $<index> <descriptor>
    Returns {index: _ODBSymbol}.
    """
    symbols = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith(';'):
            continue
        if not stripped.startswith('$'):
            break  # symbol table is always at the top

        stripped = stripped.split('#')[0].split(';')[0].strip()
        parts = stripped.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[0][1:])  # strip '$'
        except ValueError:
            continue

        symbols[idx] = parse_symbol_descriptor(parts[1])
    return symbols


def parse_symbol_table_from_text(text: str) -> dict:
    """Parse symbol table from full features file text."""
    return parse_symbol_table(text.splitlines())


def detect_symbol_scale(features_text: str, uf: float) -> float:
    """
    Detect whether symbol sizes are in mils while coordinates are in mm.

    InCAM Pro stores symbol tables in mils regardless of the job's native unit.
    Heuristic: if largest symbol > MIL_DETECT_RATIO × coordinate range, symbols
    are in mils and need a MILS_TO_MM correction factor.

    Returns MILS_TO_MM (0.0254) if correction needed, else 1.0.
    """
    lines = features_text.splitlines()
    symbols = parse_symbol_table(lines)
    if not symbols:
        return 1.0

    max_sym = max(
        (max(s.size_x, s.size_y) for s in symbols.values() if s.shape not in ('unknown', 'skip')),
        default=0.0
    )
    if max_sym <= 0:
        return 1.0

    coords_abs = []
    for line in lines:
        if line.startswith('P ') or line.startswith('L '):
            try:
                parts = line.split()
                coords_abs.append(abs(float(parts[1]) * uf))
                coords_abs.append(abs(float(parts[2]) * uf))
            except (IndexError, ValueError):
                pass
            if len(coords_abs) >= 100:
                break

    if not coords_abs:
        return 1.0

    max_coord = max(coords_abs)
    if max_coord < COORD_NONZERO_EPSILON:
        return 1.0

    if max_sym > max_coord * MIL_DETECT_RATIO:
        return MILS_TO_MM

    return 1.0


def load_user_symbols(job_root: str, uf: float) -> dict:
    """
    Load user-defined (complex) symbols from the job's symbols/ directory.

    Returns {symbol_name_lower: _ODBSymbol} with bounding-box approximation.
    """
    result = {}
    sym_dir = os.path.join(job_root, 'symbols')
    if not os.path.isdir(sym_dir):
        return result

    # Lazy import to avoid circular dependency at module load time
    from odb.features import read_features_text, parse_features_text, compute_bounds

    for sym_name in os.listdir(sym_dir):
        sym_path = os.path.join(sym_dir, sym_name)
        if not os.path.isdir(sym_path):
            continue
        feat_path = os.path.join(sym_path, 'features')
        text = read_features_text(feat_path)
        if text is None:
            continue
        try:
            inner_geoms, _, _, _, _ = parse_features_text(text, uf, set())
            if not inner_geoms:
                continue
            bounds = compute_bounds(inner_geoms)
            w = bounds[2] - bounds[0]
            h = bounds[3] - bounds[1]
            result[sym_name.lower()] = _ODBSymbol('rect', max(w, 0.01), max(h, 0.01), sym_name)
        except Exception:
            continue
    return result
