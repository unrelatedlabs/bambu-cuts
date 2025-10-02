#!/usr/bin/env python3
"""
DXF to SVG converter module.
Converts DXF files to SVG format, ensuring connected paths remain continuous.
"""

import math
from typing import List, Tuple, Dict, Any, Optional
import xml.etree.ElementTree as ET


class DXFParser:
    """Parser for DXF file format."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.entities = []

    def parse(self):
        """Parse DXF file and extract entities."""
        with open(self.filepath, 'r') as f:
            lines = [line.strip() for line in f.readlines()]

        # Find ENTITIES section
        i = 0
        while i < len(lines):
            if lines[i] == 'SECTION' and i + 2 < len(lines) and lines[i + 2] == 'ENTITIES':
                i += 3
                break
            i += 1

        # Parse entities
        while i < len(lines):
            if lines[i] == 'ENDSEC':
                break

            if lines[i] == 'SPLINE':
                entity = self._parse_spline(lines, i)
                self.entities.append(entity)
                i = entity['end_index']
            elif lines[i] == 'LINE':
                entity = self._parse_line(lines, i)
                self.entities.append(entity)
                i = entity['end_index']
            elif lines[i] == 'ARC':
                entity = self._parse_arc(lines, i)
                self.entities.append(entity)
                i = entity['end_index']
            else:
                i += 1

        return self.entities

    def _parse_spline(self, lines: List[str], start_idx: int) -> Dict[str, Any]:
        """Parse SPLINE entity."""
        i = start_idx + 1
        control_points = []
        knots = []
        degree = 3

        while i < len(lines):
            code = lines[i]
            if i + 1 >= len(lines):
                break
            value = lines[i + 1]

            if code == '0':  # Next entity
                break
            elif code == '71':
                degree = int(value)
            elif code == '40':
                knots.append(float(value))
            elif code == '10':
                x = float(value)
                y = float(lines[i + 3]) if i + 3 < len(lines) and lines[i + 2] == '20' else 0
                control_points.append((x, y))
                i += 4
                continue

            i += 2

        return {
            'type': 'SPLINE',
            'degree': degree,
            'control_points': control_points,
            'knots': knots,
            'end_index': i
        }

    def _parse_line(self, lines: List[str], start_idx: int) -> Dict[str, Any]:
        """Parse LINE entity."""
        i = start_idx + 1
        x1, y1, x2, y2 = 0, 0, 0, 0

        while i < len(lines):
            code = lines[i]
            if i + 1 >= len(lines):
                break
            value = lines[i + 1]

            if code == '0':  # Next entity
                break
            elif code == '10':
                x1 = float(value)
            elif code == '20':
                y1 = float(value)
            elif code == '11':
                x2 = float(value)
            elif code == '21':
                y2 = float(value)

            i += 2

        return {
            'type': 'LINE',
            'start': (x1, y1),
            'end': (x2, y2),
            'end_index': i
        }

    def _parse_arc(self, lines: List[str], start_idx: int) -> Dict[str, Any]:
        """Parse ARC entity."""
        i = start_idx + 1
        cx, cy, radius, start_angle, end_angle = 0, 0, 1, 0, 360

        while i < len(lines):
            code = lines[i]
            if i + 1 >= len(lines):
                break
            value = lines[i + 1]

            if code == '0':  # Next entity
                break
            elif code == '10':
                cx = float(value)
            elif code == '20':
                cy = float(value)
            elif code == '40':
                radius = float(value)
            elif code == '50':
                start_angle = float(value)
            elif code == '51':
                end_angle = float(value)

            i += 2

        return {
            'type': 'ARC',
            'center': (cx, cy),
            'radius': radius,
            'start_angle': start_angle,
            'end_angle': end_angle,
            'end_index': i
        }


class PathConnector:
    """Connects geometric entities into continuous paths."""

    def __init__(self, tolerance: float = 0.01):
        self.tolerance = tolerance

    def connect_paths(self, entities: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Connect entities into continuous paths."""
        if not entities:
            return []

        paths = []
        used = [False] * len(entities)

        for i in range(len(entities)):
            if used[i]:
                continue

            path = [entities[i]]
            used[i] = True

            # Try to extend the path forward and backward
            changed = True
            while changed:
                changed = False

                # Try to extend forward
                end_point = self._get_end_point(path[-1])
                for j in range(len(entities)):
                    if used[j]:
                        continue

                    start_point = self._get_start_point(entities[j])
                    if self._points_close(end_point, start_point):
                        path.append(entities[j])
                        used[j] = True
                        changed = True
                        break

                # Try to extend backward
                start_point = self._get_start_point(path[0])
                for j in range(len(entities)):
                    if used[j]:
                        continue

                    end_point_j = self._get_end_point(entities[j])
                    if self._points_close(end_point_j, start_point):
                        path.insert(0, entities[j])
                        used[j] = True
                        changed = True
                        break

            paths.append(path)

        return paths

    def _get_start_point(self, entity: Dict[str, Any]) -> Tuple[float, float]:
        """Get the start point of an entity."""
        if entity['type'] == 'LINE':
            return entity['start']
        elif entity['type'] == 'ARC':
            cx, cy = entity['center']
            r = entity['radius']
            angle = math.radians(entity['start_angle'])
            return (cx + r * math.cos(angle), cy + r * math.sin(angle))
        elif entity['type'] == 'SPLINE':
            return entity['control_points'][0]
        return (0, 0)

    def _get_end_point(self, entity: Dict[str, Any]) -> Tuple[float, float]:
        """Get the end point of an entity."""
        if entity['type'] == 'LINE':
            return entity['end']
        elif entity['type'] == 'ARC':
            cx, cy = entity['center']
            r = entity['radius']
            angle = math.radians(entity['end_angle'])
            return (cx + r * math.cos(angle), cy + r * math.sin(angle))
        elif entity['type'] == 'SPLINE':
            return entity['control_points'][-1]
        return (0, 0)

    def _points_close(self, p1: Tuple[float, float], p2: Tuple[float, float]) -> bool:
        """Check if two points are close within tolerance."""
        dx = p1[0] - p2[0]
        dy = p1[1] - p2[1]
        return math.sqrt(dx*dx + dy*dy) < self.tolerance


