#!/usr/bin/env python3
"""
Bambu Cuts - Cutter and Plotter API

A Flask API backend for controlling Bambu Lab printers as CNC cutters/plotters.
Provides RESTful endpoints for printer control, jogging, and G-code execution.

Author: AI Assistant
"""

from flask import Flask, render_template, jsonify, request, send_file, Response, stream_with_context
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
import json
import time
import sys
import os
import tempfile
from pathlib import Path
import threading
import base64
import math
from io import BytesIO

try:
    import bambulabs_api as bl
    from bambucuts import config
    from bambucuts.compress_3mf import process_3mf
    from bambucuts.gcodetools import GCodeTools, CuttingParameters
    from bambucuts.dxf2svg import convert_dxf_to_svg
except ImportError as e:
    print(f"Error importing required modules: {e}")
    print("Make sure bambulabs_api is installed and bambucuts is available")
    import traceback
    traceback.print_exc()
    sys.exit(1)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a1plotter-secret-key'
CORS(app)  # Enable CORS for all routes
socketio = SocketIO(app, cors_allowed_origins="*")

# Global state
state = {
    'position': {'x': 0.0, 'y': 0.0, 'z': 0.0, 'e': 0.0},
    'step_size': 1.0,
    'feed_rate': 1000.0,
    'printer_connected': False,
    'connection_error': None,
    'gcode_history': [],
    'camera_streaming': False,
    'mqtt_status': {
        'connected': False,
        'error': None,
        'last_update': None,
        'print': {}
    },
    'direct_job': {
        'active': False,
        'status': 'idle'
    }
}

# Printer instance
printer = None

# Direct-send progress markers. Each marker is queued after an M400, so MQTT
# progress reflects completed motion up to that batch.
PROGRESS_BATCH_SIZE = 25

# Camera streaming control
camera_thread = None
camera_stop_event = threading.Event()
camera_frame_lock = threading.Lock()
latest_camera_frame = None
latest_camera_frame_time = None

# MQTT status monitor control
mqtt_status_thread = None
mqtt_status_stop_event = threading.Event()
mqtt_status_lock = threading.Lock()
direct_job_lock = threading.Lock()


def connect_printer():
    """Connect to the BambuLab printer."""
    global printer, state

    try:
        # Get current config values
        cfg = config._config_data
        print(f"Connecting to printer at {cfg['ip']}...")
        printer = bl.Printer(cfg['ip'], cfg['access_code'], cfg['serial'])
        printer.connect()
        time.sleep(2)  # Allow time for connection to establish
        state['printer_connected'] = True
        state['connection_error'] = None
        print("Successfully connected to printer")

        # Set relative mode for jogging
        set_relative_mode()

        start_mqtt_status_monitor()

        return True
    except Exception as e:
        state['printer_connected'] = False
        state['connection_error'] = str(e)
        print(f"Failed to connect to printer: {e}")
        return False


def disconnect_printer():
    """Disconnect from the BambuLab printer."""
    global printer, state

    try:
        stop_camera_stream()
        stop_mqtt_status_monitor()

        if printer and state['printer_connected']:
            # Restore absolute mode before disconnecting
            set_absolute_mode()
            printer.disconnect()
            print("Disconnected from printer")
    except Exception as e:
        print(f"Error disconnecting from printer: {e}")
    finally:
        state['printer_connected'] = False
        printer = None


def _merge_mqtt_print_status(print_status):
    """Merge Bambu's sparse MQTT print status deltas into the cached state."""
    if not isinstance(print_status, dict):
        return

    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        cached_print.update(print_status)
        state['mqtt_status'] = {
            'connected': True,
            'error': None,
            'last_update': time.time(),
            'print': cached_print
        }


def _handle_mqtt_status_message(message):
    payload = message.get('json')
    if not isinstance(payload, dict):
        return

    print_status = payload.get('print')
    _merge_mqtt_print_status(print_status)


def start_mqtt_status_monitor():
    """Start a background MQTT listener that caches the latest print status."""
    global mqtt_status_thread, mqtt_status_stop_event

    if mqtt_status_thread and mqtt_status_thread.is_alive():
        return True

    cfg = config.get_config()
    if not cfg.get('ip') or not cfg.get('access_code') or not cfg.get('serial'):
        return False

    try:
        from bambucuts.mqtt_dump import MqttDumpError, listen_mqtt_reports
    except ImportError as e:
        with mqtt_status_lock:
            state['mqtt_status']['error'] = f'MQTT status requires paho-mqtt: {e}'
        return False

    mqtt_status_stop_event.clear()

    def worker():
        while not mqtt_status_stop_event.is_set():
            try:
                listen_mqtt_reports(
                    cfg.get('ip', ''),
                    cfg.get('access_code', ''),
                    cfg.get('serial', ''),
                    _handle_mqtt_status_message,
                    stop_event=mqtt_status_stop_event,
                    request_pushall=True,
                )
            except MqttDumpError as e:
                with mqtt_status_lock:
                    cached_print = dict(state['mqtt_status'].get('print') or {})
                    state['mqtt_status'] = {
                        'connected': False,
                        'error': str(e),
                        'last_update': state['mqtt_status'].get('last_update'),
                        'print': cached_print
                    }
                if not mqtt_status_stop_event.wait(5):
                    continue

    mqtt_status_thread = threading.Thread(target=worker, daemon=True)
    mqtt_status_thread.start()
    return True


