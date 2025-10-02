#!/usr/bin/env python3
"""
SVG to G-code converter with drag knife support.
Handles trailing offset, lead-in moves, and corner compensation for free-swivel drag knives.
"""

import math
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional, Dict, Any
import re


class Point:
    """2D point helper class."""
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

    def distance(self, other: 'Point') -> float:
        """Calculate distance to another point."""
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def angle_to(self, other: 'Point') -> float:
        """Calculate angle to another point in radians."""
        return math.atan2(other.y - self.y, other.x - self.x)

    def offset(self, distance: float, angle: float) -> 'Point':
        """Create new point offset by distance and angle."""
        return Point(
            self.x + distance * math.cos(angle),
            self.y + distance * math.sin(angle)
        )

    def __repr__(self):
        return f"Point({self.x:.3f}, {self.y:.3f})"


class DragKnifeConfig:
    """Configuration for drag knife behavior."""
    def __init__(
        self,
        offset: float = 1.0,          # Trailing offset in mm
        lead_in: float = 2.0,          # Lead-in distance in mm
        corner_threshold: float = 60,  # Corner angle threshold in degrees (higher = only sharp corners)
        feed_rate: float = 1000,       # Feed rate in mm/min
        z_safe: float = 5.0,           # Safe Z height
        z_cut: float = 0.0,            # Cutting Z height
    ):
        self.offset = offset
        self.lead_in = lead_in
        self.corner_threshold = math.radians(corner_threshold)
        self.feed_rate = feed_rate
        self.z_safe = z_safe
        self.z_cut = z_cut