class SVGGenerator:
    """Generates SVG from connected paths."""

    def __init__(self):
        self.bounds = None

    def generate(self, paths: List[List[Dict[str, Any]]], output_file: str):
        """Generate SVG file from paths."""
        # Calculate bounds
        self._calculate_bounds(paths)

        if not self.bounds:
            return

        min_x, min_y, max_x, max_y = self.bounds
        width = max_x - min_x
        height = max_y - min_y
        padding = max(width, height) * 0.1

        # Create SVG root
        svg = ET.Element('svg', {
            'xmlns': 'http://www.w3.org/2000/svg',
            'viewBox': f'{min_x - padding} {-(max_y + padding)} {width + 2*padding} {height + 2*padding}',
            'width': f'{width + 2*padding}',
            'height': f'{height + 2*padding}'
        })

        # Add paths
        for path in paths:
            path_d = self._generate_path_d(path)
            if path_d:
                ET.SubElement(svg, 'path', {
                    'd': path_d,
                    'fill': 'none',
                    'stroke': 'black',
                    'stroke-width': '0.1'
                })

        # Write to file
        tree = ET.ElementTree(svg)
        ET.indent(tree, space='  ')
        tree.write(output_file, encoding='utf-8', xml_declaration=True)

    def _calculate_bounds(self, paths: List[List[Dict[str, Any]]]):
        """Calculate bounding box for all paths."""
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')

        for path in paths:
            for entity in path:
                if entity['type'] == 'LINE':
                    x1, y1 = entity['start']
                    x2, y2 = entity['end']
                    min_x = min(min_x, x1, x2)
                    max_x = max(max_x, x1, x2)
                    min_y = min(min_y, y1, y2)
                    max_y = max(max_y, y1, y2)
                elif entity['type'] == 'ARC':
                    cx, cy = entity['center']
                    r = entity['radius']
                    min_x = min(min_x, cx - r)
                    max_x = max(max_x, cx + r)
                    min_y = min(min_y, cy - r)
                    max_y = max(max_y, cy + r)
                elif entity['type'] == 'SPLINE':
                    for x, y in entity['control_points']:
                        min_x = min(min_x, x)
                        max_x = max(max_x, x)
                        min_y = min(min_y, y)
                        max_y = max(max_y, y)

        if min_x != float('inf'):
            self.bounds = (min_x, min_y, max_x, max_y)

    def _generate_path_d(self, path: List[Dict[str, Any]]) -> str:
        """Generate SVG path d attribute from entity path."""
        if not path:
            return ''

        d_parts = []

        for i, entity in enumerate(path):
            if entity['type'] == 'LINE':
                x1, y1 = entity['start']
                x2, y2 = entity['end']
                if i == 0:
                    d_parts.append(f'M {x1:.4f} {-y1:.4f}')
                d_parts.append(f'L {x2:.4f} {-y2:.4f}')

            elif entity['type'] == 'ARC':
                cx, cy = entity['center']
                r = entity['radius']
                start_angle = math.radians(entity['start_angle'])
                end_angle = math.radians(entity['end_angle'])

                x1 = cx + r * math.cos(start_angle)
                y1 = cy + r * math.sin(start_angle)
                x2 = cx + r * math.cos(end_angle)
                y2 = cy + r * math.sin(end_angle)

                if i == 0:
                    d_parts.append(f'M {x1:.4f} {-y1:.4f}')

                # Calculate arc sweep - DXF arcs go counter-clockwise
                angle_diff = end_angle - start_angle
                # Normalize to [0, 2π)
                while angle_diff < 0:
                    angle_diff += 2 * math.pi
                while angle_diff > 2 * math.pi:
                    angle_diff -= 2 * math.pi

                large_arc = 1 if angle_diff > math.pi else 0
                # Because SVG Y-axis is flipped, we need to invert the sweep direction
                sweep = 0

                d_parts.append(f'A {r:.4f} {r:.4f} 0 {large_arc} {sweep} {x2:.4f} {-y2:.4f}')

            elif entity['type'] == 'SPLINE':
                points = entity['control_points']
                if not points:
                    continue

                if i == 0:
                    d_parts.append(f'M {points[0][0]:.4f} {-points[0][1]:.4f}')

                # Use cubic bezier approximation for splines
                if len(points) >= 4:
                    for j in range(0, len(points) - 3, 3):
                        cp1 = points[j + 1]
                        cp2 = points[j + 2]
                        end = points[j + 3]
                        d_parts.append(f'C {cp1[0]:.4f} {-cp1[1]:.4f} {cp2[0]:.4f} {-cp2[1]:.4f} {end[0]:.4f} {-end[1]:.4f}')
                else:
                    # For shorter splines, just draw lines
                    for point in points[1:]:
                        d_parts.append(f'L {point[0]:.4f} {-point[1]:.4f}')

        return ' '.join(d_parts)


def convert_dxf_to_svg(dxf_file: str, svg_file: str):
    """Convert DXF file to SVG with continuous paths."""
    # Parse DXF
    parser = DXFParser(dxf_file)
    entities = parser.parse()

    # Connect paths
    connector = PathConnector(tolerance=0.1)
    paths = connector.connect_paths(entities)

    # Generate SVG
    generator = SVGGenerator()
    generator.generate(paths, svg_file)


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dxf2svg.py <input.dxf> [output.svg]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file + '.svg'

    convert_dxf_to_svg(input_file, output_file)
    print(f"Converted {input_file} to {output_file}")
