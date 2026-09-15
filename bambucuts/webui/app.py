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
import statistics
import uuid
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
    },
    # History of direct-send jobs keyed by their UUID, most recent last.
    'direct_jobs': {}
}

# How many finished direct jobs to keep queryable by UUID.
DIRECT_JOB_HISTORY_LIMIT = 50

# Printer instance
printer = None

# The printer reports status every couple of seconds, so a feed with nothing
# newer than this is dead even if the cached 'connected' flag says otherwise.
MQTT_STALE_SECONDS = 30.0

# How long to wait before rebuilding a dropped MQTT status connection.
MQTT_RECONNECT_DELAY = 5.0

# A direct job is considered stalled once it has waited longer than its
# estimated run time (from feed rates) times this factor, but never less than
# the floor, so tiny jobs get time for the MQTT checkpoint to arrive.
DIRECT_JOB_TIMEOUT_FACTOR = 1.5
DIRECT_JOB_MIN_TIMEOUT_SECONDS = 30.0

# While a direct job is active, ask the printer for a full status report this
# often so the done marker is seen promptly instead of waiting for the
# printer's own report cadence, which is several seconds. The printer answers
# at most once per second whatever the request rate; a sweep from 1 to 20 Hz
# on an A1 showed 2 Hz as good as anything and 10 Hz or more slightly worse.
DIRECT_JOB_STATUS_POLL_INTERVAL = 0.5

# Camera streaming control
camera_thread = None
camera_stop_event = threading.Event()
camera_frame_lock = threading.Lock()
latest_camera_frame = None
latest_camera_frame_time = None

# MQTT status monitor control
mqtt_status_thread = None
# Updated in place by the listener: 'pushall_count', 'last_pushall_at'.
mqtt_pushall_stats = {}
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

    progress_keys = ('mc_percent', 'mc_remaining_time', 'layer_num', 'total_layer_num', 'gcode_state')

    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        changes = {
            key: (cached_print.get(key), print_status[key])
            for key in progress_keys
            if key in print_status and cached_print.get(key) != print_status[key]
        }
        cached_print.update(print_status)
        state['mqtt_status'] = {
            'connected': True,
            'error': None,
            'last_update': time.time(),
            'print': cached_print,
            'last_report_meta': state['mqtt_status'].get('last_report_meta'),
        }

    if changes:
        summary = ', '.join(f"{key}: {old} -> {new}" for key, (old, new) in changes.items())
        print(f"Printer progress changed: {summary}")

    # Resolve the active direct job here, not only when a client polls
    # /api/status, so completion is recorded even with no browser open.
    _, mqtt_progress = current_mqtt_progress()
    update_direct_job_from_mqtt(mqtt_progress)


# Keep this many reports per job for the benchmark / job endpoints.
DIRECT_JOB_REPORT_LOG_LIMIT = 400


def _record_job_report(print_status, meta):
    """Append one MQTT report to the active job's timeline and log it.

    Only runs while a direct job is active, so idle traffic stays quiet. The
    line shows when the report arrived relative to queueing, whether it was
    the answer to one of our pushalls (and its round trip) or an unsolicited
    delta from the printer, and what progress it carried. That is the data
    that tells whether latency sits in the printer executing M73 or in how
    the report reaches us.
    """
    with direct_job_lock:
        job = state.get('direct_job') or {}
        if not job.get('active'):
            return
        queued_at = job.get('queued_at') or job.get('started_at') or time.time()
        entry = {
            't': round(time.time() - queued_at, 3),
            'kind': 'pushall' if meta.get('pushall_matched') else 'delta',
            'rtt': round(meta['pushall_rtt'], 3) if meta.get('pushall_rtt') is not None else None,
            'since_pushall': round(meta['since_last_pushall'], 3) if meta.get('since_last_pushall') is not None else None,
            'keys': len(print_status),
            'mc_percent': print_status.get('mc_percent'),
            'mc_remaining_time': print_status.get('mc_remaining_time'),
            'gcode_state': print_status.get('gcode_state'),
            'marker_field': job.get('marker_field'),
            'marker_seen': print_status.get(job.get('marker_field') or 'mc_percent'),
        }
        if len(print_status) < 10:
            entry['payload'] = {k: v for k, v in print_status.items() if k != 'sequence_id'}
        reports = job.setdefault('reports', [])
        reports.append(entry)
        if len(reports) > DIRECT_JOB_REPORT_LOG_LIMIT:
            del reports[:len(reports) - DIRECT_JOB_REPORT_LOG_LIMIT]
    rtt = f"rtt {entry['rtt']:.3f}s" if entry['rtt'] is not None else f"since pushall {entry['since_pushall']}s"
    extra = f"  {entry['payload']}" if entry.get('payload') else ''
    print(f"MQTT report +{entry['t']:7.3f}s {entry['kind']:7s} {rtt}  keys={entry['keys']:3d}  "
          f"{entry['marker_field']}={entry['marker_seen']} mc_percent={entry['mc_percent']} "
          f"R={entry['mc_remaining_time']} state={entry['gcode_state']}{extra}")


# Fields whose changes the 3MF benchmark records while its watch is active.
WATCH_FIELDS = ('gcode_state', 'mc_percent', 'mc_remaining_time', 'mc_print_line_number',
                'mc_print_stage', 'mc_print_sub_stage', 'stg_cur', 'print_type', 'layer_num',
                'print_error', 'gcode_file')
mqtt_watch_lock = threading.Lock()


def start_mqtt_watch():
    """Begin recording timestamped changes of WATCH_FIELDS from every report."""
    with mqtt_watch_lock:
        state['mqtt_watch'] = {'started_at': time.time(), 'last': {}, 'events': []}


def stop_mqtt_watch():
    with mqtt_watch_lock:
        watch = state.pop('mqtt_watch', None)
    return watch


def _record_watch_events(print_status):
    with mqtt_watch_lock:
        watch = state.get('mqtt_watch')
        if not watch:
            return
        now = time.time()
        last = watch['last']
        for field in WATCH_FIELDS:
            if field not in print_status:
                continue
            value = print_status[field]
            if field in last and last[field] == value:
                continue
            old = last.get(field)
            watch['events'].append({'t': now, 'field': field, 'old': old, 'new': value})
            last[field] = value
            print(f"MQTT watch +{now - watch['started_at']:7.3f}s {field}: {old!r} -> {value!r}")


