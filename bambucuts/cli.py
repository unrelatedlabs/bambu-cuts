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
        material_thickness=0.0,  # For plotting, no Z depth
        cutting_speed=args.feed_rate,
        movement_speed=3000.0,
        join_paths=True,
        knife_offset=0.0,  # No offset for pen plotting
        origin_top_left=True,
        mirror_y=True  # Mirror Y by default for correct orientation
    )

    # Convert
    try:
        tools = GCodeTools(params)
        gcode = tools.svg_to_gcode(str(input_path), str(output_path))

        # Write output (svg_to_gcode already writes the file if output_path is provided)
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


def cmd_mqtt_dump(args):
    """Dump raw MQTT report messages from the configured printer."""
    if args.json and args.ndjson:
        print("Use either --json or --ndjson, not both", file=sys.stderr)
        sys.exit(2)
    if args.follow and args.json:
        print("--follow streams messages; use --ndjson or text output instead of --json", file=sys.stderr)
        sys.exit(2)

    from bambucuts import config
    try:
        from bambucuts.mqtt_dump import MqttDumpError, dump_mqtt, format_message
    except ImportError as e:
        print(f"MQTT dump requires paho-mqtt: {e}", file=sys.stderr)
        sys.exit(1)

    cfg = config.get_config()
    ip = args.ip or cfg.get('ip', '')
    serial = args.serial or cfg.get('serial', '')
    access_code = args.access_code or cfg.get('access_code', '')
    duration = None if args.follow else args.seconds
    max_messages = args.count if args.count is not None else (0 if args.follow else 5)
    seen_count = 0

    def print_live_message(message):
        nonlocal seen_count
        seen_count += 1
        if args.ndjson:
            import json
            print(json.dumps({"type": "message", "message": message}, sort_keys=True), flush=True)
        else:
            if seen_count > 1 and not args.raw:
                print()
            print(format_message(message, index=seen_count, raw=args.raw), flush=True)

    try:
        if not args.json:
            limit = "until interrupted" if duration is None and max_messages == 0 else "for the requested window"
            print(f"Listening to Bambu MQTT reports {limit}...", file=sys.stderr)

        dump = dump_mqtt(
            ip,
            access_code,
            serial,
            duration=duration,
            max_messages=max_messages,
            request_pushall=not args.no_pushall,
            port=args.port,
            on_message=None if args.json else print_live_message,
        )
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return
    except MqttDumpError as e:
        print(f"MQTT dump failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        import json
        print(json.dumps(dump, indent=2, sort_keys=True))
    elif args.ndjson:
        import json
        print(json.dumps({
            "type": "summary",
            "message_count": dump["message_count"],
            "elapsed": dump["elapsed"],
            "errors": dump["errors"],
        }, sort_keys=True), flush=True)
    else:
        print(f"\nMessages: {dump['message_count']} in {dump['elapsed']:.2f}s", file=sys.stderr)


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

    # MQTT dump command
    mqtt_dump_parser = subparsers.add_parser('mqtt-dump', help='Dump raw Bambu MQTT report messages')
    mqtt_dump_parser.add_argument('--ip', help='Printer IP address (default: configured printer IP)')
    mqtt_dump_parser.add_argument('--serial', help='Printer serial number (default: configured serial)')
    mqtt_dump_parser.add_argument('--access-code', help='Printer LAN access code (default: configured access code)')
    mqtt_dump_parser.add_argument('--port', type=int, default=8883, help='Printer MQTT port (default: 8883)')
    mqtt_dump_parser.add_argument('--seconds', type=float, default=5.0, help='Seconds to collect messages (default: 5)')
    mqtt_dump_parser.add_argument('--count', type=int, default=None, help='Stop after this many messages, 0 for no limit (default: 5, or 0 with --follow)')
    mqtt_dump_parser.add_argument('--follow', action='store_true', help='Keep streaming reports until Ctrl-C or --count is reached')
    mqtt_dump_parser.add_argument('--no-pushall', action='store_true', help='Do not request a full printer report')
    mqtt_dump_parser.add_argument('--raw', action='store_true', help='Print raw payload strings instead of pretty JSON messages')
    mqtt_dump_parser.add_argument('--json', action='store_true', help='Print the whole dump result as JSON')
    mqtt_dump_parser.add_argument('--ndjson', action='store_true', help='Stream one JSON object per line as messages arrive')
    mqtt_dump_parser.set_defaults(func=cmd_mqtt_dump)

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute command
    args.func(args)


if __name__ == '__main__':
    main()
