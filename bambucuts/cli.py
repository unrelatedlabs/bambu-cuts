#!/usr/bin/env python3
"""
Bambu Cuts CLI - Command Line Interface

Provides command-line tools for:
- Starting the web server
- Converting SVG to G-code
- Converting DXF to SVG
"""

import sys
import argparse
from pathlib import Path


def cmd_server(args):
    """Start the Bambu Cuts web server."""
    from bambucuts.webui import start_server
    start_server(host=args.host, port=args.port, debug=args.debug)


def cmd_svg2gcode(args):
    """Convert SVG file to G-code."""
    from bambucuts.gcodetools import GCodeTools, CuttingParameters

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix('.gcode')

    print(f"Converting {input_path} to G-code...")

    # Set up cutting parameters
    params = CuttingParameters(
        tool_diameter=args.tool_diameter,
        cutting_depth=args.depth,
        feed_rate=args.feed_rate,
        plunge_rate=args.plunge_rate,
        safe_height=args.safe_height
    )

    # Convert
    try:
        tools = GCodeTools(str(input_path), params)
        gcode = tools.svg_to_gcode()

        # Write output
        output_path.write_text(gcode)
        print(f"G-code written to: {output_path}")
        print(f"Generated {len(gcode.splitlines())} lines of G-code")
    except Exception as e:
        print(f"Error converting SVG: {e}")
        sys.exit(1)


def cmd_dxf2svg(args):
    """Convert DXF file to SVG."""
    from bambucuts.dxf2svg import convert_dxf_to_svg

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix('.svg')

    print(f"Converting {input_path} to SVG...")

    try:
        convert_dxf_to_svg(str(input_path), str(output_path))
        print(f"SVG written to: {output_path}")
    except Exception as e:
        print(f"Error converting DXF: {e}")
        sys.exit(1)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Bambu Cuts - Cutter and Plotter for Bambu Lab Printers',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Server command
    server_parser = subparsers.add_parser('server', help='Start the web server')
    server_parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    server_parser.add_argument('--port', type=int, default=5425, help='Port to bind to (default: 5425)')
    server_parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    server_parser.set_defaults(func=cmd_server)

    # SVG to G-code command
    svg2gcode_parser = subparsers.add_parser('svg2gcode', help='Convert SVG to G-code')
    svg2gcode_parser.add_argument('input', help='Input SVG file')
    svg2gcode_parser.add_argument('-o', '--output', help='Output G-code file (default: input.gcode)')
    svg2gcode_parser.add_argument('--tool-diameter', type=float, default=0.4, help='Tool diameter in mm (default: 0.4)')
    svg2gcode_parser.add_argument('--depth', type=float, default=-0.1, help='Cutting depth in mm (default: -0.1)')
    svg2gcode_parser.add_argument('--feed-rate', type=float, default=1000, help='Feed rate in mm/min (default: 1000)')
    svg2gcode_parser.add_argument('--plunge-rate', type=float, default=500, help='Plunge rate in mm/min (default: 500)')
    svg2gcode_parser.add_argument('--safe-height', type=float, default=5.0, help='Safe height in mm (default: 5.0)')
    svg2gcode_parser.set_defaults(func=cmd_svg2gcode)

    # DXF to SVG command
    dxf2svg_parser = subparsers.add_parser('dxf2svg', help='Convert DXF to SVG')
    dxf2svg_parser.add_argument('input', help='Input DXF file')
    dxf2svg_parser.add_argument('-o', '--output', help='Output SVG file (default: input.svg)')
    dxf2svg_parser.set_defaults(func=cmd_dxf2svg)

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute command
    args.func(args)


if __name__ == '__main__':
    main()