def _handle_mqtt_status_message(message):
    payload = message.get('json')
    if not isinstance(payload, dict):
        return

    print_status = payload.get('print')
    if not isinstance(print_status, dict):
        return

    _record_watch_events(print_status)
    meta = message.get('meta') or {}
    with mqtt_status_lock:
        state['mqtt_status']['last_report_meta'] = dict(meta)
    _record_job_report(print_status, meta)
    _merge_mqtt_print_status(print_status)


def _record_mqtt_feed_down(error):
    """Mark the cached MQTT status as disconnected, keeping the last print data."""
    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        state['mqtt_status'] = {
            'connected': False,
            'error': error,
            'last_update': state['mqtt_status'].get('last_update'),
            'print': cached_print
        }
    if error:
        print(f"MQTT status feed down: {error}")


def mqtt_feed_health(last_update):
    """Report whether the MQTT status feed is actually live.

    The cached 'connected' flag only records that a message once arrived, so a
    listener that died silently keeps looking healthy. Treat a feed with no
    recent message as down regardless of the flag.
    """
    age = (time.time() - last_update) if last_update else None
    listener_alive = bool(mqtt_status_thread and mqtt_status_thread.is_alive())
    stale = age is None or age > MQTT_STALE_SECONDS

    if stale:
        if age is None:
            reason = 'No MQTT status received yet'
        else:
            reason = f'No MQTT status for {age:.0f}s'
        if not listener_alive:
            reason += ' (listener not running)'
    else:
        reason = None

    return {
        'age': age,
        'stale': stale,
        'listener_alive': listener_alive,
        'reason': reason,
    }


def _active_job_pushall_interval():
    """Pushall cadence for the MQTT listener: fast while a direct job is active, else none."""
    with direct_job_lock:
        active = bool((state.get('direct_job') or {}).get('active'))
    active = active or bool(state.get('mqtt_watch'))
    return state.get('pushall_interval', DIRECT_JOB_STATUS_POLL_INTERVAL) if active else None


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
                    pushall_interval=_active_job_pushall_interval,
                    stats=mqtt_pushall_stats,
                )
                # Returned without error: either we were asked to stop, or the
                # session ended quietly. Either way, reconnect below.
                _record_mqtt_feed_down(None if mqtt_status_stop_event.is_set() else 'MQTT session ended')
            except MqttDumpError as e:
                _record_mqtt_feed_down(str(e))
            except Exception as e:  # never let the listener thread die silently
                _record_mqtt_feed_down(f'MQTT status listener error: {e}')

            if mqtt_status_stop_event.wait(MQTT_RECONNECT_DELAY):
                break
            print("Reconnecting MQTT status listener...")

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
        # gcode_check=False: the library's validator rejects bare axis flags
        # (e.g. "G28 X"), so skip it and let the printer do the validating.
        printer.gcode(gcode, gcode_check=False)
        print(f"G-code sent to printer: {gcode}")
        return True
    except Exception as e:
        print(f"Failed to send G-code to printer: {e}")
        state['connection_error'] = str(e)
        return False


def send_gcode_lines_to_printer(lines: list) -> bool:
    """Send several G-code lines to the printer in a single MQTT gcode_line command."""
    if not state['printer_connected'] or not printer:
        print(f"Cannot send G-code - printer not connected: {len(lines)} lines")
        return False

    if not lines:
        return True

    try:
        # The library joins the list with newlines into one gcode_line payload.
        # gcode_check=False for the same reason as the single-line send.
        printer.gcode(list(lines), gcode_check=False)
        print(f"G-code batch sent to printer: {len(lines)} lines")
        return True
    except Exception as e:
        print(f"Failed to send G-code batch to printer: {e}")
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


def current_mqtt_marker_pair():
    """Return the currently cached (M73 P, M73 R) pair."""
    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        return (
            cached_print.get('mc_percent'),
            cached_print.get('mc_remaining_time'),
        )


def _gcode_words(code: str):
    """Split an executable G-code line into (letter, value) words."""
    words = []
    for part in code.split():
        letter = part[0].upper()
        try:
            value = float(part[1:])
        except ValueError:
            continue
        words.append((letter, value))
    return words


def estimate_gcode_seconds(gcode_text: str, default_feed_mm_min: float = 1000.0,
                           start_position=None) -> float:
    """Estimate how long the printer needs to execute the G-code.

    Sums linear move distance divided by the modal feed rate, plus G4 dwells.
    Acceleration is ignored, so short segments run slower than estimated; the
    caller pads the result. Tracks G90/G91 and G92 so distances are right for
    both absolute and relative files. `start_position` seeds the head
    position as {'x': .., 'y': .., 'z': ..}; axes still unknown contribute
    nothing until the first move sets them.
    """
    seconds = 0.0
    feed_mm_min = max(1.0, float(default_feed_mm_min or 1000.0))
    absolute = True
    position = {'X': None, 'Y': None, 'Z': None}
    for axis, value in (start_position or {}).items():
        axis = str(axis).upper()
        if axis in position and value is not None:
            try:
                position[axis] = float(value)
            except (TypeError, ValueError):
                pass

    for _, code in extract_executable_gcode(gcode_text):
        upper = code.upper()
        cmd = upper.split()[0]

        if cmd == 'G90':
            absolute = True
            continue
        if cmd == 'G91':
            absolute = False
            continue
        if cmd == 'G92':
            for letter, value in _gcode_words(upper)[1:]:
                if letter in position:
                    position[letter] = value
            continue
        if cmd == 'G4':
            for letter, value in _gcode_words(upper)[1:]:
                if letter == 'P':
                    seconds += value / 1000.0
                elif letter == 'S':
                    seconds += value
            continue
        if cmd == 'G28':
            for axis in position:
                position[axis] = 0.0
            continue
        if cmd not in ('G0', 'G1'):
            continue

        squared = 0.0
        for letter, value in _gcode_words(upper)[1:]:
            if letter == 'F':
                if value > 0:
                    feed_mm_min = value
                continue
            if letter not in position:
                continue
            if absolute:
                if position[letter] is not None:
                    squared += (value - position[letter]) ** 2
                position[letter] = value
            else:
                squared += value ** 2
                if position[letter] is not None:
                    position[letter] += value
        if squared:
            seconds += math.sqrt(squared) / (feed_mm_min / 60.0)

    return seconds


def direct_job_timeout_seconds(estimated_seconds: float) -> float:
    """Stall timeout for a direct job: padded estimate, never below the floor."""
    return max(DIRECT_JOB_MIN_TIMEOUT_SECONDS, estimated_seconds * DIRECT_JOB_TIMEOUT_FACTOR)


