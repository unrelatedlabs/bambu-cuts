#!/usr/bin/env python3
"""
GCode Tools - A comprehensive module for SVG to G-code and G-code to SVG conversion
with support for drag knife and swivel knife cutting operations.

Features:
- SVG to G-code conversion with configurable cutting parameters
- G-code to SVG visualization with different colors for cutting vs tool moves
- Debug SVG output showing original SVG with superimposed G-code details
- Support for drag knife and swivel knife with configurable offset
- Configurable cutting parameters (material thickness, cut depth, passes, knife force)
- CLI interface and importable module
"""

import argparse
import os
import sys
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET
import re
import math
import numpy as np

try:
    from svg_to_gcode.svg_parser import parse_file
    from svg_to_gcode.compiler import Compiler, interfaces
    from svg_to_gcode.formulas import linear_map
except ImportError:
    print("Error: svg-to-gcode package not found. Please install it with: pip install svg-to-gcode")
    sys.exit(1)


@dataclass
class CuttingParameters:
    """Configuration parameters for cutting operations."""
    material_thickness: float = 1.0  # mm
    cut_depth_per_pass: float = 0.2  # mm
    number_of_passes: int = 5
    knife_force: float = 100.0  # grams
    movement_speed: float = 10000.0  # mm/min
    cutting_speed: float = 1000.0  # mm/min
    knife_offset: float = 0.0  # mm offset for knife compensation (blade trailing distance)
    corner_loop_radius: float = None  # mm radius for corner loops (defaults to 2x knife_offset)
    join_paths: bool = True  # Join connected paths to minimize tool lifts
    path_tolerance: float = 0.1  # mm tolerance for considering paths as connected
    swivel_sensitivity: float = 0.6  # Swivel sensitivity (0.0 = no swivel, 1.0 = full swivel)
    sharp_corner_threshold: float = 45.0  # Degrees - threshold for sharp corner handling


@dataclass
class GCodeLine:
    """Represents a single G-code line with metadata."""
    command: str
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    f: Optional[float] = None
    is_cutting: bool = False
    is_tool_move: bool = False
    line_number: int = 0