def stop_mqtt_status_monitor():
    """Stop the background MQTT status listener."""
    global mqtt_status_thread

    mqtt_status_stop_event.set()
    if mqtt_status_thread:
        mqtt_status_thread.join(timeout=5)
    mqtt_status_thread = None

    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        state['mqtt_status'] = {
            'connected': False,
            'error': None,
            'last_update': state['mqtt_status'].get('last_update'),
            'print': cached_print
        }


def camera_stream_worker():
    """Background worker thread for streaming camera frames."""
    global printer, camera_stop_event

    print("Camera stream worker started")

    try:
        if not printer.camera_client_alive():
            if not printer.camera_start():
                print("Failed to start camera")
                return
            time.sleep(1)  # Give camera time to initialize

        while not camera_stop_event.is_set():
            try:
                # Get camera frame (base64 encoded)
                frame_base64 = printer.get_camera_frame()

                if frame_base64:
                    # Process frame (placeholder for future CV work)
                    processed_frame = process_camera_frame(frame_base64)
                    remember_camera_frame(processed_frame)

                    # Emit frame to all connected clients
                    socketio.emit('camera_frame', {'frame': processed_frame}, namespace='/')

                # Limit frame rate to ~10 FPS
                time.sleep(0.1)

            except Exception as e:
                print(f"Error streaming frame: {e}")
                time.sleep(0.5)

    except Exception as e:
        print(f"Camera stream worker error: {e}")
    finally:
        state['camera_streaming'] = False
        print("Camera stream worker stopped")


def process_camera_frame(frame_base64: str) -> str:
    """
    Process camera frame for computer vision tasks.

    This is a placeholder function that can be extended with:
    - Object detection
    - Print monitoring
    - Quality control
    - Defect detection

    Args:
        frame_base64: Base64 encoded image from camera

    Returns:
        Processed frame as base64 string
    """
    # For now, just return the original frame
    # In the future, add CV processing here:
    # - OpenCV operations
    # - ML model inference
    # - Overlay graphics
    # - Measurements

    try:
        # Decode base64 to image
        # image_data = base64.b64decode(frame_base64)
        # img = Image.open(BytesIO(image_data))

        # TODO: Add CV processing here
        # Example: img = apply_filters(img)
        # Example: img = detect_objects(img)
        # Example: img = overlay_measurements(img)

        # Return processed frame
        # processed_buffer = BytesIO()
        # img.save(processed_buffer, format='JPEG')
        # return base64.b64encode(processed_buffer.getvalue()).decode('utf-8')

        return frame_base64  # Return original for now

    except Exception as e:
        print(f"Error processing camera frame: {e}")
        return frame_base64


def remember_camera_frame(frame_base64: str):
    """Cache the latest camera frame for HTTP polling clients."""
    global latest_camera_frame, latest_camera_frame_time
    with camera_frame_lock:
        latest_camera_frame = frame_base64
        latest_camera_frame_time = time.time()


def start_camera_stream():
    """Start camera streaming in background thread."""
    global camera_thread, camera_stop_event, state, latest_camera_frame, latest_camera_frame_time

    if state['camera_streaming']:
        print("Camera already streaming")
        return True

    if not printer or not state['printer_connected']:
        print("Printer not connected")
        return False

    # Reset stop event
    camera_stop_event.clear()
    with camera_frame_lock:
        latest_camera_frame = None
        latest_camera_frame_time = None

    # Start camera thread
    state['camera_streaming'] = True
    camera_thread = threading.Thread(target=camera_stream_worker, daemon=True)
    camera_thread.start()

    return True


def stop_camera_stream():
    """Stop camera streaming."""
    global camera_thread, camera_stop_event, state, latest_camera_frame, latest_camera_frame_time

    if not state['camera_streaming']:
        with camera_frame_lock:
            latest_camera_frame = None
            latest_camera_frame_time = None
        return

    print("Stopping camera stream...")
    camera_stop_event.set()

    if camera_thread:
        camera_thread.join(timeout=5)

    state['camera_streaming'] = False
    with camera_frame_lock:
        latest_camera_frame = None
        latest_camera_frame_time = None


def set_relative_mode():
    """Set printer to relative positioning mode for jogging."""
    if not state['printer_connected'] or not printer:
        return False

    try:
        printer.gcode("G91")
        print("Set printer to relative mode (G91)")
        return True
    except Exception as e:
        print(f"Failed to set relative mode: {e}")
        return False


def set_absolute_mode():
    """Set printer to absolute positioning mode."""
    if not state['printer_connected'] or not printer:
        return False

    try:
        printer.gcode("G90")
        print("Set printer to absolute mode (G90)")
        return True
    except Exception as e:
        print(f"Failed to set absolute mode: {e}")
        return False


def send_gcode_to_printer(gcode: str) -> bool:
    """Send G-code command to the printer."""
    if not state['printer_connected'] or not printer:
        print(f"Cannot send G-code - printer not connected: {gcode}")
        return False

    try:
        printer.gcode(gcode)
        print(f"G-code sent to printer: {gcode}")
        return True
    except Exception as e:
        print(f"Failed to send G-code to printer: {e}")
        state['connection_error'] = str(e)
        return False


def add_to_history(gcode: str):
    """Add G-code command to history."""
    state['gcode_history'].append(gcode)
    # Keep only last 20 commands
    if len(state['gcode_history']) > 20:
        state['gcode_history'].pop(0)