# Done-marker strategy: a G-code line the printer echoes back as a field in
# its MQTT status report. Only the progress marker is used:
#   m73: M73 P<n> R0 -> mc_percent == n (and mc_remaining_time 0)
MARKER_STRATEGIES = ('m73',)
DEFAULT_MARKER_STRATEGY = 'm73'
MARKER_CLEANUP_GCODE = {}


def completion_marker_percent(baseline_pair=None) -> int:
    """Pick an M73 percent that differs from the printer's current mc_percent."""
    baseline_percent = (baseline_pair or (None, None))[0]
    try:
        return (int(baseline_percent) + 1) % 100
    except (TypeError, ValueError):
        return 1


def build_completion_marker(strategy: str, cached_print: dict):
    """Return (gcode_line, report_field, expected_value, marker_percent) for a strategy.

    The expected value is chosen to differ from what the printer currently
    reports, so the change is unambiguous.
    """
    percent = completion_marker_percent((cached_print.get('mc_percent'), cached_print.get('mc_remaining_time')))
    return f"M73 P{percent} R0", 'mc_percent', str(percent), percent


def add_completion_marker(gcode_text: str, baseline_pair=None, default_feed_mm_min: float = 1000.0,
                          start_position=None, strategy: str = DEFAULT_MARKER_STRATEGY):
    """Append one M400 + done-marker line after the last executable line.

    The M400 drains queued motion, so the printer echoing the marker over
    MQTT means every move before it has finished. A single marker keeps the
    motion continuous; per-batch markers would force a full stop at each one.
    """
    total_commands = sum(1 for _ in extract_executable_gcode(gcode_text))
    if total_commands == 0:
        return gcode_text, {
            'enabled': False,
            'command_count': 0,
            'marker_strategy': strategy,
            'marker_field': None,
            'marker_value': None,
            'marker_percent': None,
            'estimated_seconds': 0.0,
            'timeout_seconds': 0.0,
        }

    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
    if baseline_pair is not None:
        cached_print['mc_percent'], cached_print['mc_remaining_time'] = baseline_pair
    marker_line, marker_field, marker_value, marker_percent = build_completion_marker(strategy, cached_print)
    estimated_seconds = estimate_gcode_seconds(gcode_text, default_feed_mm_min, start_position)
    marker_lines = [
        f"; bambucuts done marker: {total_commands} command(s)",
        "M400 ; wait for queued motion to finish",
        marker_line,
    ]
    return '\n'.join([gcode_text.rstrip('\n'), *marker_lines]), {
        'enabled': True,
        'command_count': total_commands,
        'marker_strategy': strategy,
        'marker_line': marker_line,
        'marker_field': marker_field,
        'marker_value': marker_value,
        'marker_percent': marker_percent,
        'estimated_seconds': round(estimated_seconds, 1),
        'timeout_seconds': round(direct_job_timeout_seconds(estimated_seconds), 1),
    }


def _store_direct_job(job):
    """Store a job in the UUID-keyed history, trimming old entries. Caller holds direct_job_lock."""
    jobs = state.setdefault('direct_jobs', {})
    jobs[job['id']] = job
    while len(jobs) > DIRECT_JOB_HISTORY_LIMIT:
        del jobs[next(iter(jobs))]


def begin_direct_job(progress_info):
    """Start tracking a direct-send job by its final M73 checkpoint."""
    started_at = time.time()
    job = {
        'id': str(uuid.uuid4()),
        'active': True,
        'status': 'queueing',
        'started_at': started_at,
        'completed_at': None,
        'queued_at': None,
        'queued_count': 0,
        'command_count': progress_info.get('command_count', 0),
        'marker_percent': progress_info.get('marker_percent'),
        'marker_strategy': progress_info.get('marker_strategy', DEFAULT_MARKER_STRATEGY),
        'marker_line': progress_info.get('marker_line'),
        'marker_field': progress_info.get('marker_field', 'mc_percent'),
        'marker_value': progress_info.get('marker_value'),
        'estimated_seconds': progress_info.get('estimated_seconds', 0.0),
        'timeout_seconds': progress_info.get('timeout_seconds', DIRECT_JOB_MIN_TIMEOUT_SECONDS),
        'last_percent': None,
        'last_remaining_time': None,
        'last_update': None,
        'message': 'Queueing G-code and waiting for the final M73 checkpoint',
    }

    print(f"Direct job {job['id']} started: {job['command_count']} command(s), "
          f"marker '{job['marker_line']}' expecting {job['marker_field']}={job['marker_value']}, "
          f"estimated {job['estimated_seconds']}s, timeout {job['timeout_seconds']}s")

    with mqtt_status_lock:
        cached_print = dict(state['mqtt_status'].get('print') or {})
        cached_print[job['marker_field']] = None
        if job['marker_field'] == 'mc_percent':
            cached_print['mc_remaining_time'] = None
            cached_print['layer_num'] = None
        state['mqtt_status']['print'] = cached_print

    with direct_job_lock:
        previous = dict(state.get('direct_job') or {})
        if previous.get('active') and previous.get('id'):
            previous['active'] = False
            previous['status'] = 'superseded'
            previous['message'] = 'Superseded by a newer direct job'
            _store_direct_job(previous)
        state['direct_job'] = job
        _store_direct_job(job)

    return job.copy()


def mark_direct_job_queued(job_id, queued_count, errors):
    """Record whether direct G-code was queued successfully."""
    with direct_job_lock:
        job = dict(state.get('direct_job') or {})
        if job.get('id') != job_id:
            return job

        job['queued_at'] = time.time()
        job['queued_count'] = queued_count
        job['pushalls_at_queue'] = mqtt_pushall_stats.get('pushall_count', 0)
        if errors:
            job['active'] = False
            job['status'] = 'queue_error'
            job['message'] = 'Failed while queueing direct G-code'
            job['errors'] = errors
        else:
            job['status'] = 'waiting'
            job['message'] = 'Queued; waiting for printer to reach the final M73 checkpoint'

        state['direct_job'] = job
        _store_direct_job(job)
        return job.copy()


