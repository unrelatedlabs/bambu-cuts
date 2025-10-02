#!/usr/bin/env python3
"""
Example usage of gcodetools.py module
"""

from gcodetools import GCodeTools, CuttingParameters

def main():
    # Example 1: Basic SVG to G-code conversion
    print("=== Example 1: SVG to G-code conversion ===")
    
    # Create cutting parameters
    params = CuttingParameters(
        material_thickness=2.0,  # 2mm material
        cut_depth_per_pass=0.3,  # 0.3mm per pass
        number_of_passes=7,      # 7 passes total
        knife_force=150.0,       # 150g knife force
        knife_offset=0.5,        # 0.5mm knife trailing offset
        knife_angle=0.0,         # knife angle compensation
        join_paths=True,         # Join connected paths to minimize tool lifts
        path_tolerance=0.1       # 0.1mm tolerance for path connection
    )
    
    # Create GCodeTools instance
    tools = GCodeTools(params)
    
    # Convert SVG to G-code
    try:
        gcode = tools.svg_to_gcode("badge.svg", "badge_output.gcode")
        print("✓ SVG to G-code conversion completed")
        print(f"  Generated {len(gcode.splitlines())} lines of G-code")
    except FileNotFoundError:
        print("✗ badge.svg not found, skipping this example")
    
    # Example 2: G-code to SVG visualization
    print("\n=== Example 2: G-code to SVG visualization ===")
    
    try:
        svg_viz = tools.gcode_to_svg("badge_output.gcode", "badge_visualization.svg")
        print("✓ G-code to SVG visualization completed")
    except FileNotFoundError:
        print("✗ badge_output.gcode not found, skipping this example")
    
    # Example 3: Debug SVG with overlay
    print("\n=== Example 3: Debug SVG with overlay ===")
    
    try:
        debug_svg = tools.create_debug_svg("badge.svg", "badge_output.gcode", "badge_debug.svg")
        print("✓ Debug SVG with overlay completed")
    except FileNotFoundError:
        print("✗ Required files not found, skipping this example")
    
    # Example 4: Different knife offset configurations
    print("\n=== Example 4: Different knife offset configurations ===")
    
    # Configuration for fine detail work
    fine_params = CuttingParameters(
        material_thickness=1.5,
        cut_depth_per_pass=0.2,
        number_of_passes=8,
        knife_force=120.0,
        knife_offset=0.2,  # Smaller offset for fine detail
        knife_angle=0.0
    )
    
    # Configuration for thick material
    thick_params = CuttingParameters(
        material_thickness=3.0,
        cut_depth_per_pass=0.5,
        number_of_passes=6,
        knife_force=200.0,
        knife_offset=1.0,  # Larger offset for thick material
        knife_angle=5.0    # Angle compensation for thick material
    )
    
    fine_tools = GCodeTools(fine_params)
    thick_tools = GCodeTools(thick_params)
    print("✓ Fine detail and thick material configurations created")
    
    # Example 5: Path joining comparison
    print("\n=== Example 5: Path joining comparison ===")
    
    # With path joining enabled
    joined_params = CuttingParameters(
        material_thickness=1.0,
        cut_depth_per_pass=0.2,
        number_of_passes=5,
        knife_force=100.0,
        knife_offset=0.3,
        join_paths=True,         # Enable path joining
        path_tolerance=0.1
    )
    
    # Without path joining
    separate_params = CuttingParameters(
        material_thickness=1.0,
        cut_depth_per_pass=0.2,
        number_of_passes=5,
        knife_force=100.0,
        knife_offset=0.3,
        join_paths=False,        # Disable path joining
        path_tolerance=0.1
    )
    
    joined_tools = GCodeTools(joined_params)
    separate_tools = GCodeTools(separate_params)
    print("✓ Path joining comparison configurations created")
    print("  - Joined paths: Minimizes tool lifts for connected segments")
    print("  - Separate paths: Each segment gets individual tool lift/lower")
    
    print("\n=== All examples completed ===")

if __name__ == "__main__":
    main()