class SVGParser:
    """Parse SVG paths into point sequences."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.paths = []

    def parse(self) -> List[List[Point]]:
        """Parse SVG file and extract paths as point sequences."""
        tree = ET.parse(self.filepath)
        root = tree.getroot()

        # Handle namespaces
        ns = {'svg': 'http://www.w3.org/2000/svg'}

        # Find all path elements
        paths = root.findall('.//svg:path', ns)
        if not paths:
            paths = root.findall('.//path')

        for path_elem in paths:
            d = path_elem.get('d', '')
            if d:
                points = self._parse_path_d(d)
                if points:
                    self.paths.append(points)

        return self.paths

    def _parse_path_d(self, d: str) -> List[Point]:
        """Parse SVG path d attribute into points."""
        points = []

        # Tokenize the path data
        tokens = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?', d)

        i = 0
        current_pos = Point(0, 0)
        path_start = Point(0, 0)

        while i < len(tokens):
            cmd = tokens[i]
            i += 1

            if cmd in 'Mm':
                # Move to
                x = float(tokens[i])
                y = float(tokens[i + 1])
                i += 2

                if cmd == 'M':
                    current_pos = Point(x, y)
                else:  # relative
                    current_pos = Point(current_pos.x + x, current_pos.y + y)

                path_start = current_pos
                points.append(Point(current_pos.x, current_pos.y))

            elif cmd in 'Ll':
                # Line to
                x = float(tokens[i])
                y = float(tokens[i + 1])
                i += 2

                if cmd == 'L':
                    current_pos = Point(x, y)
                else:  # relative
                    current_pos = Point(current_pos.x + x, current_pos.y + y)

                points.append(Point(current_pos.x, current_pos.y))

            elif cmd in 'Hh':
                # Horizontal line
                x = float(tokens[i])
                i += 1

                if cmd == 'H':
                    current_pos = Point(x, current_pos.y)
                else:
                    current_pos = Point(current_pos.x + x, current_pos.y)

                points.append(Point(current_pos.x, current_pos.y))

            elif cmd in 'Vv':
                # Vertical line
                y = float(tokens[i])
                i += 1

                if cmd == 'V':
                    current_pos = Point(current_pos.x, y)
                else:
                    current_pos = Point(current_pos.x, current_pos.y + y)

                points.append(Point(current_pos.x, current_pos.y))

            elif cmd in 'Cc':
                # Cubic bezier - approximate with line segments
                x1 = float(tokens[i])
                y1 = float(tokens[i + 1])
                x2 = float(tokens[i + 2])
                y2 = float(tokens[i + 3])
                x = float(tokens[i + 4])
                y = float(tokens[i + 5])
                i += 6

                if cmd == 'c':  # relative
                    x1 += current_pos.x
                    y1 += current_pos.y
                    x2 += current_pos.x
                    y2 += current_pos.y
                    x += current_pos.x
                    y += current_pos.y

                # Approximate bezier with segments
                p0 = current_pos
                p1 = Point(x1, y1)
                p2 = Point(x2, y2)
                p3 = Point(x, y)

                segments = 20
                for j in range(1, segments + 1):
                    t = j / segments
                    # Cubic bezier formula
                    bx = (1-t)**3 * p0.x + 3*(1-t)**2*t * p1.x + 3*(1-t)*t**2 * p2.x + t**3 * p3.x
                    by = (1-t)**3 * p0.y + 3*(1-t)**2*t * p1.y + 3*(1-t)*t**2 * p2.y + t**3 * p3.y
                    points.append(Point(bx, by))

                current_pos = Point(x, y)

            elif cmd in 'Aa':
                # Arc - approximate with line segments
                rx = float(tokens[i])
                ry = float(tokens[i + 1])
                rotation = float(tokens[i + 2])
                large_arc = int(tokens[i + 3])
                sweep = int(tokens[i + 4])
                x = float(tokens[i + 5])
                y = float(tokens[i + 6])
                i += 7

                if cmd == 'a':  # relative
                    x += current_pos.x
                    y += current_pos.y

                # Simple arc approximation with line segments
                segments = 20
                for j in range(1, segments + 1):
                    t = j / segments
                    px = current_pos.x + t * (x - current_pos.x)
                    py = current_pos.y + t * (y - current_pos.y)
                    points.append(Point(px, py))

                current_pos = Point(x, y)

            elif cmd in 'Zz':
                # Close path
                if points and path_start:
                    if points[-1].distance(path_start) > 0.001:
                        points.append(Point(path_start.x, path_start.y))

        return points


class DragKnifeToolpath:
    """Generate drag knife toolpath with lead-in and corner handling."""

    def __init__(self, config: DragKnifeConfig):
        self.config = config

    def generate_toolpath(self, paths: List[List[Point]]) -> str:
        """Generate G-code from paths with drag knife compensation."""
        gcode = []

        # Header
        gcode.append("; Generated by svg2gcode with drag knife support")
        gcode.append(f"; Knife offset: {self.config.offset}mm")
        gcode.append(f"; Lead-in distance: {self.config.lead_in}mm")
        gcode.append("G21 ; mm mode")
        gcode.append("G90 ; absolute positioning")
        gcode.append(f"G0 Z{self.config.z_safe:.3f} ; raise to safe height")
        gcode.append(f"F{self.config.feed_rate} ; set feed rate")
        gcode.append("")

        # Process each path
        for path_idx, path in enumerate(paths):
            if len(path) < 2:
                continue

            gcode.append(f"; Path {path_idx + 1}")

            # Generate compensated path
            compensated = self._generate_compensated_path(path)

            if compensated:
                # Rapid move to start (with safe Z)
                start = compensated[0]
                gcode.append(f"G0 X{start.x:.4f} Y{start.y:.4f} ; move to start")
                gcode.append(f"G1 Z{self.config.z_cut:.3f} ; lower knife")

                # Cut the path
                for point in compensated[1:]:
                    gcode.append(f"G1 X{point.x:.4f} Y{point.y:.4f}")

                # Raise knife
                gcode.append(f"G0 Z{self.config.z_safe:.3f} ; raise knife")
                gcode.append("")

        # Footer
        gcode.append("; End of program")
        gcode.append(f"G0 Z{self.config.z_safe:.3f}")
        gcode.append("M2")

        return '\n'.join(gcode)

    def _generate_compensated_path(self, path: List[Point]) -> List[Point]:
        """Generate path with drag knife compensation."""
        if len(path) < 2:
            return path

        result = []

        # Calculate initial approach angle
        approach_angle = path[0].angle_to(path[1])

        # Add lead-in move (approach from behind)
        lead_in_point = path[0].offset(-self.config.lead_in, approach_angle)
        result.append(lead_in_point)

        # Add all points of the path
        for i in range(len(path)):
            curr_point = path[i]

            # Check if this is a sharp corner
            if i > 0 and i < len(path) - 1:
                prev_point = path[i - 1]
                next_point = path[i + 1]
                corner_angle = self._calculate_corner_angle(prev_point, curr_point, next_point)

                if abs(corner_angle) > self.config.corner_threshold:
                    # Sharp corner - add overcut compensation
                    self._add_corner_compensation(result, prev_point, curr_point, next_point, corner_angle)
                else:
                    # Not a sharp corner - just add the point
                    result.append(curr_point)
            else:
                # First or last point - no corner check needed
                result.append(curr_point)

        # Add lead-out move
        if len(path) >= 2:
            exit_angle = path[-2].angle_to(path[-1])
            lead_out_point = path[-1].offset(self.config.lead_in, exit_angle)
            result.append(lead_out_point)

        return result

    def _calculate_corner_angle(self, p1: Point, p2: Point, p3: Point) -> float:
        """Calculate the angle at corner p2 between segments p1->p2 and p2->p3."""
        angle1 = math.atan2(p2.y - p1.y, p2.x - p1.x)
        angle2 = math.atan2(p3.y - p2.y, p3.x - p2.x)

        # Calculate the turn angle
        diff = angle2 - angle1

        # Normalize to [-π, π]
        while diff > math.pi:
            diff -= 2 * math.pi
        while diff < -math.pi:
            diff += 2 * math.pi

        return diff

    def _add_corner_compensation(
        self,
        result: List[Point],
        prev: Point,
        corner: Point,
        next_pt: Point,
        corner_angle: float
    ):
        """Add compensation moves for sharp corners."""
        # Calculate angles
        approach_angle = prev.angle_to(corner)
        exit_angle = corner.angle_to(next_pt)

        # Simple drag knife compensation:
        # 1. Overcut past the corner along approach path
        # 2. Move back to corner (blade pivots naturally)
        # 3. Continue on exit path

        # Overcut distance: needs to be enough for blade to align
        overcut_dist = self.config.offset * 2.0
        overcut_point = corner.offset(overcut_dist, approach_angle)
        result.append(overcut_point)

        # Return to corner - the blade will naturally pivot during this move
        result.append(corner)


def convert_svg_to_gcode(
    svg_file: str,
    gcode_file: str,
    knife_offset: float = 1.0,
    lead_in: float = 2.0,
    corner_threshold: float = 30,
    feed_rate: float = 1000
):
    """Convert SVG file to G-code with drag knife support."""

    # Configure drag knife
    config = DragKnifeConfig(
        offset=knife_offset,
        lead_in=lead_in,
        corner_threshold=corner_threshold,
        feed_rate=feed_rate
    )

    # Parse SVG
    parser = SVGParser(svg_file)
    paths = parser.parse()

    if not paths:
        print("No paths found in SVG file")
        return

    print(f"Found {len(paths)} path(s)")

    # Generate toolpath
    toolpath = DragKnifeToolpath(config)
    gcode = toolpath.generate_toolpath(paths)

    # Write to file
    with open(gcode_file, 'w') as f:
        f.write(gcode)

    print(f"Generated {gcode_file}")


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python svg2gcode.py <input.svg> [output.gcode] [options]")
        print("\nOptions:")
        print("  --offset <mm>      Knife trailing offset (default: 1.0mm)")
        print("  --lead-in <mm>     Lead-in distance (default: 2.0mm)")
        print("  --corner <deg>     Corner threshold angle (default: 60 deg)")
        print("  --feed <mm/min>    Feed rate (default: 1000 mm/min)")
        print("\nExample:")
        print("  python svg2gcode.py design.svg --offset 0.5 --lead-in 3.0")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else input_file.replace('.svg', '.gcode')

    # Parse optional arguments
    knife_offset = 1.0
    lead_in = 2.0
    corner_threshold = 60
    feed_rate = 1000

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == '--offset' and i + 1 < len(sys.argv):
            knife_offset = float(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--lead-in' and i + 1 < len(sys.argv):
            lead_in = float(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--corner' and i + 1 < len(sys.argv):
            corner_threshold = float(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--feed' and i + 1 < len(sys.argv):
            feed_rate = float(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    convert_svg_to_gcode(
        input_file,
        output_file,
        knife_offset=knife_offset,
        lead_in=lead_in,
        corner_threshold=corner_threshold,
        feed_rate=feed_rate
    )