def _apply_stall_timeout(job, mqtt_progress):
    """Fail a queued job that waited longer than its estimated run time allows.

    Without this a job whose checkpoint never arrives - a dead MQTT feed, a
    cancelled print - stays 'active' forever. Caller holds direct_job_lock.
    """
    queued_at = job.get('queued_at')
    if not job.get('active') or not queued_at:
        return job

    waited = time.time() - queued_at
    timeout = job.get('timeout_seconds') or DIRECT_JOB_MIN_TIMEOUT_SECONDS
    if waited < timeout:
        return job

    reason = mqtt_progress.get('stale_reason') or f'no checkpoint after {waited:.0f}s (timeout {timeout:.0f}s)'

    job['active'] = False
    job['status'] = 'stalled'
    job['completed_at'] = time.time()
    job['message'] = f'Stopped waiting for the final checkpoint: {reason}'
    print(f"Direct job {job.get('id')} stalled: {job['message']}")

    state['direct_job'] = job
    _store_direct_job(job)
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
        field = job.get('marker_field') or 'mc_percent'
        reported = (mqtt_progress.get('print') or {}).get(field)
        if mqtt_progress.get('stale'):
            return _apply_stall_timeout(job, mqtt_progress)
        if progress_update is None or progress_update < job.get('started_at', 0) or reported is None:
            return _apply_stall_timeout(job, mqtt_progress)

        if field == 'mc_percent':
            # Our marker always sends R0, so a nonzero remaining time is not ours.
            if remaining_time is not None and remaining_time != 0:
                return _apply_stall_timeout(job, mqtt_progress)
        if str(reported) != str(job.get('marker_value')):
            return _apply_stall_timeout(job, mqtt_progress)

        meta = mqtt_progress.get('report_meta') or {}
        detect_seconds = round(progress_update - job['queued_at'], 3) if job.get('queued_at') else None
        job['last_percent'] = percent
        job['last_remaining_time'] = remaining_time
        job['last_update'] = progress_update
        job['active'] = False
        job['status'] = 'complete'
        job['completed_at'] = progress_update
        job['detect_seconds'] = detect_seconds
        job['detect_report_kind'] = 'pushall' if meta.get('pushall_matched') else 'delta'
        job['detect_pushall_rtt'] = round(meta['pushall_rtt'], 3) if meta.get('pushall_rtt') is not None else None
        job['detect_since_pushall'] = round(meta['since_last_pushall'], 3) if meta.get('since_last_pushall') is not None else None
        job['pushalls_sent'] = max(0, meta.get('pushall_count', 0) - job.get('pushalls_at_queue', 0))
        job['message'] = 'Direct G-code execution reached the final done marker'
        print(f"Final checkpoint reached: {field}={reported} at +{detect_seconds}s after queue, "
              f"via {job['detect_report_kind']} (rtt {job['detect_pushall_rtt']}s, "
              f"since last pushall {job['detect_since_pushall']}s), "
              f"{job['pushalls_sent']} pushalls sent during job. Done.")

        state['direct_job'] = job
        _store_direct_job(job)
        return job.copy()


def current_mqtt_progress():
    """Snapshot the cached MQTT print status as the progress dict used for job tracking."""
    with mqtt_status_lock:
        mqtt_status = {
            'connected': state['mqtt_status'].get('connected'),
            'error': state['mqtt_status'].get('error'),
            'last_update': state['mqtt_status'].get('last_update'),
            'print': dict(state['mqtt_status'].get('print') or {}),
        }
        report_meta = dict(state['mqtt_status'].get('last_report_meta') or {})

    mqtt_print = mqtt_status['print']
    last_update = mqtt_status.get('last_update')
    health = mqtt_feed_health(last_update)
    # A stale feed is a dead feed, whatever the cached flag says.
    mqtt_live = bool(mqtt_status.get('connected')) and not health['stale']
    mqtt_status['connected'] = mqtt_live
    mqtt_status['stale'] = health['stale']
    if health['stale'] and not mqtt_status.get('error'):
        mqtt_status['error'] = health['reason']
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
        'age': health['age'],
        'stale': health['stale'],
        'stale_reason': health['reason'],
        'listener_alive': health['listener_alive'],
        'mqtt_connected': mqtt_live,
        'mqtt_error': mqtt_status.get('error'),
        'report_meta': report_meta,
        'print': mqtt_print,
    }
    return mqtt_status, mqtt_progress


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

    mqtt_status, mqtt_progress = current_mqtt_progress()
    direct_job = update_direct_job_from_mqtt(mqtt_progress)
    # The per-report timeline can be hundreds of entries; keep the 1 Hz status
    # poll light and leave the full record to /api/gcode/jobs/<id>.
    direct_job = {k: v for k, v in direct_job.items() if k != 'reports'}

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


@app.route('/api/set-xy-zero', methods=['POST'])
def set_xy_zero():
    """Set current X and Y position as zero."""
    state['position']['x'] = 0.0
    state['position']['y'] = 0.0

    gcode = "G92 X0 Y0"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/set-xyze-zero', methods=['POST'])
def set_xyze_zero():
    """Set current X, Y, Z and E positions as zero."""
    state['position']['x'] = 0.0
    state['position']['y'] = 0.0
    state['position']['z'] = 0.0
    state['position']['e'] = 0.0

    gcode = "G92 X0 Y0 Z0 E0"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

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


