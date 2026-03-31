"""odb/archive.py — Archive extraction, unit detection, layer/step discovery."""

import io
import os
import re
import tarfile
import tempfile
from typing import Optional

from odb.constants import INCHES_TO_MM, UNITS_HEADER_SCAN_LINES
from odb.models import MATRIX_TYPE_MAP, StepRepeat


# ---------------------------------------------------------------------------
# Archive extraction
# ---------------------------------------------------------------------------

def extract_tgz(data: bytes) -> tuple:
    """
    Extract an ODB++ .tgz archive to a temporary directory.

    Returns (tmp_dir, job_root_path).
    job_root is the top-level directory inside the archive (the job name).

    Security: members with absolute paths or '..' are filtered to prevent
    path traversal attacks from untrusted archives.
    """
    tmp = tempfile.mkdtemp(prefix='odb_')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
        safe_members = [
            m for m in tf.getmembers()
            if not os.path.isabs(m.name) and '..' not in m.name.split('/')
        ]
        tf.extractall(tmp, members=safe_members)

    entries = [e for e in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, e))]
    job_name = entries[0] if entries else ''
    job_root = os.path.join(tmp, job_name) if job_name else tmp
    return tmp, job_root


# ---------------------------------------------------------------------------
# Unit detection
# ---------------------------------------------------------------------------

def read_units(job_root: str) -> str:
    """
    Detect coordinate units from misc/info (UNITS=INCH or UNITS=MM).
    Falls back to 'mm' if the file is missing or unreadable.
    """
    info_path = os.path.join(job_root, 'misc', 'info')
    try:
        with open(info_path, 'r', errors='ignore') as f:
            for line in f:
                line = line.strip().upper()
                if line.startswith('UNITS'):
                    if 'INCH' in line:
                        return 'inch'
                    if 'MM' in line or 'METRIC' in line:
                        return 'mm'
    except (OSError, IOError):
        pass
    return 'mm'


def units_from_text(text: str) -> Optional[str]:
    """
    Extract units from the header section of a features file text.
    Returns 'mm', 'inch', or None if not found.
    """
    for line in text.splitlines()[:UNITS_HEADER_SCAN_LINES]:
        upper = line.strip().upper()
        if upper.startswith('UNITS'):
            if 'INCH' in upper:
                return 'inch'
            if 'MM' in upper or 'METRIC' in upper:
                return 'mm'
    return None


def read_design_origin(job_root: str, uf: float) -> tuple:
    """Read design origin (x, y) in mm from misc/attrlist."""
    attrlist_path = os.path.join(job_root, 'misc', 'attrlist')
    ox, oy = 0.0, 0.0
    try:
        with open(attrlist_path, 'r', errors='ignore') as f:
            for line in f:
                if line.startswith('.design_origin_x='):
                    ox = float(line.strip().split('=')[1]) * uf
                elif line.startswith('.design_origin_y='):
                    oy = float(line.strip().split('=')[1]) * uf
    except (OSError, IOError, ValueError, IndexError):
        pass
    return ox, oy


# ---------------------------------------------------------------------------
# Matrix & layer discovery
# ---------------------------------------------------------------------------

def parse_matrix(job_root: str) -> list:
    """
    Parse matrix/matrix to get the ordered layer list.

    ODB++ matrix format is block-based:
        LAYER {
            ROW=4
            TYPE=SIGNAL
            NAME=SIGNAL_1
        }

    Returns list of (layer_name, layer_type_string) in file order.
    """
    matrix_path = os.path.join(job_root, 'matrix', 'matrix')
    layers = []

    try:
        with open(matrix_path, 'r', errors='ignore') as f:
            content = f.read()
    except (OSError, IOError):
        return layers

    block_pattern = re.compile(r'LAYER\s*\{([^}]+)\}', re.IGNORECASE | re.DOTALL)
    kv_pattern = re.compile(r'^\s*([A-Z_]+)\s*=\s*(.+?)\s*$', re.IGNORECASE | re.MULTILINE)

    for block_match in block_pattern.finditer(content):
        block_text = block_match.group(1)
        props = {m.group(1).upper(): m.group(2).strip() for m in kv_pattern.finditer(block_text)}

        name = props.get('NAME', '').strip()
        raw_type = props.get('TYPE', '').upper()

        if not name:
            continue

        layer_type = MATRIX_TYPE_MAP.get(raw_type, 'other')
        layers.append((name, layer_type))

    return layers


