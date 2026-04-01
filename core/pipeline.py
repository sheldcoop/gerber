"""
core/pipeline.py — Main ODB++ rendering pipeline (_render_pipeline).

Called by render_odb_to_cam in gerber_renderer.py after cache miss.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from gerbonara.graphic_objects import Flash, Line, Region
from gerbonara.apertures import CircleAperture
from gerbonara.utils import MM

from odb_parser import (
    _extract_odb_tgz,
    _read_units,
    _parse_matrix,
    _scan_layers_dir,
    _find_step,
    _read_features_text,
    _parse_features_text,
    _compute_bounds,
    _load_user_symbols,
    _parse_step_repeat,
)

from core.cache import _svg_to_data_url_fast
from core.layer_renderer import _parse_layer_to_gerbonara
from core.step_layout import compute_unit_positions
from core.panel_builder import build_panel_svg

_DRILL_SPAN_RE = re.compile(r'^\d+[FB](CO)?[-_]\d+[FB](CO)?', re.IGNORECASE)
_IMPEDANCE_RE = re.compile(r'^L\d{2}_', re.IGNORECASE)
_RENDERABLE_TYPES = {'copper', 'signal', 'power', 'mixed', 'soldermask', 'drill'}

LAYER_COLORS = ['#b87333', '#4488cc', '#44aa44', '#9966bb', '#cc6644',
                '#44ccaa', '#cc4466', '#44cccc']


def _render_pipeline(data: bytes, filename: str, layer_filter: list):
    """Parse ODB++ archive and render each layer as CAM-quality SVG.

    Returns a RenderedODB instance (no caching — caller handles that).
    """
    import shutil
    # Lazy import to avoid circular dependency with gerber_renderer
    from gerber_renderer import RenderedLayer, RenderedODB

    tmp_dir, job_root = _extract_odb_tgz(data)
    warnings = []

    try:
        # ── Phase 1: units, step name, layer list ─────────────────────────
        units = _read_units(job_root)
        uf = 25.4 if units == 'inch' else 1.0

        steps_dir = os.path.join(job_root, 'steps')

        # Parse step-repeat hierarchy first so we can find the leaf step dynamically
        step_hierarchy = _parse_step_repeat(job_root, uf)

        # Leaf step = appears as a child in hierarchy but has no step-repeat entries
        # of its own (panel → qtr_panel → cluster → unit: 'unit' is the leaf)
        _all_ch = {sr.child_step.lower() for rpts in step_hierarchy.values() for sr in rpts}
        _leaves = [s for s in _all_ch if s not in step_hierarchy]
        step_name = (_leaves[0] if _leaves else
                     'unit' if os.path.isdir(os.path.join(steps_dir, 'unit')) else
                     _find_step(job_root))

        user_sym_map = _load_user_symbols(job_root, uf)
        matrix_layers = _parse_matrix(job_root)
        if not matrix_layers:
            matrix_layers = _scan_layers_dir(os.path.join(job_root, 'steps', step_name, 'layers'))

        if layer_filter:
            selected = [(n, t) for n, t in matrix_layers
                        if n.lower() in [l.lower() for l in layer_filter]]
        else:
            selected = [
                (n, t) for n, t in matrix_layers
                if t in _RENDERABLE_TYPES and not _IMPEDANCE_RE.match(n)
            ]

        # ── Phase 2: parse layers in parallel ─────────────────────────────
        def _process_layer(args):
            name, ltype = args
            # Name-based drill reclassification: ODB++ sometimes exports drill span
            # layers (e.g. "2B-3B", "2F-3F") with matrix TYPE=MIXED or SIGNAL.
            if ltype != 'drill' and _DRILL_SPAN_RE.match(name):
                ltype = 'drill'
            result = _parse_layer_to_gerbonara(job_root, step_name, name, uf, user_sym_map)
            if result is None:
                return name, ltype, None, None, f"Layer '{name}': no features found"
            gf, stats = result
            if not gf.objects:
                return name, ltype, None, None, f"Layer '{name}': 0 objects parsed"
            if ltype == 'drill':
                # Strip Region objects — only flash/line features are drill holes/vias.
                gf.objects = [o for o in gf.objects if not isinstance(o, Region)]
                if not gf.objects:
                    return name, ltype, None, None, f"Layer '{name}': no drill features after region strip"
                # Coordinate unit fix: drill features sometimes stored in inches even
                # when file declares mm → bounding box will be tiny (< 5 mm).
                if uf == 1.0:
                    bb = gf.bounding_box(MM)
                    extent = max(abs(bb[1][0] - bb[0][0]), abs(bb[1][1] - bb[0][1]))
                    if extent < 5.0:
                        result2 = _parse_layer_to_gerbonara(job_root, step_name, name, 25.4, user_sym_map)
                        if result2 and result2[0].objects:
                            gf, stats = result2
                            gf.objects = [o for o in gf.objects if not isinstance(o, Region)]
                # Aperture sanity: if any aperture looks mils-sized (> 1 mm), apply
                # 0.0254 correction (mils → mm).
                def _ap_dim(ap):
                    if hasattr(ap, 'diameter'):
                        return ap.diameter
                    return max(getattr(ap, 'w', 0), getattr(ap, 'h', 0))
                _dims = [_ap_dim(obj.aperture) for obj in gf.objects if isinstance(obj, Flash)]
                if _dims:
                    _max_ap = max(_dims)
                    if _max_ap > 1.0:
                        _scale = 0.0254  # mils → mm
                        for obj in gf.objects:
                            if isinstance(obj, Flash):
                                _d = _ap_dim(obj.aperture)
                                obj.aperture = CircleAperture(diameter=max(_d * _scale, 0.02), unit=MM)
                        _dims = [_ap_dim(obj.aperture) for obj in gf.objects if isinstance(obj, Flash)]
                        _max_ap = max(_dims) if _dims else 0.0
                # Density-based overlap correction: if circles physically overlap
                # (diameter > estimated inter-via spacing), scale down.
                if _dims and len(_dims) >= 10:
                    _bb_drill = gf.bounding_box(MM)
                    _area = (abs(_bb_drill[1][0] - _bb_drill[0][0]) *
                             abs(_bb_drill[1][1] - _bb_drill[0][1]))
                    if _area > 0.01:
                        _est_spacing = (_area / len(_dims)) ** 0.5
                        if _max_ap > _est_spacing * 0.9:
                            _sf = (0.25 * _est_spacing) / _max_ap
                            for obj in gf.objects:
                                if isinstance(obj, Flash):
                                    _d = _ap_dim(obj.aperture)
                                    obj.aperture = CircleAperture(
                                        diameter=max(_d * _sf, 0.02), unit=MM
                                    )
            return name, ltype, gf, stats, None

        parse_results = []
        with ThreadPoolExecutor(max_workers=min(4, len(selected))) as executor:
            futures = {executor.submit(_process_layer, item): item for item in selected}
            for future in as_completed(futures):
                parse_results.append(future.result())

        # ── Phase 3: render SVGs in parallel ──────────────────────────────
        valid_results = []
        for name, ltype, gf, stats, warn in parse_results:
            if warn:
                warnings.append(warn)
            elif gf is not None:
                valid_results.append((name, ltype, gf, stats))

        layer_color_map = {
            name: LAYER_COLORS[i % len(LAYER_COLORS)]
            for i, (name, _, _, _) in enumerate(valid_results)
        }

        rendered_layers = {}
        all_bounds = []

        def _render_layer(name, ltype, gf, stats):
            fg_color = '#FFD700' if ltype == 'drill' else '#b87333'
            svg_str = str(gf.to_svg(fg=fg_color, bg='#060A06'))
            svg_data_url = _svg_to_data_url_fast(svg_str)

            stack_color = layer_color_map[name]
            stack_svg = str(gf.to_svg(fg=stack_color, bg='#060A06'))
            color_urls = {stack_color: _svg_to_data_url_fast(stack_svg)}

            bb = gf.bounding_box(MM)
            bounds = (bb[0][0], bb[0][1], bb[1][0], bb[1][1])
            total = stats['flash'] + stats['line'] + stats['region'] + stats['clear']

            return name, RenderedLayer(
                name=name,
                layer_type=ltype,
                svg_string=svg_str,
                svg_data_url=svg_data_url,
                color_svg_urls=color_urls,
                gerber_file=gf,
                bounds=bounds,
                feature_count=total,
                stats=stats,
            ), bounds

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(valid_results)))) as executor:
            render_futures = {
                executor.submit(_render_layer, name, ltype, gf, stats): name
                for name, ltype, gf, stats in valid_results
            }
            for future in as_completed(render_futures):
                name, layer_obj, bounds = future.result()
                rendered_layers[name] = layer_obj
                # Only copper layers drive board_bounds used for centering.
                # Soldermask/drill extend beyond the board profile and inflate bounds.
                if layer_obj.layer_type in ('copper', 'signal', 'power', 'mixed', 'outline'):
                    all_bounds.append(bounds)

        # ── Phase 4: aggregate board bounds (copper layers only) ──────────
        if all_bounds:
            board_bounds = (
                min(b[0] for b in all_bounds),
                min(b[1] for b in all_bounds),
                max(b[2] for b in all_bounds),
                max(b[3] for b in all_bounds),
            )
        else:
            board_bounds = (0, 0, 1, 1)

        # ── Phase 5: clip panel-scale drill layers to unit bounds ─────────
        # 2B-3B / 2F-3F store all panel vias in one file. Filter to unit bounds.
        _tol = 1.0  # mm tolerance around unit bounds for edge vias
        _ux0, _uy0, _ux1, _uy1 = board_bounds
        for _dname, _dlyr in list(rendered_layers.items()):
            if _dlyr.layer_type != 'drill' or _dlyr.gerber_file is None:
                continue
            _gf = _dlyr.gerber_file
            _bb = _gf.bounding_box(MM)
            _ext = max(abs(_bb[1][0] - _bb[0][0]), abs(_bb[1][1] - _bb[0][1]))
            if _ext <= 100:
                continue  # already unit-scale, skip
            _kept = []
            for _obj in _gf.objects:
                if isinstance(_obj, Flash):
                    if (_ux0 - _tol <= _obj.x <= _ux1 + _tol and
                            _uy0 - _tol <= _obj.y <= _uy1 + _tol):
                        _kept.append(_obj)
                elif isinstance(_obj, Line):
                    mx = (_obj.x1 + _obj.x2) / 2
                    my = (_obj.y1 + _obj.y2) / 2
                    if (_ux0 - _tol <= mx <= _ux1 + _tol and
                            _uy0 - _tol <= my <= _uy1 + _tol):
                        _kept.append(_obj)
            if not _kept:
                del rendered_layers[_dname]
                warnings.append(f"Layer '{_dname}': no drill features within unit bounds")
                continue
            _gf.objects = _kept
            _fg = '#FFD700'
            _svg2 = str(_gf.to_svg(fg=_fg, bg='#060A06'))
            _bb2 = _gf.bounding_box(MM)
            _bounds2 = (_bb2[0][0], _bb2[0][1], _bb2[1][0], _bb2[1][1])
            _dlyr.svg_string = _svg2
            _dlyr.svg_data_url = _svg_to_data_url_fast(_svg2)
            _dlyr.bounds = _bounds2
            _dlyr.color_svg_urls = {
                next(iter(_dlyr.color_svg_urls), _fg): _svg_to_data_url_fast(
                    str(_gf.to_svg(fg=next(iter(_dlyr.color_svg_urls), _fg), bg='#060A06'))
                )
            }

        # ── Phase 6: compute panel layout from step-repeat + profile ──────
        panel_layout = None
        if step_hierarchy:
            unit_w = board_bounds[2] - board_bounds[0]
            unit_h = board_bounds[3] - board_bounds[1]

            # Detect InCAM Pro inches quirk FIRST so uf is correct for profile parsing.
            # If smallest step-repeat spacing < 5 mm but × 25.4 matches copper extent,
            # coordinates are in inches — re-parse with the correct factor.
            _all_spacings = []
            for _sr_list in step_hierarchy.values():
                for _sr in _sr_list:
                    if _sr.dx > 0: _all_spacings.append(_sr.dx)
                    if _sr.dy > 0: _all_spacings.append(_sr.dy)
            if _all_spacings and unit_w > 10:
                _min_spacing = min(_all_spacings)
                if _min_spacing < 5.0 and _min_spacing * 25.4 > unit_w * 0.8:
                    step_hierarchy = _parse_step_repeat(job_root, 25.4)
                    uf = 25.4  # profile coordinates are also in inches — update uf

            # Parse profile layer for accurate unit dimensions.
            try:
                profile_path = os.path.join(job_root, 'steps', step_name, 'profile')
                profile_text = _read_features_text(profile_path)
                if profile_text is None:
                    profile_text = _read_features_text(profile_path + '.Z')

                if not profile_text:
                    warnings.append(f"⚠️ Profile: file not found at steps/{step_name}/profile")
                else:
                    warnings.append(f"📄 Profile: found at steps/{step_name}/profile ({len(profile_text)} chars), uf={uf}")
                    unknown_symbols_dummy = set()
                    geoms, widths, warns, _, _ = _parse_features_text(profile_text, uf, unknown_symbols_dummy)

                    if not geoms:
                        import re as _re_prof
                        _outline_xs, _outline_ys = [], []
                        for _pline in profile_text.splitlines():
                            _pline = _pline.strip()
                            if _pline.startswith(('OB ', 'OS ')):
                                _pts = _re_prof.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', _pline)
                                if len(_pts) >= 2:
                                    _outline_xs.append(float(_pts[0]) * uf)
                                    _outline_ys.append(float(_pts[1]) * uf)
                        warnings.append(f"📄 Profile OB/OS fallback: {len(_outline_xs)} points, uf={uf}")
                        if _outline_xs and _outline_ys:
                            pb = (min(_outline_xs), min(_outline_ys),
                                  max(_outline_xs), max(_outline_ys))
                        else:
                            pb = None
                    else:
                        pb = _compute_bounds(geoms)

                    if pb:
                        profile_w = pb[2] - pb[0]
                        profile_h = pb[3] - pb[1]
                        # Profile is the authoritative board edge. Only reject if
                        # physically nonsensical (< 1 mm or > 800 mm).
                        if 1.0 < profile_w < 800.0 and 1.0 < profile_h < 800.0:
                            unit_w = profile_w
                            unit_h = profile_h
                            warnings.append(f"✅ Unit size from board profile: {unit_w:.2f}×{unit_h:.2f} mm")
            except Exception as e:
                warnings.append(f"⚠️ Could not parse profile layer ({e}) — using copper bounds")

            # Derive panel frame dimensions from ODB++ top-level step profile.
            _panel_w, _panel_h = None, None
            try:
                _all_ch2 = {sr.child_step.lower() for rpts in step_hierarchy.values() for sr in rpts}
                _top_steps = set(step_hierarchy.keys()) - _all_ch2
                _top = 'panel' if 'panel' in _top_steps else (sorted(_top_steps)[0] if _top_steps else None)
                if _top:
                    _pp = os.path.join(job_root, 'steps', _top, 'profile')
                    _pt = _read_features_text(_pp) or _read_features_text(_pp + '.Z')
                    if _pt:
                        _pg, _, _, _, _ = _parse_features_text(_pt, uf, set())
                        if not _pg:
                            import re as _re_pan
                            _pxs, _pys = [], []
                            for _pl2 in _pt.splitlines():
                                _pl2 = _pl2.strip()
                                if _pl2.startswith(('OB ', 'OS ')):
                                    _pp2 = _re_pan.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', _pl2)
                                    if len(_pp2) >= 2:
                                        _pxs.append(float(_pp2[0]) * uf)
                                        _pys.append(float(_pp2[1]) * uf)
                            _pb = (min(_pxs), min(_pys), max(_pxs), max(_pys)) if _pxs else None
                        else:
                            _pb = _compute_bounds(_pg)
                        if _pb:
                            _panel_w = _pb[2] - _pb[0]
                            _panel_h = _pb[3] - _pb[1]
                            warnings.append(f"📐 Panel size from ODB++: {_panel_w:.1f}×{_panel_h:.1f} mm")
            except Exception:
                pass  # Fall back to content-only bounds

            panel_layout = compute_unit_positions(
                step_hierarchy, (unit_w, unit_h),
                panel_width=_panel_w, panel_height=_panel_h,
            )

        # ── Phase 7: build panel SVG for first copper layer ───────────────
        if panel_layout and rendered_layers:
            _first_copper = next(
                (lo for lo in rendered_layers.values() if lo.layer_type != 'drill'),
                None
            )
            if _first_copper:
                try:
                    _first_copper.panel_svg_data_url = build_panel_svg(
                        _first_copper.svg_string, panel_layout
                    )
                except Exception:
                    pass

        # ── Phase 8: assemble result ───────────────────────────────────────
        return RenderedODB(
            layers=rendered_layers,
            board_bounds=board_bounds,
            step_name=step_name,
            units=units,
            panel_layout=panel_layout,
            warnings=warnings,
        )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