@app.route('/api/mqtt-pushall', methods=['POST'])
def mqtt_pushall():
    """Ask the printer for an immediate full status push (throttle callers:
    Bambu rate-limits pushall)."""
    if not state['printer_connected'] or not printer:
        return jsonify({'success': False, 'error': 'printer not connected'}), 400
    try:
        ok = printer.mqtt_client.pushall()
        return jsonify({'success': bool(ok)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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

    # Track position from EVERY line, G90/G91-aware (multi-line safe).
    # The absolute/relative mode persists across calls in state, matching
    # the printer's own modal state.
    for raw in gcode.split('\n'):
        cmd = raw.split(';')[0].strip().upper()
        if not cmd:
            continue
        if cmd == 'G90':
            state['gcode_absolute'] = True
            continue
        if cmd == 'G91':
            state['gcode_absolute'] = False
            continue
        if cmd.startswith('G28'):
            # homing defines the printer origin for the homed axes
            axes = cmd.split()[1:]
            if not axes or any(a.startswith('X') or a.startswith('Y') for a in axes):
                state['position']['x'] = 0.0
                state['position']['y'] = 0.0
            if not axes or any(a.startswith('Z') for a in axes):
                state['position']['z'] = 0.0
            continue
        if cmd.startswith('G92'):
            for part in cmd.split()[1:]:
                ax = part[0].lower()
                if ax in 'xyze':
                    try:
                        state['position'][ax] = float(part[1:] or 0.0)
                    except ValueError:
                        pass
            continue
        if cmd.startswith(('G0 ', 'G1 ')) or cmd in ('G0', 'G1'):
            absolute = state.get('gcode_absolute', True)
            for part in cmd.split()[1:]:
                ax = part[0].lower()
                if ax not in 'xyz':   # E is relative (M83) on this rig
                    continue
                try:
                    val = float(part[1:])
                except ValueError:
                    continue
                if absolute:
                    state['position'][ax] = val
                else:
                    state['position'][ax] += val

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


def queue_direct_gcode(gcode_text: str, single_call: bool = False, progress_markers: bool = True,
                       start_position=None, marker_strategy: str = DEFAULT_MARKER_STRATEGY):
    """Queue G-code on the printer as a tracked direct job.

    Returns the same dict the send-all endpoint reports. When progress
    markers are on and the printer is connected, the returned 'direct_job'
    can be followed to completion with wait_for_direct_job(). The run-time
    estimate starts from `start_position`, defaulting to the jog-tracked
    position so the first move's travel is counted.
    """
    if start_position is None:
        start_position = dict(state['position'])
    progress_info = {
        'enabled': False,
        'command_count': sum(1 for _ in extract_executable_gcode(gcode_text)),
        'marker_percent': None,
        'estimated_seconds': round(estimate_gcode_seconds(gcode_text, state['feed_rate'], start_position), 1),
        'timeout_seconds': 0.0,
    }
    gcode_to_send = gcode_text
    if progress_markers:
        gcode_to_send, progress_info = add_completion_marker(
            gcode_text,
            default_feed_mm_min=state['feed_rate'],
            start_position=start_position,
            strategy=marker_strategy,
        )

    sent_count = 0
    queued_count = 0
    errors = []
    direct_job = None
    if state['printer_connected'] and progress_info.get('enabled'):
        direct_job = begin_direct_job(progress_info)

    executable_lines = list(extract_executable_gcode(gcode_to_send))
    payload_bytes = 0

    if single_call:
        # Ship every line in a single MQTT gcode_line command instead of one
        # publish per line.
        lines = [line for _, line in executable_lines]
        for line in lines:
            add_to_history(line)

        queued_count = len(lines)
        sent_count = sum(
            1 for line in lines if not line.upper().startswith(('M73', 'M400')))
        payload_bytes = len('\n'.join(lines).encode('utf-8'))

        if state['printer_connected']:
            if not send_gcode_lines_to_printer(lines):
                errors.append(f"Failed to send {queued_count} lines in one call")
    else:
        for line_num, line in executable_lines:
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

    return {
        'success': len(errors) == 0,
        'sent_count': sent_count,
        'queued_count': queued_count,
        'single_call': single_call,
        'payload_bytes': payload_bytes,
        'progress': progress_info,
        'direct_job': direct_job,
        'job_id': direct_job['id'] if direct_job else None,
        'errors': errors
    }


def wait_for_direct_job(job_id: str, poll_seconds: float = 0.02, stop_event=None):
    """Block until the direct job leaves the active state, then return it.

    The MQTT callback resolves completion on its own; this loop also re-runs
    the stall check so a dead feed still ends the wait.
    """
    while True:
        with direct_job_lock:
            job = dict(state.get('direct_jobs', {}).get(job_id) or {})
        if not job:
            return None
        if not job.get('active'):
            return job
        if stop_event is not None and stop_event.is_set():
            return job
        _, mqtt_progress = current_mqtt_progress()
        update_direct_job_from_mqtt(mqtt_progress)
        time.sleep(poll_seconds)


@app.route('/api/gcode/send-all', methods=['POST'])
def send_all_gcode():
    """Send all G-code lines from editor."""
    data = request.json
    gcode_text = data.get('gcode', '')
    progress_markers = data.get('progress_markers', True)
    single_call = bool(data.get('single_call', False))

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to send'}), 400

    return jsonify(queue_direct_gcode(gcode_text, single_call=single_call, progress_markers=progress_markers))


# ---------------------------------------------------------------------------
# Done-signal latency benchmark
# ---------------------------------------------------------------------------

BENCHMARK_SETUP_XY = (10.0, 10.0)
BENCHMARK_TARGET_XY = (110.0, 110.0)

benchmark_lock = threading.Lock()
benchmark_thread = None
benchmark_stop_event = threading.Event()


def _summarize(values):
    values = [v for v in values if v is not None]
    if not values:
        return {'count': 0}
    summary = {
        'count': len(values),
        'min': round(min(values), 3),
        'max': round(max(values), 3),
        'mean': round(statistics.fmean(values), 3),
        'median': round(statistics.median(values), 3),
    }
    summary['stdev'] = round(statistics.stdev(values), 3) if len(values) > 1 else 0.0
    return summary


def build_benchmark_gcode(line_count: int, feed_mm_min: float, distance_mm: float = None) -> str:
    """Move from the setup point to the target in `line_count` equal steps.

    distance_mm overrides the per-axis travel; 0 gives a zero-motion batch
    that measures pure command-processing and reporting latency.
    """
    (x0, y0), (x1, y1) = BENCHMARK_SETUP_XY, BENCHMARK_TARGET_XY
    if distance_mm is not None:
        x1, y1 = x0 + distance_mm, y0 + distance_mm
    line_count = max(1, int(line_count))
    lines = []
    for step in range(1, line_count + 1):
        frac = step / line_count
        x = x0 + (x1 - x0) * frac
        y = y0 + (y1 - y0) * frac
        feed = f" F{feed_mm_min:.0f}" if step == 1 else ""
        lines.append(f"G1 X{x:.3f} Y{y:.3f}{feed}")
    return '\n'.join(lines)


def _benchmark_one(gcode_text: str, single_call: bool, stop_event, start_position=None,
                   marker_strategy: str = DEFAULT_MARKER_STRATEGY):
    """Queue one batch, wait for its done signal, and time each phase."""
    t_send = time.perf_counter()
    result = queue_direct_gcode(gcode_text, single_call=single_call, start_position=start_position,
                                marker_strategy=marker_strategy)
    t_queued = time.perf_counter()
    if not result['success'] or not result['job_id']:
        return {
            'ok': False,
            'error': '; '.join(result['errors']) or 'no direct job created (printer connected?)',
            'queue_seconds': round(t_queued - t_send, 3),
        }
    job = wait_for_direct_job(result['job_id'], stop_event=stop_event)
    t_done = time.perf_counter()
    ok = bool(job) and job.get('status') == 'complete'
    job = job or {}
    return {
        'ok': ok,
        'status': job.get('status', 'missing'),
        'error': None if ok else job.get('message'),
        'queue_seconds': round(t_queued - t_send, 3),
        'wait_seconds': round(t_done - t_queued, 3),
        'total_seconds': round(t_done - t_send, 3),
        'estimated_motion_seconds': result['progress'].get('estimated_seconds'),
        'detect_seconds': job.get('detect_seconds'),
        'detect_report_kind': job.get('detect_report_kind'),
        'detect_pushall_rtt': job.get('detect_pushall_rtt'),
        'detect_since_pushall': job.get('detect_since_pushall'),
        'pushalls_sent': job.get('pushalls_sent'),
        'reports': list(job.get('reports') or []),
    }


def run_direct_latency_benchmark(iterations: int = 10, line_counts=(1, 10), feed_mm_min=None,
                                 single_call: bool = True, stop_event=None, distance_mm=None,
                                 marker_strategy: str = DEFAULT_MARKER_STRATEGY, pushall_interval=None):
    """Measure how long the done signal takes after queueing a direct batch.

    Each iteration first parks the head at BENCHMARK_SETUP_XY as its own job,
    then times a fresh job that moves to BENCHMARK_TARGET_XY in `line_count`
    lines. 'overhead' is wait time minus the estimated motion time, which is
    roughly the round trip through the printer's queue, M400, M73, and MQTT.
    """
    stop_event = stop_event or threading.Event()
    feed_mm_min = float(feed_mm_min or state['feed_rate'])
    iterations = max(1, int(iterations))
    x0, y0 = BENCHMARK_SETUP_XY
    setup_gcode = f"G90\nG1 X{x0:.3f} Y{y0:.3f} F{feed_mm_min:.0f}"

    report = {
        'status': 'running',
        'started_at': time.time(),
        'finished_at': None,
        'iterations': iterations,
        'feed_mm_min': feed_mm_min,
        'single_call': single_call,
        'setup_xy': BENCHMARK_SETUP_XY,
        'target_xy': BENCHMARK_TARGET_XY if distance_mm is None else (x0 + distance_mm, y0 + distance_mm),
        'distance_mm': distance_mm,
        'marker_strategy': marker_strategy,
        'pushall_interval': pushall_interval if pushall_interval is not None else DIRECT_JOB_STATUS_POLL_INTERVAL,
        'cases': [],
        'error': None,
    }
    with benchmark_lock:
        state['benchmark'] = dict(report)
    previous_interval = state.get('pushall_interval')
    if pushall_interval is not None:
        state['pushall_interval'] = pushall_interval

    def publish():
        with benchmark_lock:
            state['benchmark'] = json.loads(json.dumps(report))

    try:
        for line_count in line_counts:
            batch_gcode = build_benchmark_gcode(line_count, feed_mm_min, distance_mm)
            case = {'line_count': int(line_count), 'gcode': batch_gcode, 'runs': [], 'stats': {}}
            report['cases'].append(case)
            for run_index in range(1, iterations + 1):
                if stop_event.is_set():
                    raise RuntimeError('benchmark stopped')
                setup = _benchmark_one(setup_gcode, single_call, stop_event, marker_strategy=marker_strategy)
                if not setup['ok']:
                    raise RuntimeError(f"setup move failed on run {run_index}: {setup.get('error')}")
                run = _benchmark_one(batch_gcode, single_call, stop_event,
                                     start_position={'x': x0, 'y': y0}, marker_strategy=marker_strategy)
                run['run'] = run_index
                if run['ok']:
                    run['overhead_seconds'] = round(
                        run['wait_seconds'] - (run['estimated_motion_seconds'] or 0.0), 3)
                case['runs'].append(run)
                print(f"Benchmark {line_count}-line run {run_index}/{iterations}: "
                      f"queue {run['queue_seconds']}s, wait {run.get('wait_seconds')}s, "
                      f"detected via {run.get('detect_report_kind')} rtt {run.get('detect_pushall_rtt')}s, "
                      f"{run.get('pushalls_sent')} pushalls, status {run.get('status')}")
                publish()
                if not run['ok']:
                    raise RuntimeError(f"measured batch failed on run {run_index}: {run.get('error')}")

            ok_runs = [r for r in case['runs'] if r['ok']]
            case['stats'] = {
                'queue_seconds': _summarize([r['queue_seconds'] for r in ok_runs]),
                'wait_seconds': _summarize([r['wait_seconds'] for r in ok_runs]),
                'total_seconds': _summarize([r['total_seconds'] for r in ok_runs]),
                'overhead_seconds': _summarize([r['overhead_seconds'] for r in ok_runs]),
                'pushall_rtt': _summarize([r['detect_pushall_rtt'] for r in ok_runs]),
                'pushalls_sent': _summarize([r['pushalls_sent'] for r in ok_runs]),
                'detected_via': {
                    kind: sum(1 for r in ok_runs if r.get('detect_report_kind') == kind)
                    for kind in ('pushall', 'delta')
                },
                'estimated_motion_seconds': ok_runs[0]['estimated_motion_seconds'] if ok_runs else None,
            }
            print(f"Benchmark {line_count}-line stats: {case['stats']}")
        report['status'] = 'complete'
    except Exception as e:
        report['status'] = 'failed'
        report['error'] = str(e)
        print(f"Benchmark failed: {e}")
    finally:
        if pushall_interval is not None:
            if previous_interval is None:
                state.pop('pushall_interval', None)
            else:
                state['pushall_interval'] = previous_interval
        cleanup = MARKER_CLEANUP_GCODE.get(marker_strategy)
        if cleanup and state['printer_connected']:
            send_gcode_to_printer(cleanup)
            print(f"Benchmark cleanup sent: {cleanup}")
        report['finished_at'] = time.time()
        publish()
    return report


@app.route('/api/gcode/benchmark', methods=['POST'])
def start_direct_latency_benchmark():
    """Start the done-signal latency benchmark in the background.

    Body (all optional): iterations (default 10), line_counts (default [1, 10]),
    feed_rate in mm/min (default: current jog feed rate), single_call (default true).
    Poll GET /api/gcode/benchmark for progress and results.
    """
    global benchmark_thread

    if not state['printer_connected']:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 400
    if benchmark_thread and benchmark_thread.is_alive():
        return jsonify({'success': False, 'error': 'Benchmark already running'}), 409

    data = request.json or {}
    iterations = _bounded_body_int(data, 'iterations', 10, 1, 100)
    try:
        line_counts = [max(1, min(500, int(v))) for v in data.get('line_counts', [1, 10])]
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'line_counts must be a list of integers'}), 400
    if not line_counts:
        return jsonify({'success': False, 'error': 'line_counts must not be empty'}), 400
    try:
        feed_mm_min = float(data.get('feed_rate') or state['feed_rate'])
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'feed_rate must be a number'}), 400
    single_call = bool(data.get('single_call', True))
    distance_mm = data.get('distance_mm')
    if distance_mm is not None:
        try:
            distance_mm = max(0.0, min(200.0, float(distance_mm)))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'distance_mm must be a number'}), 400

    pushall_interval = data.get('pushall_interval')
    if pushall_interval is not None:
        try:
            pushall_interval = max(0.02, min(10.0, float(pushall_interval)))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'pushall_interval must be seconds'}), 400
    marker_strategy = str(data.get('marker', DEFAULT_MARKER_STRATEGY))
    if marker_strategy not in MARKER_STRATEGIES:
        return jsonify({'success': False, 'error': f'marker must be one of {list(MARKER_STRATEGIES)}'}), 400

    benchmark_stop_event.clear()
    benchmark_thread = threading.Thread(
        target=run_direct_latency_benchmark,
        kwargs={
            'iterations': iterations,
            'line_counts': line_counts,
            'feed_mm_min': feed_mm_min,
            'single_call': single_call,
            'stop_event': benchmark_stop_event,
            'distance_mm': distance_mm,
            'marker_strategy': marker_strategy,
            'pushall_interval': pushall_interval,
        },
        daemon=True,
    )
    benchmark_thread.start()
    return jsonify({
        'success': True,
        'message': f'Benchmark started: {iterations} iteration(s) for line counts {line_counts}',
        'iterations': iterations,
        'line_counts': line_counts,
        'feed_rate': feed_mm_min,
        'single_call': single_call,
    })