def scan_layers_dir(layers_dir: str) -> list:
    """
    Fallback when matrix/matrix is missing: scan the layers directory and
    classify by name heuristics.
    """
    if not os.path.isdir(layers_dir):
        return []

    name_patterns = [
        (re.compile(r'signal|copper|top|bot|inner|l\d', re.I), 'copper'),
        (re.compile(r'solder.?mask|mask_top|mask_bot', re.I),   'soldermask'),
        (re.compile(r'silk|legend|overlay', re.I),               'silkscreen'),
        (re.compile(r'paste|cream', re.I),                       'paste'),
        (re.compile(r'drill|via|hole', re.I),                    'drill'),
        (re.compile(r'outline|profile|board.?edge|contour', re.I), 'outline'),
    ]

    result = []
    for entry in sorted(os.listdir(layers_dir)):
        if not os.path.isdir(os.path.join(layers_dir, entry)):
            continue
        layer_type = 'other'
        for pattern, ltype in name_patterns:
            if pattern.search(entry):
                layer_type = ltype
                break
        result.append((entry, layer_type))
    return result


def find_step(job_root: str) -> str:
    """
    Find the primary step directory inside steps/.
    Returns the first directory found alphabetically.
    """
    steps_dir = os.path.join(job_root, 'steps')
    try:
        entries = sorted([
            e for e in os.listdir(steps_dir)
            if os.path.isdir(os.path.join(steps_dir, e))
        ])
    except OSError:
        return 'pcb'

    return entries[0] if entries else 'pcb'


# ---------------------------------------------------------------------------
# Step-repeat hierarchy
# ---------------------------------------------------------------------------

def _make_step_repeat(fields: dict, uf: float) -> StepRepeat:
    """Construct a StepRepeat from a parsed key=value field dict."""
    return StepRepeat(
        child_step=fields['NAME'],
        x=fields.get('X', 0.0) * uf,
        y=fields.get('Y', 0.0) * uf,
        dx=fields.get('DX', 0.0) * uf,
        dy=fields.get('DY', 0.0) * uf,
        nx=int(fields.get('NX', 1)),
        ny=int(fields.get('NY', 1)),
        angle=fields.get('ANGLE', 0.0),
    )


def parse_step_repeat(job_root: str, uf: float = 1.0) -> dict:
    """Parse STEP-REPEAT data from all stephdr files in the ODB++ archive.

    Args:
        job_root: Path to the extracted ODB++ job root directory.
        uf: Unit factor to convert coordinates to mm (25.4 for inches, 1.0 for mm).

    Returns:
        Dict mapping step name → list of StepRepeat entries.
    """
    steps_dir = os.path.join(job_root, 'steps')
    result = {}
    try:
        step_names = [
            e for e in os.listdir(steps_dir)
            if os.path.isdir(os.path.join(steps_dir, e))
        ]
    except OSError:
        return result

    for step_name in step_names:
        stephdr_path = os.path.join(steps_dir, step_name, 'stephdr')
        if not os.path.isfile(stephdr_path):
            continue
        try:
            with open(stephdr_path, 'r', errors='replace') as f:
                text = f.read()
        except OSError:
            continue

        repeats = []
        in_sr = False
        sr_fields = {}

        for line in text.splitlines():
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith('STEP-REPEAT'):
                in_sr = True
                sr_fields = {}
                continue

            if in_sr:
                if stripped == '' or stripped == '}' or upper.startswith('STEP-REPEAT'):
                    if sr_fields.get('NAME'):
                        repeats.append(_make_step_repeat(sr_fields, uf))
                    sr_fields = {}
                    if upper.startswith('STEP-REPEAT'):
                        continue
                    else:
                        in_sr = bool(sr_fields)
                        continue

                if '=' in stripped:
                    key, _, val = stripped.partition('=')
                    key = key.strip().upper()
                    val = val.strip().rstrip(';').strip()
                    if key == 'NAME':
                        sr_fields['NAME'] = val
                    elif key in ('X', 'Y', 'DX', 'DY', 'ANGLE'):
                        try:
                            sr_fields[key] = float(val)
                        except ValueError:
                            pass
                    elif key in ('NX', 'NY'):
                        try:
                            sr_fields[key] = int(val)
                        except ValueError:
                            pass

        # Flush last entry
        if sr_fields.get('NAME'):
            repeats.append(_make_step_repeat(sr_fields, uf))

        if repeats:
            result[step_name.lower()] = repeats

    return result
