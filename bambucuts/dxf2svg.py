#!/usr/bin/env python3
"""
DXF to SVG converter module.

Converts DXF files to SVG, keeping connected geometry as continuous paths.
Uses ezdxf to robustly flatten every supported entity type (LINE, ARC,
CIRCLE, ELLIPSE, LWPOLYLINE, POLYLINE, SPLINE, ...) into polylines, so the
converter is not limited to the handful of entity types a hand-rolled parser
would understand.
"""

import math
import xml.etree.ElementTree as ET
from typing import List, Tuple

import ezdxf
from ezdxf import path as ezdxf_path

# Maximum distance between the true curve and its flattened approximation, in
# drawing units (mm for typical DXF). Smaller = smoother curves, more points.
FLATTENING_DISTANCE = 0.05


def _entity_polylines(msp) -> List[Tuple[List[Tuple[float, float]], bool]]:
    """Flatten every drawable entity in a layout into (points, is_closed)."""
    polylines = []
    for entity in msp:
        try:
            path = ezdxf_path.make_path(entity)
        except (TypeError, ValueError):
            # Non-path entities (POINT, TEXT, INSERT without geometry, ...).
            continue

        points = [(p.x, p.y) for p in path.flattening(FLATTENING_DISTANCE)]
        if len(points) < 2:
            continue
        polylines.append((points, path.is_closed))

    return polylines


def _build_path_d(points: List[Tuple[float, float]], is_closed: bool) -> str:
    """Build an SVG path 'd' string, flipping Y (DXF is Y-up, SVG is Y-down)."""
    d_parts = [f'M {points[0][0]:.4f} {-points[0][1]:.4f}']
    for x, y in points[1:]:
        d_parts.append(f'L {x:.4f} {-y:.4f}')
    if is_closed:
        d_parts.append('Z')
    return ' '.join(d_parts)


def convert_dxf_to_svg(dxf_file: str, svg_file: str):
    """Convert a DXF file to SVG with continuous, Y-flipped paths."""
    doc = ezdxf.readfile(dxf_file)
    polylines = _entity_polylines(doc.modelspace())

    if not polylines:
        raise ValueError(
            f"No drawable geometry found in {dxf_file}. "
            "The file may only contain points/text or unsupported entities."
        )

    # Bounding box across all flattened points.
    xs = [x for points, _ in polylines for x, _ in points]
    ys = [y for points, _ in polylines for _, y in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x
    height = max_y - min_y

    # No padding: the SVG (and the G-code derived from it) must match the real
    # part dimensions exactly. Y is flipped, so the top edge is -max_y.
    svg = ET.Element('svg', {
        'xmlns': 'http://www.w3.org/2000/svg',
        'viewBox': f'{min_x} {-max_y} {width} {height}',
        'width': f'{width}',
        'height': f'{height}',
    })

    for points, is_closed in polylines:
        ET.SubElement(svg, 'path', {
            'd': _build_path_d(points, is_closed),
            'fill': 'none',
            'stroke': 'black',
            'stroke-width': '0.1',
        })

    tree = ET.ElementTree(svg)
    ET.indent(tree, space='  ')
    tree.write(svg_file, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dxf2svg.py <input.dxf> [output.svg]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file + '.svg'

    convert_dxf_to_svg(input_file, output_file)
    print(f"Converted {input_file} to {output_file}")