@app.route('/api/gcode/benchmark', methods=['GET'])
def get_direct_latency_benchmark():
    """Return the latest benchmark report, including per-run timings and stats."""
    with benchmark_lock:
        report = state.get('benchmark')
    if not report:
        return jsonify({'success': False, 'error': 'No benchmark has been run'}), 404
    return jsonify({'success': True, 'benchmark': report})


@app.route('/api/gcode/benchmark/stop', methods=['POST'])
def stop_direct_latency_benchmark():
    """Ask a running benchmark to stop after the current wait."""
    benchmark_stop_event.set()
    return jsonify({'success': True})


def _bounded_body_int(data, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(data.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# ---------------------------------------------------------------------------
# 3MF print latency benchmark
# ---------------------------------------------------------------------------

BENCHMARK_3MF_FIRST_MARKER = 37
BENCHMARK_3MF_DONE_MARKER = 73
BENCHMARK_3MF_TIMEOUT = 180.0

benchmark_3mf_thread = None


def build_benchmark_3mf_gcode(feed_mm_min: float) -> str:
    """Marker before the first move, park, diagonal, then M400 + done marker."""
    (x0, y0), (x1, y1) = BENCHMARK_SETUP_XY, BENCHMARK_TARGET_XY
    return '\n'.join([
        "G90",
        f"M73 P{BENCHMARK_3MF_FIRST_MARKER} R0 ; reached just before the first move",
        f"G1 X{x0:.3f} Y{y0:.3f} F{feed_mm_min:.0f}",
        f"G1 X{x1:.3f} Y{y1:.3f} F{feed_mm_min:.0f}",
        "M400 ; wait for queued motion to finish",
        f"M73 P{BENCHMARK_3MF_DONE_MARKER} R0 ; all plot motion done",
    ])


def _wait_for_watch(predicate, timeout: float, stop_event):
    """Poll the watch's events until predicate(events) returns a truthy value or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline and not stop_event.is_set():
        with mqtt_watch_lock:
            events = list((state.get('mqtt_watch') or {}).get('events', []))
        found = predicate(events)
        if found:
            return found
        time.sleep(0.02)
    return None


def run_3mf_latency_benchmark(iterations: int = 3, feed_mm_min=None, stop_event=None):
    """Time a 3MF print from start command to first move and to end of motion.

    Each iteration packages the benchmark G-code into the template 3MF,
    uploads it, sends the start command, and then reads timestamps off the
    MQTT watch: gcode_state RUNNING, the marker placed just before the
    first move, the M400-gated done marker, and FINISH after the template's
    end-of-print jingle. All times are seconds after the start command.
    """
    stop_event = stop_event or threading.Event()
    feed_mm_min = float(feed_mm_min or state['feed_rate'])
    iterations = max(1, int(iterations))
    gcode_text = build_benchmark_3mf_gcode(feed_mm_min)
    template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'template.3mf')
    estimated = estimate_gcode_seconds(gcode_text, feed_mm_min, {'x': BENCHMARK_TARGET_XY[0], 'y': BENCHMARK_TARGET_XY[1]})

    report = {
        'status': 'running', 'started_at': time.time(), 'finished_at': None,
        'iterations': iterations, 'feed_mm_min': feed_mm_min, 'gcode': gcode_text,
        'estimated_motion_seconds': round(estimated, 1), 'runs': [], 'stats': {}, 'error': None,
    }

    def publish():
        with benchmark_lock:
            state['benchmark_3mf'] = json.loads(json.dumps(report))
    publish()

    def first_time(events, field, value=None):
        # Skip the initial snapshot (old is None): it is the state left over
        # from before the start command, e.g. FINISH from the previous run.
        for e in events:
            if e['old'] is None:
                continue
            if e['field'] == field and (value is None or str(e['new']) == str(value)):
                return e['t']
        return None

    try:
        for run_index in range(1, iterations + 1):
            if stop_event.is_set():
                raise RuntimeError('benchmark stopped')
            temp_dir = tempfile.mkdtemp()
            gcode_path = os.path.join(temp_dir, 'bench.gcode')
            out_name = f"bench_{run_index}.3mf"
            out_path = os.path.join(temp_dir, out_name)
            run = {'run': run_index}
            try:
                with open(gcode_path, 'w') as f:
                    f.write(gcode_text)
                t0 = time.perf_counter()
                process_3mf(template_path, out_path, gcode_path, verbose=False)
                t1 = time.perf_counter()
                run['package_seconds'] = round(t1 - t0, 3)
                run['file_bytes'] = os.path.getsize(out_path)
                with open(out_path, 'rb') as f:
                    result = printer.upload_file(f, out_name)
                t2 = time.perf_counter()
                run['upload_seconds'] = round(t2 - t1, 3)
                if '226' not in result:
                    raise RuntimeError(f'upload failed: {result}')

                start_mqtt_watch()
                printer.start_print(out_name, 1)
                t_start = time.time()
                run['start_command_seconds'] = round(time.perf_counter() - t2, 3)
                print(f"3MF benchmark run {run_index}: started {out_name}, waiting for FINISH")

                def finished(events):
                    return first_time(events, 'gcode_state', 'FINISH') or first_time(events, 'gcode_state', 'FAILED')
                finish_t = _wait_for_watch(finished, BENCHMARK_3MF_TIMEOUT, stop_event)
                watch = stop_mqtt_watch() or {'events': []}
                events = watch['events']
                rel = lambda t: round(t - t_start, 3) if t else None
                run['events'] = [{'t': rel(e['t']), 'field': e['field'], 'old': e['old'], 'new': e['new']}
                                 for e in events if e['t'] >= t_start - 0.5]
                run['t_running'] = rel(first_time(events, 'gcode_state', 'RUNNING'))
                run['t_prepare'] = rel(first_time(events, 'gcode_state', 'PREPARE'))
                run['t_first_move_marker'] = rel(first_time(events, 'mc_percent', BENCHMARK_3MF_FIRST_MARKER))
                run['t_done_marker'] = rel(first_time(events, 'mc_percent', BENCHMARK_3MF_DONE_MARKER))
                run['t_finish'] = rel(finish_t)
                run['t_first_line_number'] = rel(next((e['t'] for e in events
                                                       if e['field'] == 'mc_print_line_number' and str(e['new']) not in ('0', '')), None))
                run['ok'] = run['t_done_marker'] is not None
                if run['t_done_marker'] is not None and run['t_first_move_marker'] is not None:
                    run['motion_window_seconds'] = round(run['t_done_marker'] - run['t_first_move_marker'], 3)
                print(f"3MF benchmark run {run_index}: running {run['t_running']}s, first move {run['t_first_move_marker']}s, "
                      f"done {run['t_done_marker']}s, finish {run['t_finish']}s")
            finally:
                for path in (gcode_path, out_path):
                    if os.path.exists(path):
                        os.remove(path)
                if os.path.exists(temp_dir):
                    os.rmdir(temp_dir)
            report['runs'].append(run)
            publish()
            if not run['ok']:
                raise RuntimeError(f"run {run_index} never reported the done marker")
            # Let the printer settle back before the next start command.
            time.sleep(3.0)

        ok = [r for r in report['runs'] if r.get('ok')]
        report['stats'] = {
            key: _summarize([r.get(key) for r in ok])
            for key in ('upload_seconds', 'start_command_seconds', 't_running', 't_first_move_marker',
                        't_done_marker', 't_finish', 'motion_window_seconds')
        }
        report['status'] = 'complete'
    except Exception as e:
        stop_mqtt_watch()
        report['status'] = 'failed'
        report['error'] = str(e)
        print(f"3MF benchmark failed: {e}")
    finally:
        report['finished_at'] = time.time()
        publish()
    return report


@app.route('/api/gcode/benchmark-3mf', methods=['POST'])
def start_3mf_latency_benchmark():
    """Start the 3MF print latency benchmark in the background.

    Body (optional): iterations (default 3), feed_rate in mm/min.
    Poll GET /api/gcode/benchmark-3mf for progress and results.
    """
    global benchmark_3mf_thread
    if not state['printer_connected']:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 400
    if benchmark_3mf_thread and benchmark_3mf_thread.is_alive():
        return jsonify({'success': False, 'error': 'Benchmark already running'}), 409
    data = request.json or {}
    iterations = _bounded_body_int(data, 'iterations', 3, 1, 20)
    try:
        feed_mm_min = float(data.get('feed_rate') or state['feed_rate'])
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'feed_rate must be a number'}), 400
    benchmark_stop_event.clear()
    benchmark_3mf_thread = threading.Thread(
        target=run_3mf_latency_benchmark,
        kwargs={'iterations': iterations, 'feed_mm_min': feed_mm_min, 'stop_event': benchmark_stop_event},
        daemon=True,
    )
    benchmark_3mf_thread.start()
    return jsonify({'success': True, 'iterations': iterations, 'feed_rate': feed_mm_min})


@app.route('/api/gcode/benchmark-3mf', methods=['GET'])
def get_3mf_latency_benchmark():
    with benchmark_lock:
        report = state.get('benchmark_3mf')
    if not report:
        return jsonify({'success': False, 'error': 'No 3MF benchmark has been run'}), 404
    return jsonify({'success': True, 'benchmark': report})


@app.route('/api/gcode/jobs', methods=['GET'])
def list_direct_jobs():
    """List recent direct-send jobs with their completion status."""
    with direct_job_lock:
        jobs = [dict(job) for job in state.get('direct_jobs', {}).values()]
    return jsonify({'jobs': jobs})


@app.route('/api/gcode/jobs/<job_id>', methods=['GET'])
def get_direct_job(job_id):
    """Get one direct-send job by its UUID."""
    with direct_job_lock:
        job = state.get('direct_jobs', {}).get(job_id)
        job = dict(job) if job else None
    if job is None:
        return jsonify({'success': False, 'error': 'Unknown job id'}), 404
    return jsonify({'success': True, 'job': job})


@app.route('/api/gcode/send-all-3mf', methods=['POST'])
def send_all_gcode_3mf():
    """Send G-code by converting to 3MF and uploading to printer."""
    data = request.json
    gcode_text = data.get('gcode', '')
    filename = data.get('filename', 'plot.gcode')
    progress_markers = data.get('progress_markers', True)

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
            'command_count': sum(1 for _ in extract_executable_gcode(gcode_text)),
            'marker_percent': None,
            'estimated_seconds': round(estimate_gcode_seconds(gcode_text, state['feed_rate']), 1),
            'timeout_seconds': 0.0,
        }
        gcode_to_package = gcode_text
        if progress_markers:
            gcode_to_package, progress_info = add_completion_marker(
                gcode_text,
                baseline_pair=current_mqtt_marker_pair(),
                default_feed_mm_min=state['feed_rate'],
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

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to convert'}), 400

    # Create temporary directory for files
    temp_dir = tempfile.mkdtemp()
    temp_gcode_path = None
    temp_3mf_path = None

    try:
        gcode_to_package = gcode_text
        if progress_markers:
            gcode_to_package, _ = add_completion_marker(
                gcode_text,
                baseline_pair=current_mqtt_marker_pair(),
                default_feed_mm_min=state['feed_rate'],
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