def strip_inline_comment(line: str) -> str:
    """Return the executable portion of a G-code line."""
    return line.split(';', 1)[0].strip()


def is_executable_gcode_line(line: str) -> bool:
    return bool(strip_inline_comment(line))


def extract_executable_gcode(gcode_text: str):
    """Yield (line_number, executable_gcode) for non-empty G-code lines."""
    for line_num, line in enumerate(gcode_text.split('\n'), 1):
        code = strip_inline_comment(line)
        if code:
            yield line_num, code


def clamp_progress_batch_size(value) -> int:
    try:
        batch_size = int(value)
    except (TypeError, ValueError):
        batch_size = PROGRESS_BATCH_SIZE
    return max(1, min(500, batch_size))


def current_mqtt_marker_pair():
    """Return the currently cached (M73 P, M73 R) pair."""
    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        return (
            cached_print.get('mc_percent'),
            cached_print.get('mc_remaining_time'),
        )


def _scaled_marker_percents(marker_count: int, mode: str = 'normal'):
    if marker_count == 1:
        if mode == 'lower':
            return [99]
        if mode == 'upper':
            return [2]
        return [100]

    if mode == 'lower':
        return [max(1, math.floor(index * 99 / marker_count)) for index in range(1, marker_count + 1)]
    if mode == 'upper':
        return [
            2 + math.floor((index - 1) * 98 / (marker_count - 1))
            for index in range(1, marker_count + 1)
        ]
    return [max(1, math.floor(index * 100 / marker_count)) for index in range(1, marker_count + 1)]


def build_progress_marker_plan(total_commands: int, requested_batch_size: int, baseline_pair=None):
    """Build unique M73 checkpoint pairs, avoiding the cached baseline value."""
    if total_commands <= 0:
        return [], requested_batch_size

    batch_size = max(1, requested_batch_size)
    marker_count = math.ceil(total_commands / batch_size)
    if marker_count > 99:
        batch_size = math.ceil(total_commands / 99)
        marker_count = math.ceil(total_commands / batch_size)

    baseline_pair = baseline_pair or (None, None)
    candidate_modes = [
        ('normal', 0),
        ('lower', 0),
        ('upper', 0),
        ('normal', 1),
        ('lower', 1),
        ('upper', 1),
    ]

    for percent_mode, remaining_offset in candidate_modes:
        percents = _scaled_marker_percents(marker_count, mode=percent_mode)
        pairs = [
            (percent, marker_count - index + remaining_offset)
            for index, percent in enumerate(percents, 1)
        ]
        if len(set(pairs)) != len(pairs):
            continue
        if pairs[0] == baseline_pair or pairs[-1] == baseline_pair:
            continue

        return [
            {
                'index': index,
                'percent': percent,
                'remaining': remaining,
            }
            for index, (percent, remaining) in enumerate(pairs, 1)
        ], batch_size

    # This should be unreachable for marker_count <= 100, but keep a safe fallback.
    percents = _scaled_marker_percents(marker_count, mode='lower')
    return [
        {
            'index': index,
            'percent': percent,
            'remaining': marker_count - index + 1,
        }
        for index, percent in enumerate(percents, 1)
    ], batch_size


def progress_marker_lines(completed_commands: int, total_commands: int, marker):
    """Create M73 marker lines after a completed batch."""
    if total_commands <= 0:
        return []

    return [
        f"; bambucuts progress: {completed_commands}/{total_commands}",
        "M400 ; wait for queued motion before reporting progress",
        f"M73 P{marker['percent']} R{marker['remaining']}",
        f"M73 L{marker['index']}",
    ]


def add_m73_progress_markers(gcode_text: str, batch_size: int = PROGRESS_BATCH_SIZE, baseline_pair=None):
    """Insert M73 progress markers after each batch of executable G-code."""
    batch_size = clamp_progress_batch_size(batch_size)
    lines = gcode_text.splitlines()
    total_commands = sum(1 for line in lines if is_executable_gcode_line(line))

    if total_commands == 0:
        return gcode_text, {
            'enabled': False,
            'batch_size': batch_size,
            'command_count': 0,
            'marker_count': 0,
            'markers': [],
        }

    markers, batch_size = build_progress_marker_plan(total_commands, batch_size, baseline_pair=baseline_pair)
    output_lines = ["; bambucuts progress start"]
    completed_commands = 0
    marker_index = 0

    for line in lines:
        output_lines.append(line)

        if not is_executable_gcode_line(line):
            continue

        completed_commands += 1
        if completed_commands % batch_size == 0 or completed_commands == total_commands:
            marker_index += 1
            marker = markers[marker_index - 1]
            output_lines.extend(progress_marker_lines(
                completed_commands,
                total_commands,
                marker,
            ))

    return '\n'.join(output_lines), {
        'enabled': True,
        'batch_size': batch_size,
        'command_count': total_commands,
        'marker_count': marker_index,
        'markers': markers,
    }


def begin_direct_job(progress_info):
    """Start tracking a direct-send job by its M73 checkpoints."""
    started_at = time.time()
    job = {
        'id': str(int(started_at * 1000)),
        'active': True,
        'status': 'queueing',
        'started_at': started_at,
        'completed_at': None,
        'queued_at': None,
        'queued_count': 0,
        'command_count': progress_info.get('command_count', 0),
        'marker_count': progress_info.get('marker_count', 0),
        'markers': progress_info.get('markers', []),
        'batch_size': progress_info.get('batch_size', PROGRESS_BATCH_SIZE),
        'last_marker_index': 0,
        'logical_percent': 0,
        'last_percent': None,
        'last_remaining_time': None,
        'last_update': None,
        'message': 'Queueing G-code and waiting for M73 checkpoints',
    }

    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        cached_print['mc_percent'] = None
        cached_print['mc_remaining_time'] = None
        cached_print['layer_num'] = None
        state['mqtt_status']['print'] = cached_print

    with direct_job_lock:
        state['direct_job'] = job

    return job.copy()