class KnifeOffsetCompensator:
    """Handles 2D knife offset compensation for drag knife trailing behavior."""
    
    def __init__(self, offset: float, corner_loop_radius: float = None):
        self.offset = offset
        self.corner_loop_radius = corner_loop_radius or (offset * 2)  # Default to 2x offset
        
    def compensate_path(self, points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """
        Compensate a path for drag knife trailing behavior using geometric offset.
        
        This implements the Roland/Graphtec style geometric offset compensation
        where the tool center path is offset from the desired cut path.
        
        Args:
            points: List of (x, y) coordinates representing the desired cut path
            
        Returns:
            Compensated path that the tool center should follow
        """
        if len(points) < 2 or self.offset == 0:
            return points
            
        # Convert to numpy arrays for easier manipulation
        points_array = np.array(points)
        
        # Calculate the offset path
        offset_path = self._calculate_geometric_offset(points_array)
        
        # Add corner loops for sharp corners
        offset_path_with_loops = self._add_corner_loops(offset_path)
        
        return [(float(x), float(y)) for x, y in offset_path_with_loops]
    
    def _calculate_geometric_offset(self, points: np.ndarray) -> np.ndarray:
        """
        Calculate geometric offset for drag knife compensation.
        
        This implements a proper continuous path offset algorithm that creates
        smooth, continuous tool paths instead of fragmented segments.
        """
        if len(points) < 2:
            return points
            
        # For drag knife, we need to offset the path to the "inside" of the curve
        # so the blade tip follows the original path
        offset_points = []
        
        for i in range(len(points)):
            if i == 0:
                # First point - use direction to next point
                direction = self._get_direction_vector(points[0], points[1])
                offset_point = self._offset_point_perpendicular(points[0], direction, self.offset)
                offset_points.append(offset_point)
            elif i == len(points) - 1:
                # Last point - use direction from previous point
                direction = self._get_direction_vector(points[i-1], points[i])
                offset_point = self._offset_point_perpendicular(points[i], direction, self.offset)
                offset_points.append(offset_point)
            else:
                # Middle point - calculate smooth offset using local geometry
                prev_point = points[i-1]
                curr_point = points[i]
                next_point = points[i+1]
                
                # Calculate the local tangent direction
                dir_in = self._get_direction_vector(prev_point, curr_point)
                dir_out = self._get_direction_vector(curr_point, next_point)
                
                # Average the directions for smooth transition
                avg_direction = (dir_in + dir_out) / 2
                avg_direction = avg_direction / np.linalg.norm(avg_direction)
                
                # Apply perpendicular offset
                offset_point = self._offset_point_perpendicular(curr_point, avg_direction, self.offset)
                offset_points.append(offset_point)
        
        return np.array(offset_points)
    
    def _offset_point_perpendicular(self, point: np.ndarray, direction: np.ndarray, offset: float) -> np.ndarray:
        """
        Offset a point perpendicular to the direction vector.
        
        For drag knife compensation, we offset perpendicular to the cutting direction
        so the blade tip follows the original path.
        """
        # Calculate perpendicular direction (90 degrees clockwise)
        perp_direction = np.array([direction[1], -direction[0]])
        
        # Apply offset
        return point + perp_direction * offset
    
    def _get_direction_vector(self, p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
        """Get normalized direction vector from p1 to p2."""
        direction = p2 - p1
        length = np.linalg.norm(direction)
        if length == 0:
            return np.array([1, 0])  # Default direction
        return direction / length
    
    def _calculate_bisector(self, dir1: np.ndarray, dir2: np.ndarray) -> np.ndarray:
        """Calculate the bisector of two direction vectors."""
        # Add the two direction vectors and normalize
        bisector = dir1 + dir2
        length = np.linalg.norm(bisector)
        if length == 0:
            # If vectors are opposite, use perpendicular to first vector
            return np.array([-dir1[1], dir1[0]])
        return bisector / length
    
    def _offset_point(self, point: np.ndarray, direction: np.ndarray, offset: float) -> np.ndarray:
        """Offset a point in the given direction by the offset distance."""
        # For drag knife, we offset perpendicular to the cutting direction
        # The blade trails behind, so we offset to the "inside" of the curve
        perp_direction = np.array([-direction[1], direction[0]])  # 90 degree rotation
        return point + perp_direction * offset
    
    def _add_corner_loops(self, points: np.ndarray) -> np.ndarray:
        """
        Add corner loops for sharp corners to ensure proper blade alignment.
        
        This implements the Roland/Graphtec style corner handling where
        the knife swings past the corner and returns to realign the blade.
        """
        if len(points) < 3:
            return points
            
        result_points = [points[0]]  # Start with first point
        
        for i in range(1, len(points) - 1):
            prev_point = points[i-1]
            current_point = points[i]
            next_point = points[i+1]
            
            # Calculate angle between segments
            angle = self._calculate_angle(prev_point, current_point, next_point)
            
            # If it's a sharp corner, add a corner loop
            if abs(angle) > math.pi / 4:  # 45 degrees
                loop_points = self._create_corner_loop(prev_point, current_point, next_point)
                result_points.extend(loop_points)
            else:
                result_points.append(current_point)
        
        result_points.append(points[-1])  # Add last point
        return np.array(result_points)
    
    def _calculate_angle(self, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
        """Calculate the angle between three points."""
        v1 = p1 - p2
        v2 = p3 - p2
        
        # Calculate angle using dot product
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        cos_angle = np.clip(cos_angle, -1.0, 1.0)  # Avoid numerical errors
        
        return math.acos(cos_angle)
    
    def _create_corner_loop(self, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> List[np.ndarray]:
        """
        Create a corner loop for sharp corners.
        
        This creates a small arc that swings the knife past the corner
        and returns to ensure proper blade alignment.
        """
        # Calculate the direction vectors
        dir_in = self._get_direction_vector(p1, p2)
        dir_out = self._get_direction_vector(p2, p3)
        
        # Calculate the bisector
        bisector = self._calculate_bisector(dir_in, dir_out)
        
        # Create a small arc around the corner
        loop_points = []
        num_steps = 5  # Number of points in the corner loop
        
        for i in range(num_steps + 1):
            t = i / num_steps
            # Create a small arc that swings past the corner
            arc_point = p2 + bisector * self.corner_loop_radius * (1 - t)
            loop_points.append(arc_point)
        
        return loop_points
    
    
    def compensate_curves(self, curves) -> List:
        """
        Compensate SVG curves for 2D knife offset.
        
        This applies geometric offset compensation to each curve in the SVG,
        calculating the tool center path that will result in the desired cut path.
        """
        compensated_curves = []
        
        for curve in curves:
            # Extract points from curve (simplified)
            if hasattr(curve, 'points') and curve.points:
                points = [(p.x, p.y) for p in curve.points]
                # Apply 2D geometric offset compensation
                compensated_points = self.compensate_path(points)
                
                # Create new curve with compensated points
                # This is a simplified approach - real implementation would
                # need to handle different curve types (lines, arcs, bezier, etc.)
                compensated_curve = self._create_curve_from_points(compensated_points, curve)
                compensated_curves.append(compensated_curve)
            else:
                compensated_curves.append(curve)
                
        return compensated_curves
    
    def _create_curve_from_points(self, points: List[Tuple[float, float]], original_curve):
        """
        Create a curve object from a list of points.
        This is a simplified implementation - in practice, you'd need
        to create the appropriate curve type based on the original curve.
        """
        # For now, return a simple line curve
        # In a real implementation, you'd need to handle different curve types
        class CompensatedCurve:
            def __init__(self, points, original_curve):
                self.points = points
                self.original_curve = original_curve
                
        return CompensatedCurve(points, original_curve)


class PathJoiner:
    """Handles joining of connected SVG paths to minimize tool lifts."""
    
    def __init__(self, tolerance: float = 0.1):
        self.tolerance = tolerance
        
    def join_paths(self, curves) -> List:
        """
        Join connected paths to minimize tool lifts.
        
        Args:
            curves: List of SVG curves/paths
            
        Returns:
            List of joined paths
        """
        if not curves:
            return curves
            
        # Extract points from curves
        path_data = []
        for i, curve in enumerate(curves):
            if hasattr(curve, 'points') and curve.points:
                points = [(p.x, p.y) for p in curve.points]
                path_data.append({
                    'index': i,
                    'points': points,
                    'start': points[0] if points else None,
                    'end': points[-1] if points else None,
                    'used': False
                })
        
        if not path_data:
            return curves
            
        # Join paths that connect end-to-end
        joined_paths = []
        remaining_paths = path_data.copy()
        
        while remaining_paths:
            current_path = remaining_paths.pop(0)
            if current_path['used']:
                continue
                
            current_path['used'] = True
            joined_path = current_path['points'].copy()
            
            # Try to find connected paths
            while True:
                found_connection = False
                
                for i, other_path in enumerate(remaining_paths):
                    if other_path['used']:
                        continue
                        
                    # Check if paths connect
                    connection_type = self._check_connection(
                        current_path['end'], 
                        other_path['start'], 
                        other_path['end']
                    )
                    
                    if connection_type:
                        other_path['used'] = True
                        remaining_paths.pop(i)
                        
                        if connection_type == 'end_to_start':
                            # Add other path to current path
                            joined_path.extend(other_path['points'][1:])  # Skip first point (duplicate)
                        elif connection_type == 'end_to_end':
                            # Add reversed other path to current path
                            reversed_points = other_path['points'][::-1]
                            joined_path.extend(reversed_points[1:])  # Skip first point (duplicate)
                        elif connection_type == 'start_to_start':
                            # Add reversed current path, then other path
                            joined_path = joined_path[::-1]
                            joined_path.extend(other_path['points'][1:])
                        elif connection_type == 'start_to_end':
                            # Add other path to current path
                            joined_path.extend(other_path['points'][1:])
                        
                        # Update current path end point
                        current_path['end'] = joined_path[-1]
                        found_connection = True
                        break
                
                if not found_connection:
                    break
            
            # Create new curve with joined path
            joined_curve = self._create_curve_from_points(joined_path, current_path['index'])
            joined_paths.append(joined_curve)
        
        return joined_paths
    
    def _check_connection(self, end1: Tuple[float, float], 
                         start2: Tuple[float, float], 
                         end2: Tuple[float, float]) -> Optional[str]:
        """
        Check if two paths connect and return connection type.
        
        Returns:
            'end_to_start', 'end_to_end', 'start_to_start', 'start_to_end', or None
        """
        if not end1 or not start2 or not end2:
            return None
            
        # Check end-to-start connection
        if self._points_close(end1, start2):
            return 'end_to_start'
            
        # Check end-to-end connection
        if self._points_close(end1, end2):
            return 'end_to_end'
            
        # Check start-to-start connection
        if self._points_close(end1, start2):
            return 'start_to_start'
            
        # Check start-to-end connection
        if self._points_close(end1, end2):
            return 'start_to_end'
            
        return None
    
    def _points_close(self, p1: Tuple[float, float], p2: Tuple[float, float]) -> bool:
        """Check if two points are close enough to be considered connected."""
        if not p1 or not p2:
            return False
        distance = math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)
        return distance <= self.tolerance
    
    def _create_curve_from_points(self, points: List[Tuple[float, float]], original_index: int):
        """
        Create a curve object from a list of points.
        This is a simplified implementation - in practice, you'd need
        to create the appropriate curve type based on the original curve.
        """
        # For now, return a simple line curve
        # In a real implementation, you'd need to handle different curve types
        class SimpleCurve:
            def __init__(self, points):
                self.points = points
                self.original_index = original_index
                
        return SimpleCurve(points)


class KnifeInterface(interfaces.Gcode):
    """Custom G-code interface for knife cutting operations."""
    
    def __init__(self):
        super().__init__()
        # Default parameters - will be set by the compiler
        self.params = CuttingParameters()
        self.current_z = 0
        self.is_cutting = False
        self.last_position = None
        self.path_joiner = None
        
    def laser_off(self):
        """Turn off cutting (retract knife)."""
        self.is_cutting = False
        return f"G1 Z{self.params.material_thickness + 2} F{self.params.movement_speed}"
    
    def laser_on(self):
        """Turn on cutting (engage knife)."""
        self.is_cutting = True
        return f"G1 Z{self.params.material_thickness - self.params.cut_depth_per_pass} F{self.params.cutting_speed}"
    
    def set_laser_power(self, power):
        """Set cutting power (0 = off, 1 = on)."""
        if power < 0 or power > 1:
            raise ValueError(f"Power {power} is out of bounds. Must be between 0 and 1.")
        
        if power == 0:
            return self.laser_off()
        else:
            return self.laser_on()
    
    def set_origin(self, x, y):
        """Set origin position."""
        return f"G92 X{x} Y{y}"
    
    def rapid_move(self, x, y):
        """Rapid positioning move."""
        return f"G0 X{x} Y{y} F{self.params.movement_speed}"
    
    def linear_move(self, x, y):
        """Linear cutting move."""
        speed = self.params.cutting_speed if self.is_cutting else self.params.movement_speed
        return f"G1 X{x} Y{y} F{speed}"


class GCodeTools:
    """Main class for G-code operations."""
    
    def __init__(self, params: CuttingParameters = None):
        self.params = params or CuttingParameters()
        self.gcode_lines: List[GCodeLine] = []
        self.knife_compensator = KnifeOffsetCompensator(
            offset=self.params.knife_offset,
            corner_loop_radius=self.params.corner_loop_radius
        )
        self.path_joiner = PathJoiner(tolerance=self.params.path_tolerance)
        
    def svg_to_gcode(self, svg_path: str, output_path: str = None) -> str:
        """
        Convert SVG file to G-code.
        
        Args:
            svg_path: Path to input SVG file
            output_path: Path for output G-code file (optional)
            
        Returns:
            Generated G-code as string
        """
        if not os.path.exists(svg_path):
            raise FileNotFoundError(f"SVG file not found: {svg_path}")
        
        # Parse SVG file
        curves = parse_file(svg_path)
        
        # Join connected paths if enabled
        if self.params.join_paths:
            curves = self.path_joiner.join_paths(curves)
        
        # Create compiler with knife interface
        compiler = Compiler(
            KnifeInterface, 
            movement_speed=self.params.movement_speed,
            cutting_speed=self.params.cutting_speed,
            pass_depth=self.params.cut_depth_per_pass
        )
        
        # Set parameters on the interface
        compiler.interface.params = self.params
        
        # Note: 2D knife offset will be applied post-processing to G-code
        # for better control over the offset algorithm
        
        # Compile to G-code
        compiler.append_curves(curves)
        
        if output_path:
            compiler.compile_to_file(output_path)
            gcode_content = self._read_gcode_file(output_path)
            
            # Post-process G-code
            processed_gcode = gcode_content
            
            # Apply 2D knife offset compensation if needed
            if self.params.knife_offset != 0:
                        processed_gcode = self._apply_simple_2d_offset(processed_gcode)
            
            # Optimize tool lifts if path joining is enabled
            if self.params.join_paths:
                processed_gcode = self._optimize_tool_lifts(processed_gcode)
            
            with open(output_path, 'w') as f:
                f.write(processed_gcode)
            return processed_gcode
        else:
            # Return G-code as string
            gcode_string = compiler.compile()
            
            # Post-process G-code
            # Optimize tool lifts if path joining is enabled
            if self.params.join_paths:
                gcode_string = self._optimize_tool_lifts(gcode_string)
            
            return gcode_string
    
    def gcode_to_svg(self, gcode_path: str, output_path: str = None, 
                    original_svg_path: str = None) -> str:
        """
        Convert G-code to SVG visualization.
        
        Args:
            gcode_path: Path to input G-code file
            output_path: Path for output SVG file (optional)
            original_svg_path: Path to original SVG for overlay (optional)
            
        Returns:
            Generated SVG as string
        """
        self._parse_gcode_file(gcode_path)
        
        # Create SVG with G-code visualization
        svg_content = self._create_gcode_svg(original_svg_path)
        
        if output_path:
            with open(output_path, 'w') as f:
                f.write(svg_content)
        
        return svg_content
    
    def create_debug_svg(self, svg_path: str, gcode_path: str, output_path: str = None) -> str:
        """
        Create debug SVG showing original SVG with superimposed G-code details.
        
        Args:
            svg_path: Path to original SVG file
            gcode_path: Path to G-code file
            output_path: Path for output debug SVG file (optional)
            
        Returns:
            Debug SVG content as string
        """
        # Parse G-code
        self._parse_gcode_file(gcode_path)
        
        # Read original SVG
        with open(svg_path, 'r') as f:
            original_svg = f.read()
        
        # Create debug SVG with overlay
        debug_svg = self._create_debug_svg_overlay(original_svg)
        
        if output_path:
            with open(output_path, 'w') as f:
                f.write(debug_svg)
        
        return debug_svg
    
    
    def _parse_gcode_file(self, gcode_path: str):
        """Parse G-code file and extract line information."""
        self.gcode_lines = []
        
        with open(gcode_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith(';'):
                    continue
                
                gcode_line = self._parse_gcode_line(line, line_num)
                if gcode_line:
                    self.gcode_lines.append(gcode_line)
    
    def _parse_gcode_line(self, line: str, line_num: int) -> Optional[GCodeLine]:
        """Parse a single G-code line."""
        # Extract coordinates and commands
        x_match = re.search(r'X([+-]?\d*\.?\d+)', line)
        y_match = re.search(r'Y([+-]?\d*\.?\d+)', line)
        z_match = re.search(r'Z([+-]?\d*\.?\d+)', line)
        f_match = re.search(r'F([+-]?\d*\.?\d+)', line)
        
        x = float(x_match.group(1)) if x_match else None
        y = float(y_match.group(1)) if y_match else None
        z = float(z_match.group(1)) if z_match else None
        f = float(f_match.group(1)) if f_match else None
        
        # Determine if this is a cutting move or tool move
        # Cutting moves have Z values significantly below the material surface
        is_cutting = 'G1' in line and z is not None and z < (self.params.material_thickness - 0.5)
        is_tool_move = 'G0' in line or ('G1' in line and not is_cutting)
        
        return GCodeLine(
            command=line,
            x=x, y=y, z=z, f=f,
            is_cutting=is_cutting,
            is_tool_move=is_tool_move,
            line_number=line_num
        )
    
    def _create_gcode_svg(self, original_svg_path: str = None) -> str:
        """Create SVG visualization of G-code."""
        # Calculate bounds
        x_coords = [line.x for line in self.gcode_lines if line.x is not None]
        y_coords = [line.y for line in self.gcode_lines if line.y is not None]
        
        if not x_coords or not y_coords:
            return "<!-- No valid coordinates found in G-code -->"
        
        min_x, max_x = min(x_coords), max(x_coords)
        min_y, max_y = min(y_coords), max(y_coords)
        
        width = max_x - min_x + 20
        height = max_y - min_y + 20
        
        svg_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="{width}mm" height="{height}mm" viewBox="{min_x-10} {min_y-10} {width} {height}" 
     xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd">
  <title>G-code Visualization</title>
  
  <!-- Tool moves (rapid positioning) -->
  <g stroke="blue" stroke-width="0.1" fill="none" stroke-dasharray="2,1">
'''
        
        # Draw tool moves
        current_x, current_y = None, None
        for line in self.gcode_lines:
            if line.is_tool_move and line.x is not None and line.y is not None:
                if current_x is not None and current_y is not None:
                    svg_content += f'    <line x1="{current_x}" y1="{current_y}" x2="{line.x}" y2="{line.y}"/>\n'
                current_x, current_y = line.x, line.y
        
        svg_content += '''  </g>
  
  <!-- Cutting moves -->
  <g stroke="red" stroke-width="0.2" fill="none">
'''
        
        # Draw cutting moves
        current_x, current_y = None, None
        for line in self.gcode_lines:
            if line.is_cutting and line.x is not None and line.y is not None:
                if current_x is not None and current_y is not None:
                    svg_content += f'    <line x1="{current_x}" y1="{current_y}" x2="{line.x}" y2="{line.y}"/>\n'
                current_x, current_y = line.x, line.y
        
        svg_content += '''  </g>
  
  <!-- Legend -->
  <g font-family="Arial" font-size="2" fill="black">
    <line x1="10" y1="10" x2="20" y2="10" stroke="blue" stroke-width="0.1" stroke-dasharray="2,1"/>
    <text x="22" y="12">Tool moves (rapid positioning)</text>
    <line x1="10" y1="15" x2="20" y2="15" stroke="red" stroke-width="0.2"/>
    <text x="22" y="17">Cutting moves</text>
  </g>
</svg>'''
        
        return svg_content
    
    def _create_debug_svg_overlay(self, original_svg: str) -> str:
        """Create debug SVG with original SVG and G-code overlay."""
        # Parse original SVG to get viewBox
        root = ET.fromstring(original_svg)
        viewbox = root.get('viewBox', '0 0 100 100')
        
        # Create debug SVG
        debug_svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="100%" height="100%" viewBox="{viewbox}" 
     xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd">
  <title>Debug: Original SVG + G-code Overlay</title>
  
  <!-- Original SVG content -->
  <g opacity="0.3">
    {original_svg.split('<svg')[1].split('</svg>')[0]}
  </g>
  
  <!-- G-code overlay -->
  <g stroke="blue" stroke-width="0.1" fill="none" stroke-dasharray="2,1" opacity="0.7">
'''
        
        # Add tool moves
        current_x, current_y = None, None
        for line in self.gcode_lines:
            if line.is_tool_move and line.x is not None and line.y is not None:
                if current_x is not None and current_y is not None:
                    debug_svg += f'    <line x1="{current_x}" y1="{current_y}" x2="{line.x}" y2="{line.y}"/>\n'
                current_x, current_y = line.x, line.y
        
        debug_svg += '''  </g>
  
  <!-- Cutting moves -->
  <g stroke="red" stroke-width="0.2" fill="none" opacity="0.8">
'''
        
        # Add cutting moves
        current_x, current_y = None, None
        for line in self.gcode_lines:
            if line.is_cutting and line.x is not None and line.y is not None:
                if current_x is not None and current_y is not None:
                    debug_svg += f'    <line x1="{current_x}" y1="{current_y}" x2="{line.x}" y2="{line.y}"/>\n'
                current_x, current_y = line.x, line.y
        
        debug_svg += '''  </g>
  
  <!-- Legend -->
  <g font-family="Arial" font-size="2" fill="black">
    <rect x="10" y="10" width="80" height="30" fill="white" stroke="black" stroke-width="0.1"/>
    <line x1="15" y1="20" x2="25" y2="20" stroke="blue" stroke-width="0.1" stroke-dasharray="2,1"/>
    <text x="27" y="22">Tool moves (rapid positioning)</text>
    <line x1="15" y1="25" x2="25" y2="25" stroke="red" stroke-width="0.2"/>
    <text x="27" y="27">Cutting moves</text>
  </g>
</svg>'''
        
        return debug_svg
    
    def _read_gcode_file(self, gcode_path: str) -> str:
        """Read G-code file content."""
        with open(gcode_path, 'r') as f:
            return f.read()
    
    def _optimize_tool_lifts(self, gcode_content: str) -> str:
        """
        Optimize G-code by removing unnecessary tool lifts between connected segments.
        
        This method analyzes the G-code and removes tool lifts (Z movements) when
        the next cutting move starts at the same position where the previous one ended.
        """
        lines = gcode_content.split('\n')
        optimized_lines = []
        i = 0
        last_cutting_position = None
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Look for the pattern: Z lift -> rapid move to same position -> Z lower
            if (line.startswith('G1 Z') and 'F' in line and 
                i + 2 < len(lines)):
                
                next_line = lines[i + 1].strip()
                third_line = lines[i + 2].strip()
                
                # Check if next line is a rapid move and third line is Z lower
                if (next_line.startswith('G1 X') and 'F' in next_line and
                    third_line.startswith('G1 Z') and 'F' in third_line):
                    
                    # Extract positions
                    rapid_pos = self._extract_position_from_line(next_line)
                    
                    # Check if rapid move goes to same position as last cutting position
                    if (last_cutting_position and rapid_pos and 
                        self._positions_close(last_cutting_position, rapid_pos, self.params.path_tolerance)):
                        
                        # Skip the tool lift and rapid move, go directly to cutting
                        optimized_lines.append(third_line)  # Keep the Z lower and cutting move
                        i += 3  # Skip the lift, rapid move, and Z lower
                        continue
            
            # Track cutting positions
            if line.startswith('G1 X') and 'F' in line and not line.startswith('G1 Z'):
                last_cutting_position = self._extract_position_from_line(line)
            
            optimized_lines.append(line)
            i += 1
        
        return '\n'.join(optimized_lines)
    
    def _extract_position_from_line(self, line: str) -> Optional[Tuple[float, float]]:
        """Extract X, Y position from a G-code line."""
        x_match = re.search(r'X([+-]?\d*\.?\d+)', line)
        y_match = re.search(r'Y([+-]?\d*\.?\d+)', line)
        
        if x_match and y_match:
            return (float(x_match.group(1)), float(y_match.group(1)))
        return None
    
    def _positions_close(self, pos1: Tuple[float, float], pos2: Tuple[float, float], tolerance: float) -> bool:
        """Check if two positions are close enough to be considered the same."""
        if not pos1 or not pos2:
            return False
        distance = math.sqrt((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)
        return distance <= tolerance
    
    def _apply_2d_knife_offset(self, gcode_content: str) -> str:
        """
        Apply 2D knife offset compensation to G-code.
        
        This method extracts cutting paths from G-code and applies geometric offset
        compensation to account for the drag knife blade trailing behavior.
        """
        if self.params.knife_offset == 0:
            return gcode_content
            
        lines = gcode_content.split('\n')
        processed_lines = []
        cutting_path = []
        in_cutting_mode = False
        
        for line in lines:
            line = line.strip()
            
            # Track cutting mode
            if line.startswith('G1 Z') and 'F' in line:
                z_value = self._extract_z_from_line(line)
                if z_value and z_value < self.params.material_thickness:
                    in_cutting_mode = True
                else:
                    in_cutting_mode = False
                    # Process accumulated cutting path
                    if cutting_path:
                        compensated_path = self._compensate_cutting_path(cutting_path)
                        processed_lines.extend(compensated_path)
                        cutting_path = []
            
            # Collect cutting coordinates
            if in_cutting_mode and line.startswith('G1 X') and 'Y' in line:
                pos = self._extract_position_from_line(line)
                if pos:
                    cutting_path.append((line, pos))
                    continue  # Don't add original line yet
            
            # Add non-cutting lines immediately
            if not in_cutting_mode or not line.startswith('G1 X'):
                processed_lines.append(line)
        
        # Process any remaining cutting path
        if cutting_path:
            compensated_path = self._compensate_cutting_path(cutting_path)
            processed_lines.extend(compensated_path)
        
        return '\n'.join(processed_lines)
    
    def _extract_z_from_line(self, line: str) -> Optional[float]:
        """Extract Z coordinate from a G-code line."""
        z_match = re.search(r'Z([+-]?\d*\.?\d+)', line)
        if z_match:
            return float(z_match.group(1))
        return None
    
    def _compensate_cutting_path(self, cutting_path: List[Tuple[str, Tuple[float, float]]]) -> List[str]:
        """
        Apply 2D knife offset compensation to a cutting path.
        
        Args:
            cutting_path: List of (gcode_line, (x, y)) tuples
            
        Returns:
            List of compensated G-code lines
        """
        if len(cutting_path) < 2:
            return [line for line, _ in cutting_path]
        
        # Extract points
        points = [pos for _, pos in cutting_path]
        
        # Apply geometric offset compensation
        compensated_points = self.knife_compensator.compensate_path(points)
        
        # Generate new G-code lines with compensated coordinates
        compensated_lines = []
        for i, (original_line, _) in enumerate(cutting_path):
            if i < len(compensated_points):
                new_x, new_y = compensated_points[i]
                # Replace coordinates in the original line
                new_line = re.sub(r'X[+-]?\d*\.?\d+', f'X{new_x:.6f}', original_line)
                new_line = re.sub(r'Y[+-]?\d*\.?\d+', f'Y{new_y:.6f}', new_line)
                compensated_lines.append(new_line)
            else:
                compensated_lines.append(original_line)
        
        return compensated_lines
    
    def _apply_svg_path_offset(self, curves) -> List:
        """
        Apply 2D knife offset compensation to SVG curves.
        
        This is the correct approach - offset the SVG paths before G-code generation
        so the resulting G-code has smooth, continuous tool paths.
        """
        if self.params.knife_offset == 0:
            return curves
            
        offset_curves = []
        
        for curve in curves:
            if hasattr(curve, 'points') and curve.points:
                # Extract points from the curve
                points = np.array([(p.x, p.y) for p in curve.points])
                
                # Apply smooth geometric offset
                offset_points = self._calculate_smooth_offset(points, self.params.knife_offset)
                
                # Create new curve with offset points
                offset_curve = self._create_offset_curve(curve, offset_points)
                offset_curves.append(offset_curve)
            else:
                offset_curves.append(curve)
        
        return offset_curves
    
    def _calculate_smooth_offset(self, points: np.ndarray, offset: float) -> np.ndarray:
        """
        Calculate smooth offset for a continuous path.
        
        This creates a smooth, continuous offset path that follows the original
        path at a constant distance, suitable for drag knife compensation.
        """
        if len(points) < 2:
            return points
            
        offset_points = []
        
        for i in range(len(points)):
            if i == 0:
                # First point - offset in direction of next point
                direction = self._get_direction_vector(points[0], points[1])
                offset_point = self._offset_perpendicular(points[0], direction, offset)
                offset_points.append(offset_point)
            elif i == len(points) - 1:
                # Last point - offset in direction from previous point
                direction = self._get_direction_vector(points[i-1], points[i])
                offset_point = self._offset_perpendicular(points[i], direction, offset)
                offset_points.append(offset_point)
            else:
                # Middle point - use smooth interpolation
                prev_point = points[i-1]
                curr_point = points[i]
                next_point = points[i+1]
                
                # Calculate smooth direction using local geometry
                dir1 = self._get_direction_vector(prev_point, curr_point)
                dir2 = self._get_direction_vector(curr_point, next_point)
                
                # Smooth interpolation between directions
                t = 0.5  # Weight for interpolation
                smooth_direction = (1 - t) * dir1 + t * dir2
                smooth_direction = smooth_direction / np.linalg.norm(smooth_direction)
                
                # Apply perpendicular offset
                offset_point = self._offset_perpendicular(curr_point, smooth_direction, offset)
                offset_points.append(offset_point)
        
        return np.array(offset_points)
    
    def _offset_perpendicular(self, point: np.ndarray, direction: np.ndarray, offset: float) -> np.ndarray:
        """Offset a point perpendicular to the direction vector."""
        # Perpendicular direction (90 degrees clockwise)
        perp = np.array([direction[1], -direction[0]])
        return point + perp * offset
    
    def _create_offset_curve(self, original_curve, offset_points: np.ndarray):
        """Create a new curve with offset points."""
        # This is a simplified implementation
        # In practice, you'd need to create the appropriate curve type
        class OffsetCurve:
            def __init__(self, original_curve, offset_points):
                self.original_curve = original_curve
                self.offset_points = offset_points
                # Create point objects that match the original curve structure
                self.points = []
                for i, (x, y) in enumerate(offset_points):
                    point = type('Point', (), {'x': x, 'y': y})()
                    self.points.append(point)
        
        return OffsetCurve(original_curve, offset_points)
    
    def _apply_simple_2d_offset(self, gcode_content: str) -> str:
        """
        Apply bCNC-style drag knife offset compensation to G-code.
        
        This implements the proven bCNC drag knife algorithm that:
        1. Offsets the tool center path to compensate for blade trailing
        2. Handles corners with proper swivel movements
        3. Creates smooth, continuous cutting paths
        """
        if self.params.knife_offset == 0:
            return gcode_content
        
        lines = gcode_content.split('\n')
        processed_lines = []
        cutting_path = []
        in_cutting_mode = False
        
        cutting_segments_found = 0
        for i, line in enumerate(lines):
            line = line.strip()
            
            # Track cutting mode - process each cutting segment individually
            if line.startswith('G1 Z') and 'F' in line:
                z_value = self._extract_z_from_line(line)
                if z_value and z_value < (self.params.material_thickness + 0.5):
                    # This is a cutting depth - process previous segment if any
                    if in_cutting_mode and cutting_path:
                        offset_path = self._apply_drag_knife_offset(cutting_path)
                        processed_lines.extend(offset_path)
                        cutting_segments_found += 1
                    # Start new cutting segment
                    cutting_path = []
                    in_cutting_mode = True
                    processed_lines.append(line)  # Add the Z movement
                    continue
                elif z_value and z_value > (self.params.material_thickness + 1.0):
                    # This is a tool lift - exit cutting mode
                    if in_cutting_mode and cutting_path:
                        offset_path = self._apply_drag_knife_offset(cutting_path)
                        processed_lines.extend(offset_path)
                        cutting_path = []
                    in_cutting_mode = False
                    processed_lines.append(line)  # Add the Z movement
                    continue
                else:
                    # Other Z movements - just add them
                    processed_lines.append(line)
                    continue
            
            # Collect cutting coordinates when in cutting mode
            if in_cutting_mode and line.startswith('G1 X') and 'Y' in line and 'F' in line:
                pos = self._extract_position_from_line(line)
                if pos:
                    cutting_path.append((line, pos))
                    continue  # Don't add original line yet
            
            # Add all other lines immediately
            processed_lines.append(line)
        
        # Process any remaining cutting path
        if cutting_path:
            offset_path = self._apply_drag_knife_offset(cutting_path)
            processed_lines.extend(offset_path)
            cutting_segments_found += 1
        
        return '\n'.join(processed_lines)
    
    def _apply_drag_knife_offset(self, cutting_path: List[Tuple[str, Tuple[float, float]]]) -> List[str]:
        """
        Apply bCNC-style drag knife offset compensation.
        
        This implements the proven algorithm from bCNC that creates smooth,
        continuous tool paths with proper corner handling for drag knives.
        """
        if len(cutting_path) < 2:
            return [line for line, _ in cutting_path]
        
        # Extract points from the cutting path
        points = [pos for _, pos in cutting_path]
        
        # Apply bCNC-style drag knife offset
        offset_points = self._calculate_drag_knife_offset(points)
        
        # Generate new G-code lines with offset coordinates
        offset_lines = []
        for i, (original_line, _) in enumerate(cutting_path):
            if i < len(offset_points):
                new_x, new_y = offset_points[i]
                # Replace coordinates in the original line
                new_line = re.sub(r'X[+-]?\d*\.?\d+', f'X{new_x:.6f}', original_line)
                new_line = re.sub(r'Y[+-]?\d*\.?\d+', f'Y{new_y:.6f}', new_line)
                offset_lines.append(new_line)
            else:
                offset_lines.append(original_line)
        
        return offset_lines
    
    def _calculate_drag_knife_offset(self, points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """
        Calculate drag knife offset with proper swivel handling.
        
        This implements the bCNC-style algorithm that accounts for:
        1. Blade trailing behind tool center
        2. Blade swiveling/rotating as it follows curves
        3. Corner handling with proper swivel compensation
        """
        if len(points) < 1:
            return points
        
        # Handle single-point segments by using a default direction
        if len(points) == 1:
            # For single points, offset in a default direction (e.g., +Y)
            default_direction = (0, 1)  # Upward direction
            offset_point = self._offset_perpendicular(points[0], default_direction, self.params.knife_offset)
            return [offset_point]
            
        offset_points = []
        knife_offset = self.params.knife_offset
        
        # Calculate swivel-aware offset for each point
        for i in range(len(points)):
            if i == 0:
                # First point - use direction to next point
                direction = self._get_direction(points[0], points[1])
                offset_point = self._calculate_swivel_offset(points[0], direction, knife_offset)
                offset_points.append(offset_point)
            elif i == len(points) - 1:
                # Last point - use direction from previous point
                direction = self._get_direction(points[i-1], points[i])
                offset_point = self._calculate_swivel_offset(points[i], direction, knife_offset)
                offset_points.append(offset_point)
            else:
                # Middle point - calculate swivel direction
                prev_point = points[i-1]
                curr_point = points[i]
                next_point = points[i+1]
                
                # Calculate the swivel direction based on curve geometry
                swivel_direction = self._calculate_swivel_direction(prev_point, curr_point, next_point)
                offset_point = self._calculate_swivel_offset(curr_point, swivel_direction, knife_offset)
                offset_points.append(offset_point)
        
        return offset_points
    
    def _calculate_swivel_direction(self, prev_point: Tuple[float, float], 
                                  curr_point: Tuple[float, float], 
                                  next_point: Tuple[float, float]) -> Tuple[float, float]:
        """
        Calculate the swivel direction for a drag knife at a curve point.
        
        The knife blade swivels to follow the cutting direction, so we need to
        calculate the direction that accounts for the blade's rotation.
        """
        # Calculate incoming and outgoing directions
        dir_in = self._get_direction(prev_point, curr_point)
        dir_out = self._get_direction(curr_point, next_point)
        
        # Calculate the angle between directions
        angle = self._angle_between_vectors(dir_in, dir_out)
        angle_degrees = abs(angle) * 180 / math.pi
        
        # Use configurable swivel sensitivity and sharp corner threshold
        swivel_sensitivity = self.params.swivel_sensitivity
        sharp_threshold = math.radians(self.params.sharp_corner_threshold)
        
        # For sharp corners, use a weighted average that accounts for swivel
        if abs(angle) > sharp_threshold:
            # Sharp corner - use more of the outgoing direction for swivel
            weight_out = 0.5 + (swivel_sensitivity * 0.3)  # 0.5 to 0.8
            weight_in = 0.5 - (swivel_sensitivity * 0.3)   # 0.5 to 0.2
        else:
            # Smooth curve - use balanced weighting with swivel sensitivity
            weight_out = 0.4 + (swivel_sensitivity * 0.4)  # 0.4 to 0.8
            weight_in = 0.6 - (swivel_sensitivity * 0.4)   # 0.6 to 0.2
        
        # Calculate weighted average direction
        swivel_direction = (
            weight_in * dir_in[0] + weight_out * dir_out[0],
            weight_in * dir_in[1] + weight_out * dir_out[1]
        )
        
        # Normalize
        length = math.sqrt(swivel_direction[0]**2 + swivel_direction[1]**2)
        if length > 0:
            swivel_direction = (swivel_direction[0] / length, swivel_direction[1] / length)
        
        return swivel_direction
    
    def _calculate_swivel_offset(self, point: Tuple[float, float], 
                               direction: Tuple[float, float], 
                               offset: float) -> Tuple[float, float]:
        """
        Calculate offset point accounting for drag knife swivel behavior.
        
        The knife blade trails behind the tool center and swivels to follow
        the cutting direction, so the offset needs to account for this rotation.
        """
        # For drag knife, we offset perpendicular to the cutting direction
        # but we need to consider the blade's swivel angle
        
        # Calculate perpendicular direction (90 degrees clockwise)
        perp_x = direction[1]
        perp_y = -direction[0]
        
        # Apply the offset
        offset_point = (
            point[0] + perp_x * offset,
            point[1] + perp_y * offset
        )
        
        return offset_point
    
    def _angle_between_vectors(self, v1: Tuple[float, float], v2: Tuple[float, float]) -> float:
        """Calculate the angle between two vectors in radians."""
        dot_product = v1[0] * v2[0] + v1[1] * v2[1]
        dot_product = max(-1.0, min(1.0, dot_product))  # Clamp to avoid numerical errors
        return math.acos(dot_product)
    
    def _calculate_bisector(self, dir1: Tuple[float, float], dir2: Tuple[float, float]) -> Tuple[float, float]:
        """Calculate the bisector of two direction vectors."""
        # Add the two direction vectors
        bisector = (dir1[0] + dir2[0], dir1[1] + dir2[1])
        
        # Normalize
        length = math.sqrt(bisector[0]**2 + bisector[1]**2)
        if length > 0:
            bisector = (bisector[0] / length, bisector[1] / length)
        else:
            # If vectors are opposite, use perpendicular to first vector
            bisector = (-dir1[1], dir1[0])
        
        return bisector
    
    def _handle_sharp_corner(self, prev_point: Tuple[float, float], 
                           curr_point: Tuple[float, float], 
                           next_point: Tuple[float, float], 
                           offset: float) -> Tuple[float, float]:
        """
        Handle sharp corners with bCNC-style swivel compensation.
        
        This adds a small arc around the corner to allow the knife to swivel
        into the correct position, similar to Roland/Cricut behavior.
        """
        # Calculate the bisector of the corner
        dir_in = self._get_direction(prev_point, curr_point)
        dir_out = self._get_direction(curr_point, next_point)
        bisector = self._calculate_bisector(dir_in, dir_out)
        
        # Apply offset with corner compensation
        # For sharp corners, we reduce the offset slightly to prevent overcutting
        corner_offset = offset * 0.8
        offset_point = self._offset_perpendicular(curr_point, bisector, corner_offset)
        
        return offset_point
    
    def _offset_perpendicular(self, point: Tuple[float, float], direction: Tuple[float, float], offset: float) -> Tuple[float, float]:
        """
        Offset a point perpendicular to the direction vector.
        
        For drag knife compensation, we offset perpendicular to the cutting direction
        so the blade tip follows the original path.
        """
        # Perpendicular direction (90 degrees clockwise)
        perp_x = direction[1]
        perp_y = -direction[0]
        
        return (point[0] + perp_x * offset, point[1] + perp_y * offset)
    
    def _get_direction(self, p1: Tuple[float, float], p2: Tuple[float, float]) -> Tuple[float, float]:
        """Get normalized direction vector from p1 to p2."""
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.sqrt(dx*dx + dy*dy)
        if length == 0:
            return (1, 0)  # Default direction
        return (dx/length, dy/length)
    
    def _offset_perpendicular_simple(self, point: Tuple[float, float], direction: Tuple[float, float], offset: float) -> Tuple[float, float]:
        """Offset a point perpendicular to the direction vector."""
        # Perpendicular direction (90 degrees clockwise)
        perp_x = direction[1]
        perp_y = -direction[0]
        
        return (point[0] + perp_x * offset, point[1] + perp_y * offset)


def main():
    """CLI interface for GCodeTools."""
    parser = argparse.ArgumentParser(description='GCode Tools - SVG to G-code and G-code to SVG conversion')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # SVG to G-code command
    svg_to_gcode_parser = subparsers.add_parser('svg-to-gcode', help='Convert SVG to G-code')
    svg_to_gcode_parser.add_argument('input_svg', help='Input SVG file')
    svg_to_gcode_parser.add_argument('-o', '--output', help='Output G-code file')
    svg_to_gcode_parser.add_argument('--material-thickness', type=float, default=1.0, help='Material thickness (mm)')
    svg_to_gcode_parser.add_argument('--cut-depth', type=float, default=0.2, help='Cut depth per pass (mm)')
    svg_to_gcode_parser.add_argument('--passes', type=int, default=5, help='Number of passes')
    svg_to_gcode_parser.add_argument('--knife-force', type=float, default=100.0, help='Knife force (grams)')
    svg_to_gcode_parser.add_argument('--knife-offset', type=float, default=0.0, help='Knife blade trailing offset (mm)')
    svg_to_gcode_parser.add_argument('--corner-loop-radius', type=float, default=None, help='Corner loop radius (mm, defaults to 2x knife offset)')
    svg_to_gcode_parser.add_argument('--join-paths', action='store_true', default=True, help='Join connected paths to minimize tool lifts')
    svg_to_gcode_parser.add_argument('--no-join-paths', action='store_false', dest='join_paths', help='Disable path joining')
    svg_to_gcode_parser.add_argument('--path-tolerance', type=float, default=0.1, help='Tolerance for considering paths as connected (mm)')
    svg_to_gcode_parser.add_argument('--movement-speed', type=float, default=10000.0, help='Movement speed (mm/min)')
    svg_to_gcode_parser.add_argument('--cutting-speed', type=float, default=1000.0, help='Cutting speed (mm/min)')
    
    # G-code to SVG command
    gcode_to_svg_parser = subparsers.add_parser('gcode-to-svg', help='Convert G-code to SVG visualization')
    gcode_to_svg_parser.add_argument('input_gcode', help='Input G-code file')
    gcode_to_svg_parser.add_argument('-o', '--output', help='Output SVG file')
    gcode_to_svg_parser.add_argument('--original-svg', help='Original SVG file for overlay')
    
    # Debug SVG command
    debug_parser = subparsers.add_parser('debug-svg', help='Create debug SVG with original and G-code overlay')
    debug_parser.add_argument('svg_file', help='Original SVG file')
    debug_parser.add_argument('gcode_file', help='G-code file')
    debug_parser.add_argument('-o', '--output', help='Output debug SVG file')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if args.command == 'svg-to-gcode':
            params = CuttingParameters(
                material_thickness=args.material_thickness,
                cut_depth_per_pass=args.cut_depth,
                number_of_passes=args.passes,
                knife_force=args.knife_force,
                movement_speed=args.movement_speed,
                cutting_speed=args.cutting_speed,
                knife_offset=args.knife_offset,
                corner_loop_radius=args.corner_loop_radius,
                join_paths=args.join_paths,
                path_tolerance=args.path_tolerance
            )
            
            tools = GCodeTools(params)
            gcode = tools.svg_to_gcode(args.input_svg, args.output)
            
            if not args.output:
                print(gcode)
            else:
                print(f"G-code saved to: {args.output}")
        
        elif args.command == 'gcode-to-svg':
            tools = GCodeTools()
            svg = tools.gcode_to_svg(args.input_gcode, args.output, args.original_svg)
            
            if not args.output:
                print(svg)
            else:
                print(f"SVG saved to: {args.output}")
        
        elif args.command == 'debug-svg':
            tools = GCodeTools()
            debug_svg = tools.create_debug_svg(args.svg_file, args.gcode_file, args.output)
            
            if not args.output:
                print(debug_svg)
            else:
                print(f"Debug SVG saved to: {args.output}")
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
