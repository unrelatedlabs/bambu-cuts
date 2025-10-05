#!/usr/bin/env python3
"""
SVG Path Joiner - Remove M Commands with Regex

This version removes all M commands except the first one using regex replacement.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional, Dict
from svgpathtools import svg2paths, wsvg, Path, Line, CubicBezier, QuadraticBezier, Arc
from shapely.geometry import LineString, Point
import math
import re


class SVGPathJoinerRemoveMRegex:
    """Main class for creating truly continuous SVG paths by removing M commands with regex."""
    
    def __init__(self, tolerance: float = 0.001):
        """
        Initialize the SVG Path Joiner.
        
        Args:
            tolerance: Maximum distance between endpoints to consider them matching
        """
        self.tolerance = tolerance
        self.paths = []
        self.attributes = []
        self.joined_paths = []
        self.joined_attributes = []
    
    def load_svg(self, svg_file: str) -> None:
        """
        Load SVG file and extract paths.
        
        Args:
            svg_file: Path to SVG file
        """
        self.paths, self.attributes = svg2paths(svg_file)
        print(f"Loaded {len(self.paths)} paths from {svg_file}")
    
    def join_paths(self) -> Tuple[List[Path], List[dict]]:
        """
        Join paths into continuous paths by removing M commands.
        Only joins paths that are actually close enough to be connected.
        
        Returns:
            Tuple of (joined_paths, joined_attributes)
        """
        if not self.paths:
            return [], []
        
        # Group paths into connected components
        connected_components = self._find_connected_components()
        
        # Join each connected component
        joined_paths = []
        joined_attributes = []
        
        for component in connected_components:
            if len(component) == 1:
                # Single path, no joining needed
                joined_paths.append(self.paths[component[0]])
                joined_attributes.append(self.attributes[component[0]])
            else:
                # Multiple paths to join
                joined_path = self._join_path_component(component)
                if joined_path:
                    joined_paths.append(joined_path)
                    joined_attributes.append(self.attributes[component[0]])
        
        self.joined_paths = joined_paths
        self.joined_attributes = joined_attributes
        
        print(f"Joined {len(self.paths)} paths into {len(joined_paths)} continuous paths")
        return self.joined_paths, self.joined_attributes
    
    def _find_connected_components(self) -> List[List[int]]:
        """
        Find connected components of paths using Shapely for accurate distance calculation.
        
        Returns:
            List of connected components (each component is a list of path indices)
        """
        from shapely.geometry import Point
        
        # Create a graph of connected paths
        graph = {i: [] for i in range(len(self.paths))}
        
        for i in range(len(self.paths)):
            for j in range(i + 1, len(self.paths)):
                if self._paths_connect(self.paths[i], self.paths[j]):
                    graph[i].append(j)
                    graph[j].append(i)
        
        # Find connected components using DFS
        visited = set()
        components = []
        
        for start_node in range(len(self.paths)):
            if start_node not in visited:
                component = []
                stack = [start_node]
                
                while stack:
                    node = stack.pop()
                    if node not in visited:
                        visited.add(node)
                        component.append(node)
                        stack.extend(graph[node])
                
                components.append(component)
        
        return components
    
    def _paths_connect(self, path1: Path, path2: Path) -> bool:
        """
        Check if two paths can be connected using Shapely for accurate distance calculation.
        
        Args:
            path1: First path
            path2: Second path
            
        Returns:
            True if paths can be connected
        """
        from shapely.geometry import Point
        
        p1_start = Point(path1.start.real, path1.start.imag)
        p1_end = Point(path1.end.real, path1.end.imag)
        p2_start = Point(path2.start.real, path2.start.imag)
        p2_end = Point(path2.end.real, path2.end.imag)
        
        return (p1_end.distance(p2_start) <= self.tolerance or
                p1_start.distance(p2_end) <= self.tolerance or
                p1_end.distance(p2_end) <= self.tolerance or
                p1_start.distance(p2_start) <= self.tolerance)
    
    def _join_path_component(self, component: List[int]) -> Optional[Path]:
        """
        Join a connected component of paths.
        
        Args:
            component: List of path indices to join
            
        Returns:
            Joined Path object or None if joining fails
        """
        if not component:
            return None
        
        if len(component) == 1:
            return self.paths[component[0]]
        
        # Start with the first path
        current_path = self.paths[component[0]]
        remaining_paths = [self.paths[i] for i in component[1:]]
        
        # Collect all segments in order, preserving original curve types
        all_segments = list(current_path)
        
        while remaining_paths:
            # Find the best path to connect
            best_idx = None
            best_connection = None
            best_distance = float('inf')
            
            for i, path in enumerate(remaining_paths):
                connection = self._find_best_connection(all_segments, path)
                if connection:
                    distance = connection[1]
                    if distance < best_distance:
                        best_distance = distance
                        best_idx = i
                        best_connection = connection
            
            if best_idx is None:
                # No more connections possible
                break
            
            # Connect the best path
            path_to_connect = remaining_paths[best_idx]
            connection_type = best_connection[0]
            
            # Add segments based on connection type, preserving original curve types
            # Only join if endpoints actually touch - don't add connecting lines
            if connection_type == 'end_to_start':
                # Add path segments only if they actually touch
                all_segments.extend(path_to_connect)
            elif connection_type == 'start_to_end':
                # Add path segments at the beginning only if they actually touch
                all_segments = list(path_to_connect) + all_segments
            elif connection_type == 'end_to_end':
                # Add reversed path segments only if they actually touch
                all_segments.extend(path_to_connect.reversed())
            elif connection_type == 'start_to_start':
                # Add reversed path segments at the beginning only if they actually touch
                all_segments = list(path_to_connect.reversed()) + all_segments
            
            # Remove connected path
            remaining_paths.pop(best_idx)
        
        # Create new path from all segments
        return Path(*all_segments)
    
    def _find_best_connection(self, segments: List, path: Path) -> Optional[Tuple[str, float]]:
        """
        Find the best way to connect a list of segments to a path.
        
        Args:
            segments: List of path segments
            path: Path to connect to
            
        Returns:
            Tuple of (connection_type, distance) or None if no connection
        """
        from shapely.geometry import Point
        
        if not segments:
            return None
        
        first_point = Point(segments[0].start.real, segments[0].start.imag)
        last_point = Point(segments[-1].end.real, segments[-1].end.imag)
        p_start = Point(path.start.real, path.start.imag)
        p_end = Point(path.end.real, path.end.imag)
        
        connections = [
            ('end_to_start', last_point.distance(p_start)),
            ('start_to_end', first_point.distance(p_end)),
            ('end_to_end', last_point.distance(p_end)),
            ('start_to_start', first_point.distance(p_start))
        ]
        
        # Find the closest connection within tolerance
        valid_connections = [(conn_type, dist) for conn_type, dist in connections 
                           if dist <= self.tolerance]
        
        if not valid_connections:
            return None
        
        # Return the closest connection
        return min(valid_connections, key=lambda x: x[1])
    
    def _points_close(self, p1: complex, p2: complex) -> bool:
        """
        Check if two complex points are close enough to connect.
        
        Args:
            p1: First point as complex number
            p2: Second point as complex number
            
        Returns:
            True if points are close enough
        """
        distance = abs(p1 - p2)
        return distance <= self.tolerance
    
    def save_svg(self, output_file: str) -> None:
        """
        Save joined paths to SVG file by manually constructing the path data string.
        
        Args:
            output_file: Output SVG file path
        """
        if not self.joined_paths:
            print("No joined paths to save")
            return
        
        # Get bounds from the original paths
        if self.paths:
            min_x = min(path.bbox()[0] for path in self.paths if path.bbox())
            max_x = max(path.bbox()[1] for path in self.paths if path.bbox())
            min_y = min(path.bbox()[2] for path in self.paths if path.bbox())
            max_y = max(path.bbox()[3] for path in self.paths if path.bbox())
            width = max_x - min_x
            height = max_y - min_y
            viewbox = f"{min_x} {min_y} {width} {height}"
        else:
            viewbox = "0 0 200 200"
        
        # Create SVG content compatible with svg-to-gcode library
        width = max_x - min_x
        height = max_y - min_y
        
        # Generate path elements for each joined path
        path_elements = []
        for i, path in enumerate(self.joined_paths):
            path_data = self._construct_path_data(path)
            # Don't remove M commands - _construct_path_data already handles this correctly
            path_elements.append(f'  <path\n     d="{path_data}"\n     id="path_{i}"\n     style="fill:none;stroke:#000000;stroke-width:0.1" />')
        
        svg_content = f'''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg
   width="{width}mm"
   height="{height}mm"
   viewBox="{viewbox}"
   version="1.1"
   id="svg1"
   xmlns="http://www.w3.org/2000/svg"
   xmlns:svg="http://www.w3.org/2000/svg">
{chr(10).join(path_elements)}
</svg>'''
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(svg_content)
        
        print(f"Saved {len(self.joined_paths)} continuous paths to {output_file}")
    
    def _construct_path_data(self, path: Path) -> str:
        """
        Construct path data string from a single path.
        Preserves M commands for disconnected segments.

        Args:
            path: SVG path object

        Returns:
            Path data string
        """
        path_data = ""

        for i, segment in enumerate(path):
            # Check if this segment is disconnected from the previous one
            if i == 0:
                # First segment - always start with M command
                path_data += f"M {segment.start.real},{segment.start.imag}"
            elif not self._points_close(path[i-1].end, segment.start):
                # Disconnected segment - add M command
                path_data += f" M {segment.start.real},{segment.start.imag}"

            # Add the segment based on its type
            if isinstance(segment, Line):
                path_data += f" L {segment.end.real},{segment.end.imag}"
            elif isinstance(segment, CubicBezier):
                path_data += f" C {segment.control1.real},{segment.control1.imag} {segment.control2.real},{segment.control2.imag} {segment.end.real},{segment.end.imag}"
            elif isinstance(segment, QuadraticBezier):
                path_data += f" Q {segment.control.real},{segment.control.imag} {segment.end.real},{segment.end.imag}"
            elif isinstance(segment, Arc):
                path_data += f" A {segment.radius.real},{segment.radius.imag} {segment.rotation} {segment.large_arc} {segment.sweep} {segment.end.real},{segment.end.imag}"

        return path_data
    
    def _construct_continuous_path_data(self) -> str:
        """
        Construct a continuous path data string from all segments.
        
        Returns:
            Continuous path data string
        """
        if not self.joined_paths:
            return ""
        
        path = self.joined_paths[0]
        path_data = ""
        
        for i, segment in enumerate(path):
            if i == 0:
                # First segment - start with M command
                path_data += f"M {segment.start.real},{segment.start.imag}"
            
            # Add the segment based on its type
            if isinstance(segment, Line):
                path_data += f" L {segment.end.real},{segment.end.imag}"
            elif isinstance(segment, CubicBezier):
                path_data += f" C {segment.control1.real},{segment.control1.imag} {segment.control2.real},{segment.control2.imag} {segment.end.real},{segment.end.imag}"
            elif isinstance(segment, QuadraticBezier):
                path_data += f" Q {segment.control.real},{segment.control.imag} {segment.end.real},{segment.end.imag}"
            elif isinstance(segment, Arc):
                path_data += f" A {segment.radius.real},{segment.radius.imag} {segment.rotation} {segment.large_arc} {segment.sweep} {segment.end.real},{segment.end.imag}"
        
        return path_data
    
    def _remove_intermediate_m_commands(self, path_data: str) -> str:
        """
        Remove all M commands except the first one using regex.
        
        Args:
            path_data: Original path data string
            
        Returns:
            Path data string with intermediate M commands removed
        """
        # Replace " M [0-9.]*,[0-9.]*" with a space
        # This regex matches: space + M + space + number,number
        pattern = r' M [0-9.-]+,[0-9.-]+'
        cleaned_path = re.sub(pattern, ' ', path_data)
        
        return cleaned_path
    
    def get_connection_stats(self) -> dict:
        """
        Get statistics about path connections.
        
        Returns:
            Dictionary with connection statistics
        """
        if not self.paths:
            return {}
        
        original_count = len(self.paths)
        joined_count = len(self.joined_paths) if self.joined_paths else 0
        reduction = original_count - joined_count
        
        return {
            'original_paths': original_count,
            'joined_paths': joined_count,
            'paths_eliminated': reduction,
            'reduction_percentage': (reduction / original_count * 100) if original_count > 0 else 0
        }


def main():
    """Command-line interface for the SVG Path Joiner."""
    parser = argparse.ArgumentParser(description='Join SVG paths and remove M commands with regex')
    parser.add_argument('input_svg', help='Input SVG file path')
    parser.add_argument('output_svg', help='Output SVG file path')
    parser.add_argument('--tolerance', type=float, default=0.001, 
                       help='Maximum distance between endpoints to consider them matching (default: 0.001)')
    parser.add_argument('--stats', action='store_true',
                       help='Show connection statistics')
    
    args = parser.parse_args()
    
    try:
        # Create joiner and process
        joiner = SVGPathJoinerRemoveMRegex(tolerance=args.tolerance)
        joiner.load_svg(args.input_svg)
        joiner.join_paths()
        joiner.save_svg(args.output_svg)
        
        # Show statistics if requested
        if args.stats:
            stats = joiner.get_connection_stats()
            print(f"\nConnection Statistics:")
            print(f"  Original paths: {stats['original_paths']}")
            print(f"  Joined paths: {stats['joined_paths']}")
            print(f"  Paths eliminated: {stats['paths_eliminated']}")
            print(f"  Reduction: {stats['reduction_percentage']:.1f}%")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