def mark_direct_job_queued(job_id, queued_count, errors):
    """Record whether direct G-code was queued successfully."""
    with direct_job_lock:
        job = dict(state.get('direct_job') or {})
        if job.get('id') != job_id:
            return job

        job['queued_at'] = time.time()
        job['queued_count'] = queued_count
        if errors:
            job['active'] = False
            job['status'] = 'queue_error'
            job['message'] = 'Failed while queueing direct G-code'
            job['errors'] = errors
        else:
            job['status'] = 'waiting'
            job['message'] = 'Queued; waiting for printer to reach M73 checkpoints'

        state['direct_job'] = job
        return job.copy()


def update_direct_job_from_mqtt(mqtt_progress):
    """Update the active direct job from cached MQTT M73 progress."""
    with direct_job_lock:
        job = dict(state.get('direct_job') or {})

        if not job.get('active'):
            return job

        progress_update = mqtt_progress.get('last_update')
        percent = mqtt_progress.get('percent')
        remaining_time = mqtt_progress.get('remaining_time')
        if progress_update is None or progress_update < job.get('started_at', 0) or percent is None:
            return job

        marker_pairs = {
            (marker.get('percent'), marker.get('remaining')): marker.get('index')
            for marker in job.get('markers', [])
        }
        marker_index = marker_pairs.get((percent, remaining_time))
        if marker_index is None:
            return job

        previous_marker_index = job.get('last_marker_index') or 0
        if marker_index < previous_marker_index:
            return job

        marker_count = job.get('marker_count') or 0
        job['last_percent'] = percent
        job['last_remaining_time'] = remaining_time
        job['last_marker_index'] = marker_index
        job['logical_percent'] = round((marker_index / marker_count) * 100) if marker_count else 0
        job['last_update'] = progress_update

        if marker_count and marker_index >= marker_count:
            job['active'] = False
            job['status'] = 'complete'
            job['completed_at'] = progress_update
            job['message'] = 'Direct G-code execution reached final M73 checkpoint'
        else:
            job['status'] = 'running'
            job['message'] = f'Direct G-code reached checkpoint {marker_index}/{marker_count}'

        state['direct_job'] = job
        return job.copy()


def _bounded_query_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_query_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _query_bool(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Routes

@app.route('/')
def index():
    """Serve the main page."""
    return render_template('index.html')


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get current status including position and connection state."""
    printer_state = None
    camera_alive = False
    if state['printer_connected'] and printer:
        try:
            printer_state = str(printer.get_state())
            camera_alive = printer.camera_client_alive()
        except Exception as e:
            print(f"Error getting printer state: {e}")

    with mqtt_status_lock:
        mqtt_status = {
            'connected': state['mqtt_status'].get('connected'),
            'error': state['mqtt_status'].get('error'),
            'last_update': state['mqtt_status'].get('last_update'),
            'print': dict(state['mqtt_status'].get('print') or {}),
        }

    mqtt_print = mqtt_status['print']
    last_update = mqtt_status.get('last_update')
    mqtt_progress = {
        'percent': mqtt_print.get('mc_percent'),
        'remaining_time': mqtt_print.get('mc_remaining_time'),
        'layer_num': mqtt_print.get('layer_num'),
        'total_layer_num': mqtt_print.get('total_layer_num'),
        'print_line_number': mqtt_print.get('mc_print_line_number'),
        'print_stage': mqtt_print.get('mc_print_stage'),
        'print_sub_stage': mqtt_print.get('mc_print_sub_stage'),
        'gcode_state': mqtt_print.get('gcode_state'),
        'print_type': mqtt_print.get('print_type'),
        'last_update': last_update,
        'age': (time.time() - last_update) if last_update else None,
        'mqtt_connected': mqtt_status.get('connected'),
        'mqtt_error': mqtt_status.get('error'),
    }

    direct_job = update_direct_job_from_mqtt(mqtt_progress)

    return jsonify({
        'position': state['position'],
        'step_size': state['step_size'],
        'printer_connected': state['printer_connected'],
        'connection_error': state['connection_error'],
        'printer_ip': config._config_data.get('ip', ''),
        'printer_state': printer_state,
        'mqtt_progress': mqtt_progress,
        'mqtt_status': mqtt_status,
        'direct_job': direct_job,
        'camera_streaming': state['camera_streaming'],
        'camera_alive': camera_alive
    })


@app.route('/api/mqtt-dump', methods=['GET'])
def mqtt_dump_endpoint():
    """Dump raw Bambu MQTT report messages for debugging printer telemetry."""
    try:
        from bambucuts.mqtt_dump import MqttDumpError, dump_mqtt
    except ImportError as e:
        return jsonify({
            'success': False,
            'error': f'MQTT dump requires paho-mqtt: {e}',
        }), 500

    cfg = config.get_config()
    seconds = _bounded_query_float('seconds', 5.0, 0.1, 30.0)
    count = _bounded_query_int('count', 5, 0, 50)
    no_pushall = _query_bool('no_pushall', False)

    try:
        dump = dump_mqtt(
            cfg.get('ip', ''),
            cfg.get('access_code', ''),
            cfg.get('serial', ''),
            duration=seconds,
            max_messages=count,
            request_pushall=not no_pushall,
        )
        return jsonify(dump)
    except MqttDumpError as e:
        return jsonify({
            'success': False,
            'error': str(e),
        }), 500


@app.route('/api/mqtt-stream', methods=['GET'])
def mqtt_stream_endpoint():
    """Stream raw Bambu MQTT report messages as newline-delimited JSON."""
    try:
        from bambucuts.mqtt_dump import MqttDumpError, stream_mqtt
    except ImportError as e:
        return jsonify({
            'success': False,
            'error': f'MQTT stream requires paho-mqtt: {e}',
        }), 500

    cfg = config.get_config()
    seconds = _bounded_query_float('seconds', 60.0, 1.0, 300.0)
    count = _bounded_query_int('count', 0, 0, 1000)
    no_pushall = _query_bool('no_pushall', False)

    def generate():
        try:
            for event in stream_mqtt(
                cfg.get('ip', ''),
                cfg.get('access_code', ''),
                cfg.get('serial', ''),
                duration=seconds,
                max_messages=count,
                request_pushall=not no_pushall,
            ):
                yield json.dumps(event, sort_keys=True) + "\n"
        except MqttDumpError as e:
            yield json.dumps({
                "type": "error",
                "error": str(e),
            }, sort_keys=True) + "\n"

    return Response(
        stream_with_context(generate()),
        mimetype='application/x-ndjson',
        headers={'X-Accel-Buffering': 'no'},
    )


@app.route('/api/history', methods=['GET'])
def get_history():
    """Get G-code command history."""
    return jsonify({
        'history': state['gcode_history']
    })


@app.route('/api/connect', methods=['POST'])
def toggle_connection():
    """Connect or disconnect from printer."""
    if state['printer_connected']:
        disconnect_printer()
        return jsonify({
            'success': True,
            'connected': False,
            'message': 'Disconnected from printer'
        })
    else:
        success = connect_printer()
        return jsonify({
            'success': success,
            'connected': success,
            'message': 'Connected to printer' if success else f'Failed to connect: {state["connection_error"]}'
        })


@app.route('/api/move', methods=['POST'])
def move_axis():
    """Move specified axis by distance."""
    data = request.json
    axis = data.get('axis', '').lower()
    distance = float(data.get('distance', 0))

    if axis not in ['x', 'y', 'z', 'e']:
        return jsonify({'success': False, 'error': 'Invalid axis'}), 400

    # Update position
    state['position'][axis] += distance

    # Generate G-code
    gcode_relative = "G91"
    gcode_move = f"G1 {axis.upper()}{distance:.3f} F{state['feed_rate']:.0f}"

    # Add to history
    add_to_history(gcode_relative)
    add_to_history(gcode_move)

    # Send to printer if connected
    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode_relative) and send_gcode_to_printer(gcode_move)

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': [gcode_relative, gcode_move]
    })


@app.route('/api/home', methods=['POST'])
def home_xy():
    """Home X and Y axes."""
    # Reset position
    state['position']['x'] = 0.0
    state['position']['y'] = 0.0

    # For homing, temporarily switch to absolute mode
    if state['printer_connected']:
        set_absolute_mode()
        time.sleep(0.1)

    gcode = "G28 X Y"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)
        time.sleep(0.1)
        set_relative_mode()

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/save-z-zero', methods=['POST'])
def save_z_zero():
    """Save current Z position as zero."""
    state['position']['z'] = 0.0

    gcode = "G92 Z0"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/reset-e-zero', methods=['POST'])
def reset_e_zero():
    """Reset E position to zero."""
    state['position']['e'] = 0.0

    gcode = "G92 E0"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/move-z-absolute', methods=['POST'])
def move_z_absolute():
    """Move Z to absolute position."""
    data = request.json
    z_position = float(data.get('position', 0))

    state['position']['z'] = z_position

    # Switch to absolute mode
    if state['printer_connected']:
        set_absolute_mode()
        time.sleep(0.1)

    gcode = f"G1 Z{z_position:.1f} F600"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)
        time.sleep(0.1)
        set_relative_mode()

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/gcode', methods=['POST'])
def execute_gcode():
    """Execute custom G-code command."""
    data = request.json
    gcode = data.get('gcode', '').strip()

    if not gcode:
        return jsonify({'success': False, 'error': 'Empty G-code'}), 400

    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

    # Try to parse position updates from G-code
    gcode_upper = gcode.upper()
    if gcode_upper.startswith('G1') or gcode_upper.startswith('G0'):
        parts = gcode_upper.split()
        for part in parts[1:]:
            if part.startswith('X'):
                try:
                    state['position']['x'] = float(part[1:])
                except ValueError:
                    pass
            elif part.startswith('Y'):
                try:
                    state['position']['y'] = float(part[1:])
                except ValueError:
                    pass
            elif part.startswith('Z'):
                try:
                    state['position']['z'] = float(part[1:])
                except ValueError:
                    pass
            elif part.startswith('E'):
                try:
                    state['position']['e'] = float(part[1:])
                except ValueError:
                    pass

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/step-size', methods=['POST'])
def set_step_size():
    """Set step size."""
    data = request.json
    step_size = float(data.get('step_size', 1.0))
    state['step_size'] = step_size

    return jsonify({
        'success': True,
        'step_size': state['step_size']
    })


@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration."""
    return jsonify({
        'ip': config._config_data.get('ip', ''),
        'serial': config._config_data.get('serial', ''),
        'access_code': config._config_data.get('access_code', '')
    })


@app.route('/api/config', methods=['POST'])
def save_config_endpoint():
    """Update printer configuration and reconnect."""
    global printer

    data = request.json

    print(f"Updating config with IP={data.get('ip')}, Serial={data.get('serial')}")

    success, message = config.update_config(
        ip=data.get('ip'),
        serial=data.get('serial'),
        access_code=data.get('access_code')
    )

    if success:
        print(f"Config saved. Current config: {config._config_data}")

        # Clear printer object and state completely
        printer = None
        state['printer_connected'] = False
        state['connection_error'] = None

        # Disconnect from old printer if still connected
        try:
            disconnect_printer()
        except Exception as e:
            print(f"Error disconnecting (may already be disconnected): {e}")

        # Wait a moment
        time.sleep(1)

        # Reconnect with new settings
        print("Attempting to reconnect with new settings...")
        reconnect_success = connect_printer()

        if reconnect_success:
            print("Reconnection successful!")
            return jsonify({
                'success': True,
                'message': f'{message}. Reconnected to printer successfully.'
            })
        else:
            print(f"Reconnection failed: {state.get('connection_error', 'Unknown error')}")
            return jsonify({
                'success': False,
                'error': f"Config saved but reconnection failed: {state.get('connection_error', 'Could not connect to printer')}",
                'connection_failed': True
            }), 400
    else:
        return jsonify({
            'success': False,
            'error': message
        }), 500


@app.route('/api/gcode/validate', methods=['POST'])
def validate_gcode():
    """Validate G-code syntax."""
    data = request.json
    gcode_text = data.get('gcode', '')

    errors = []
    warnings = []
    line_num = 0

    for line in gcode_text.split('\n'):
        line_num += 1
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith(';'):
            continue

        # Remove inline comments
        if ';' in line:
            line = line.split(';')[0].strip()

        # Basic G-code validation
        if not line[0].upper() in ['G', 'M', 'T', 'N']:
            errors.append(f"Line {line_num}: Invalid command start '{line[0]}'")
            continue

        # Check for common issues
        if line.upper().startswith('G') or line.upper().startswith('M'):
            # Check if there's a number after G/M
            if len(line) < 2 or not line[1].isdigit():
                errors.append(f"Line {line_num}: Missing command number")

    return jsonify({
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'line_count': line_num
    })


@app.route('/api/gcode/format', methods=['POST'])
def format_gcode():
    """Format G-code with proper spacing and comments."""
    data = request.json
    gcode_text = data.get('gcode', '')

    formatted_lines = []

    for line in gcode_text.split('\n'):
        line = line.strip()

        # Keep empty lines and comments as-is
        if not line or line.startswith(';'):
            formatted_lines.append(line)
            continue

        # Split command and comment
        if ';' in line:
            code_part, comment_part = line.split(';', 1)
            code_part = code_part.strip()
            comment_part = comment_part.strip()
            formatted_lines.append(f"{code_part:<20} ; {comment_part}")
        else:
            formatted_lines.append(line)

    return jsonify({
        'formatted': '\n'.join(formatted_lines)
    })


@app.route('/api/gcode/send-all', methods=['POST'])
def send_all_gcode():
    """Send all G-code lines from editor."""
    data = request.json
    gcode_text = data.get('gcode', '')
    progress_markers = data.get('progress_markers', True)
    progress_batch_size = clamp_progress_batch_size(data.get('progress_batch_size', PROGRESS_BATCH_SIZE))

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to send'}), 400

    progress_info = {
        'enabled': False,
        'batch_size': progress_batch_size,
        'command_count': sum(1 for _ in extract_executable_gcode(gcode_text)),
        'marker_count': 0,
    }
    gcode_to_send = gcode_text
    if progress_markers:
        gcode_to_send, progress_info = add_m73_progress_markers(
            gcode_text,
            progress_batch_size,
            baseline_pair=current_mqtt_marker_pair(),
        )

    sent_count = 0
    queued_count = 0
    errors = []
    direct_job = None
    if state['printer_connected'] and progress_info.get('enabled'):
        direct_job = begin_direct_job(progress_info)

    for line_num, line in extract_executable_gcode(gcode_to_send):
        # Add to history
        add_to_history(line)

        # Send to printer if connected
        if state['printer_connected']:
            success = send_gcode_to_printer(line)
            if not success:
                errors.append(f"Line {line_num}: Failed to send")

        queued_count += 1
        if not line.upper().startswith(('M73', 'M400')):
            sent_count += 1
        time.sleep(0.05)  # Small delay between commands

    if direct_job:
        direct_job = mark_direct_job_queued(direct_job['id'], queued_count, errors)

    return jsonify({
        'success': len(errors) == 0,
        'sent_count': sent_count,
        'queued_count': queued_count,
        'progress': progress_info,
        'direct_job': direct_job,
        'errors': errors
    })


@app.route('/api/gcode/send-all-3mf', methods=['POST'])
def send_all_gcode_3mf():
    """Send G-code by converting to 3MF and uploading to printer."""
    data = request.json
    gcode_text = data.get('gcode', '')
    filename = data.get('filename', 'plot.gcode')
    progress_markers = data.get('progress_markers', True)
    progress_batch_size = clamp_progress_batch_size(data.get('progress_batch_size', PROGRESS_BATCH_SIZE))

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to send'}), 400

    if not state['printer_connected']:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 400

    # Create temporary directory for files
    temp_dir = tempfile.mkdtemp()
    temp_gcode_path = None
    temp_3mf_path = None

    try:
        progress_info = {
            'enabled': False,
            'batch_size': progress_batch_size,
            'command_count': sum(1 for _ in extract_executable_gcode(gcode_text)),
            'marker_count': 0,
        }
        gcode_to_package = gcode_text
        if progress_markers:
            gcode_to_package, progress_info = add_m73_progress_markers(
                gcode_text,
                progress_batch_size,
                baseline_pair=current_mqtt_marker_pair(),
            )

        # Save G-code to temporary file
        temp_gcode_path = os.path.join(temp_dir, 'temp_plot.gcode')
        with open(temp_gcode_path, 'w') as f:
            f.write(gcode_to_package)

        # Convert to 3MF using template
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     'template.3mf')

        if not os.path.exists(template_path):
            return jsonify({'success': False, 'error': f'Template file not found: {template_path}'}), 500

        # Generate output 3MF file
        output_3mf_name = filename.replace('.gcode', '.3mf') if filename.endswith('.gcode') else f"{filename}.3mf"
        temp_3mf_path = os.path.join(temp_dir, output_3mf_name)

        # Process 3MF
        process_3mf(template_path, temp_3mf_path, temp_gcode_path, verbose=False)

        # Upload to printer
        with open(temp_3mf_path, 'rb') as f:
            result = printer.upload_file(f, output_3mf_name)

        # Check if upload was successful (226 is FTP success code)
        if "226" not in result:
            return jsonify({'success': False, 'error': f'Upload failed: {result}'}), 500

        # Start the print
        printer.start_print(output_3mf_name, 1)

        return jsonify({
            'success': True,
            'message': f'Successfully uploaded and started printing {output_3mf_name}',
            'filename': output_3mf_name,
            'progress': progress_info
        })

    except Exception as e:
        print(f"Error in send_all_gcode_3mf: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

    finally:
        # Clean up temporary files
        try:
            if temp_gcode_path and os.path.exists(temp_gcode_path):
                os.remove(temp_gcode_path)
            if temp_3mf_path and os.path.exists(temp_3mf_path):
                os.remove(temp_3mf_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
        except Exception as e:
            print(f"Error cleaning up temporary files: {e}")


@app.route('/api/gcode/create-3mf', methods=['POST'])
def create_3mf():
    """Create 3MF file from G-code and return for download."""
    data = request.json
    gcode_text = data.get('gcode', '')
    filename = data.get('filename', 'plot.gcode')
    progress_markers = data.get('progress_markers', True)
    progress_batch_size = clamp_progress_batch_size(data.get('progress_batch_size', PROGRESS_BATCH_SIZE))

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to convert'}), 400

    # Create temporary directory for files
    temp_dir = tempfile.mkdtemp()
    temp_gcode_path = None
    temp_3mf_path = None

    try:
        gcode_to_package = gcode_text
        if progress_markers:
            gcode_to_package, _ = add_m73_progress_markers(
                gcode_text,
                progress_batch_size,
                baseline_pair=current_mqtt_marker_pair(),
            )

        # Save G-code to temporary file
        temp_gcode_path = os.path.join(temp_dir, 'temp_plot.gcode')
        with open(temp_gcode_path, 'w') as f:
            f.write(gcode_to_package)

        # Convert to 3MF using template
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     'template.3mf')

        if not os.path.exists(template_path):
            return jsonify({'success': False, 'error': f'Template file not found: {template_path}'}), 500

        # Generate output 3MF file
        output_3mf_name = filename.replace('.gcode', '.3mf') if filename.endswith('.gcode') else f"{filename}.3mf"
        temp_3mf_path = os.path.join(temp_dir, output_3mf_name)

        # Process 3MF
        process_3mf(template_path, temp_3mf_path, temp_gcode_path, verbose=False)

        # Return the file for download
        return send_file(
            temp_3mf_path,
            as_attachment=True,
            download_name=output_3mf_name,
            mimetype='application/vnd.ms-package.3dmanufacturing-3dmodel+xml'
        )

    except Exception as e:
        print(f"Error in create_3mf: {e}")
        # Clean up on error
        try:
            if temp_gcode_path and os.path.exists(temp_gcode_path):
                os.remove(temp_gcode_path)
            if temp_3mf_path and os.path.exists(temp_3mf_path):
                os.remove(temp_3mf_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
        except Exception as cleanup_error:
            print(f"Error cleaning up temporary files: {cleanup_error}")

        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/convert-to-gcode', methods=['POST'])
def convert_to_gcode():
    """Convert SVG or DXF file to G-code."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    file_type = request.form.get('file_type', '')

    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename'}), 400

    if file_type not in ['svg', 'dxf']:
        return jsonify({'success': False, 'error': 'Invalid file type. Only SVG and DXF are supported'}), 400

    # Create temporary directory for processing
    temp_dir = tempfile.mkdtemp()
    temp_input_path = None
    temp_svg_path = None
    temp_gcode_path = None

    try:
        # Save uploaded file
        filename = secure_filename(file.filename)
        temp_input_path = os.path.join(temp_dir, filename)
        file.save(temp_input_path)

        # Convert DXF to SVG if needed
        if file_type == 'dxf':
            temp_svg_path = os.path.join(temp_dir, filename.replace('.dxf', '.svg'))
            convert_dxf_to_svg(temp_input_path, temp_svg_path)
            svg_file_path = temp_svg_path
        else:
            svg_file_path = temp_input_path

        # Convert SVG to G-code
        params = CuttingParameters(
            material_thickness=0.0,  # For plotting, no Z depth
            cutting_speed=1000.0,
            movement_speed=3000.0,
            join_paths=True,
            knife_offset=0.0,  # No offset for pen plotting
            origin_top_left=True,
            mirror_y=True  # Mirror Y by default for correct orientation
        )

        gcode_tools = GCodeTools(params)
        temp_gcode_path = os.path.join(temp_dir, 'output.gcode')
        gcode = gcode_tools.svg_to_gcode(svg_file_path, temp_gcode_path)

        # Read the generated G-code
        with open(temp_gcode_path, 'r') as f:
            gcode_content = f.read()

        # Count lines
        line_count = len([line for line in gcode_content.split('\n') if line.strip() and not line.strip().startswith(';')])

        return jsonify({
            'success': True,
            'gcode': gcode_content,
            'line_count': line_count,
            'original_filename': filename
        })

    except Exception as e:
        print(f"Error in convert_to_gcode: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

    finally:
        # Clean up temporary files
        try:
            if temp_input_path and os.path.exists(temp_input_path):
                os.remove(temp_input_path)
            if temp_svg_path and os.path.exists(temp_svg_path):
                os.remove(temp_svg_path)
            if temp_gcode_path and os.path.exists(temp_gcode_path):
                os.remove(temp_gcode_path)
            if os.path.exists(temp_dir):
                # Remove any remaining files in temp dir
                for f in os.listdir(temp_dir):
                    try:
                        os.remove(os.path.join(temp_dir, f))
                    except:
                        pass
                os.rmdir(temp_dir)
        except Exception as e:
            print(f"Error cleaning up temporary files: {e}")


@app.route('/api/camera/start', methods=['POST'])
def start_camera():
    """Start camera streaming."""
    if not state['printer_connected']:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 400

    success = start_camera_stream()

    return jsonify({
        'success': success,
        'streaming': state['camera_streaming'],
        'message': 'Camera started' if success else 'Failed to start camera'
    })


@app.route('/api/camera/stop', methods=['POST'])
def stop_camera():
    """Stop camera streaming."""
    stop_camera_stream()

    return jsonify({
        'success': True,
        'streaming': state['camera_streaming'],
        'message': 'Camera stopped'
    })


@app.route('/api/camera/status', methods=['GET'])
def camera_status():
    """Get camera status."""
    camera_alive = False
    if printer and state['printer_connected']:
        try:
            camera_alive = printer.camera_client_alive()
        except Exception as e:
            print(f"Error checking camera status: {e}")

    return jsonify({
        'streaming': state['camera_streaming'],
        'camera_alive': camera_alive,
        'connected': state['printer_connected']
    })


@app.route('/api/camera/frame', methods=['GET'])
def camera_frame():
    """Return the latest cached camera frame for the web UI."""
    if not state['printer_connected']:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 400

    with camera_frame_lock:
        frame = latest_camera_frame
        frame_time = latest_camera_frame_time

    if frame:
        return jsonify({
            'success': True,
            'frame': frame,
            'age': time.time() - frame_time if frame_time else None,
            'streaming': state['camera_streaming']
        })

    camera_error = None
    if printer:
        try:
            frame = process_camera_frame(printer.get_camera_frame())
            remember_camera_frame(frame)
            return jsonify({
                'success': True,
                'frame': frame,
                'age': 0,
                'streaming': state['camera_streaming']
            })
        except Exception as e:
            camera_error = str(e)

    status_code = 202 if state['camera_streaming'] else 404
    return jsonify({
        'success': False,
        'status': 'warming_up' if state['camera_streaming'] else 'no_frame',
        'error': camera_error,
        'streaming': state['camera_streaming']
    }), status_code


# WebSocket handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    print('Client connected')
    emit('connection_response', {'status': 'connected'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('Client disconnected')


@socketio.on('request_camera_frame')
def handle_frame_request():
    """Handle request for single camera frame."""
    if printer and state['printer_connected']:
        try:
            frame = printer.get_camera_frame()
            if frame:
                processed_frame = process_camera_frame(frame)
                remember_camera_frame(processed_frame)
                emit('camera_frame', {'frame': processed_frame})
        except Exception as e:
            print(f"Error getting camera frame: {e}")


def start_server(host='0.0.0.0', port=5425, debug=False):
    """Start the Bambu Cuts web server."""
    print("Starting Bambu Cuts - Cutter and Plotter API...")
    print(f"Printer IP: {config._config_data.get('ip', 'Not configured')}")
    print(f"Server will run at: http://{host}:{port}")
    print()

    # Auto-connect on startup
    print("Attempting to connect to printer on startup...")
    connect_printer()

    try:
        socketio.run(app, host=host, port=port, debug=debug, use_reloader=False, allow_unsafe_werkzeug=True)
    finally:
        # Clean up
        stop_camera_stream()
        disconnect_printer()


if __name__ == '__main__':
    start_server(debug=True)
