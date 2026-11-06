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
import re
import time
import sys
import os
import tempfile
from pathlib import Path
import threading
import base64
import math
import statistics
import itertools
import collections
import uuid
from io import BytesIO

try:
    import bambulabs_api as bl
    from bambucuts import config
    from bambucuts.compress_3mf import process_3mf
    from bambucuts.gcodetools import GCodeTools, CuttingParameters
    from bambucuts.dxf2svg import convert_dxf_to_svg
    from bambucuts.head_tracker import HeadTracker, MotionState, simulate as simulate_gcode
    from bambucuts.marker_link import MarkerLink
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

# ---------------------------------------------------------------------------
# Traffic log: every API call, every publish to the printer, every marker
# from the board and every job state change, one JSON line each, so gaps
# between batches can be measured after the fact. `t` is wall time.
# Status polls are logged too but without bodies, since there are many.
TRAFFIC_QUIET_PATHS = ('/api/status', '/api/mqtt-pushall')
traffic_lock = threading.Lock()


def traffic_log_path():
    if os.environ.get('BAMBUCUTS_TRAFFIC_LOG'):
        return Path(os.environ['BAMBUCUTS_TRAFFIC_LOG'])
    profile = config.get_profile()
    return config.CONFIG_DIR / f".bambucuts{'.' + profile if profile else ''}.traffic.log"


def traffic(kind, **fields):
    entry = {'t': round(time.time(), 4), 'kind': kind, **fields}
    try:
        line = json.dumps(entry, default=str)
        with traffic_lock, open(traffic_log_path(), 'a') as f:
            f.write(line + '\n')
    except Exception as e:
        print(f"Cannot write traffic log: {e}")


@app.before_request
def _traffic_request():
    request.environ['bambucuts.t0'] = time.time()
    if request.path.startswith('/static') or request.path in TRAFFIC_QUIET_PATHS:
        return
    body = request.get_json(silent=True) if request.method == 'POST' else None
    traffic('api_request', method=request.method, path=request.path,
            query=request.query_string.decode() or None, body=body)


@app.after_request
def _traffic_response(response):
    if request.path.startswith('/static'):
        return response
    t0 = request.environ.get('bambucuts.t0') or time.time()
    fields = {'method': request.method, 'path': request.path, 'status': response.status_code,
              'seconds': round(time.time() - t0, 4)}
    if request.path not in TRAFFIC_QUIET_PATHS and response.is_json:
        data = response.get_json(silent=True)
        if isinstance(data, dict):
            job = data.get('direct_job') or {}
            fields.update({k: data.get(k) for k in ('success', 'job_id', 'error', 'waited_seconds', 'completed')
                           if data.get(k) is not None})
            if job:
                fields['job_status'] = job.get('status')
    traffic('api_response', **fields)
    return response

# Global state
state = {
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

# Where the head is, from the G-code we have sent. See bambucuts.head_tracker.
head = HeadTracker(feed=state['feed_rate'], absolute=True)

# The printer reports status every couple of seconds, so a feed with nothing
# newer than this is dead even if the cached 'connected' flag says otherwise.
MQTT_STALE_SECONDS = 30.0

# How long to wait before rebuilding a dropped MQTT status connection.
MQTT_RECONNECT_DELAY = 5.0

# A direct job is considered stalled once it has waited longer than its
# estimated run time (from feed rates) times this factor plus the pad. The
# estimate ignores acceleration, and slow dispensing batches really take
# about 1.5x it, so the factor leaves room above that. A caller can pass
# its own timeout_seconds instead. The pad also gives tiny jobs time for
# the MQTT checkpoint to arrive.
DIRECT_JOB_TIMEOUT_FACTOR = 2.0
DIRECT_JOB_TIMEOUT_PAD_SECONDS = 30.0
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

# Completion markers from the laser board over USB serial. Set up by
# start_marker_link(); None when the side channel is off or unavailable.
marker_link = None

# MQTT status monitor control
mqtt_status_thread = None
# Updated in place by the listener: 'pushall_count', 'last_pushall_at'.
mqtt_pushall_stats = {}
mqtt_status_stop_event = threading.Event()
mqtt_status_lock = threading.Lock()
direct_job_lock = threading.Lock()


def _handle_marker(address, value, at):
    """Called from the marker reader thread for every marker the board sends.

    Kept to one call on purpose: this runs on the reader thread, which must
    get back to the port promptly, and work here delays the next marker.
    Values below the marker range are real laser and servo commands, which
    the board echoes so a tool batch can be confirmed.
    """
    traffic('board_marker', address=address, value=value, at=at)
    note_board_mark(address, value, at)
    if value >= SIDE_MARKER_MIN:
        update_direct_job_from_marker(value, at, address=address)


def _handle_marker_lost(reason):
    """The port closed or a sequence gap showed a marker was lost.

    Nothing to do: every batch also carries its MQTT marker, which completes
    it when the serial one does not arrive.
    """
    traffic('marker_lost', reason=reason)
    print(f"Laser board markers: {reason}; batches in flight complete on their MQTT marker")


def start_marker_link():
    """Start reading completion markers from the laser board.

    Independent of the printer connection: the board is a local USB device
    and is worth watching even with the printer offline, since that is how
    you tell whether the side channel is wired up at all.
    """
    global marker_link

    cfg = config.get_config()
    if not cfg.get('marker_enabled', True):
        print("Marker side channel disabled in config; jobs will complete on MQTT only")
        return False

    if marker_link is None:
        marker_link = MarkerLink(
            device=cfg.get('marker_device') or None,
            on_marker=_handle_marker,
            on_lost=_handle_marker_lost,
        )
    return marker_link.start()


def stop_marker_link():
    if marker_link is not None:
        marker_link.stop()


def marker_link_status():
    """Side channel status for the status endpoint, trimmed for a 1 Hz poll.

    Reports bus health separately from port health: a board that is plugged
    in and talking but seeing no AMS-Lite frames looks identical to "no
    markers arrived" from the host's side, and silently degrades to the MQTT
    fallback. Saying so is the difference between a slow job and a puzzle.
    """
    if marker_link is None:
        return {'enabled': False, 'connected': False, 'device': None,
                'error': 'Marker side channel not started', 'marker_count': 0}
    status = marker_link.status()
    status.pop('log', None)
    if status.get('connected') and status.get('bus_alive') is False:
        status['error'] = status.get('bus_error') or status.get('error')
    return status


def connect_printer():
    """Connect to the BambuLab printer."""
    global printer, state

    try:
        # Get current config values
        cfg = config.get_config()
        print(f"Connecting to printer at {cfg['ip']} [{config.profile_label()}]...")
        printer = bl.Printer(cfg['ip'], cfg['access_code'], cfg['serial'])
        printer.connect()
        time.sleep(2)  # Allow time for connection to establish
        state['printer_connected'] = True
        state['connection_error'] = None
        print("Successfully connected to printer")

        # Nothing is sent about positioning mode here: connecting is not a user
        # action, and G90 is both the firmware's resting state and what this app
        # leaves behind on disconnect, which is what the tracker starts from.
        print(f"Assuming absolute positioning (G90), "
              f"{'position unknown until you home or zero' if head.uncertain else 'position known'}")

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
            # Leave the printer in absolute mode, which is what the next
            # connect assumes without sending anything. Only a relative jog
            # can have left it in G91, so this is usually a no-op.
            set_absolute_mode()
            printer.disconnect()
            print("Disconnected from printer")
        head.reset()
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
        traffic('mqtt_change', summary=summary)
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
        active = _active_direct_jobs()
        if not active:
            return
        job = active[-1]
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
        active = bool(_active_direct_jobs())
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


def set_positioning_mode(absolute: bool) -> bool:
    """Put the printer in G90/G91, skipping the send when it is already there.

    The head tracker carries the modal state across sends, so the mode command
    only goes out when the queued G-code would leave the printer in the other
    mode, and never without the user asking for a move. Returns True when a
    command was actually sent, which is also the only case worth waiting on.
    """
    gcode = "G90" if absolute else "G91"
    if head.target_state().absolute is absolute:
        return False

    head.apply(gcode)
    add_to_history(gcode)
    if not state['printer_connected'] or not printer:
        return False

    # Through the send queue like everything else, so it lands in order
    # behind anything still waiting to go out.
    ran, success = send_queue.submit(lambda: send_gcode_to_printer(gcode))
    return success if ran else True


def set_relative_mode() -> bool:
    """Set printer to relative positioning mode."""
    return set_positioning_mode(False)


def set_absolute_mode() -> bool:
    """Set printer to absolute positioning mode."""
    return set_positioning_mode(True)


# The printer stalls on a gcode_line message bigger than about 1 KB: it reads
# the first part and leaves the rest until the next packet arrives on that
# connection, which can take a minute. Measured with dwell-only payloads:
# up to 980 bytes of JSON always ran at once, 1000 bytes and up waited 8-58 s,
# and a second publish 0.2 s behind a 1100-byte one got it acknowledged
# 0.23 s after sending. So after any message near that size a pushall goes
# out on the same connection; it touches nothing in the G-code queue.
NUDGE_PAYLOAD_BYTES = 900


def _nudge_if_large(param: str):
    """Follow a large gcode_line message with a pushall so the printer reads it now."""
    size = len(json.dumps({"print": {"sequence_id": "0", "command": "gcode_line", "param": param}}))
    if size < NUDGE_PAYLOAD_BYTES:
        return
    try:
        printer.mqtt_client.pushall()
        traffic('printer_nudge', payload_bytes=size)
    except Exception as e:
        print(f"Could not follow a {size}-byte G-code message with a pushall: {e}")


def send_gcode_to_printer(gcode: str) -> bool:
    """Send G-code command to the printer."""
    if not state['printer_connected'] or not printer:
        print(f"Cannot send G-code - printer not connected: {gcode}")
        return False

    try:
        # gcode_check=False: the library's validator rejects bare axis flags
        # (e.g. "G28 X"), so skip it and let the printer do the validating.
        t0 = time.time()
        printer.gcode(gcode, gcode_check=False)
        _nudge_if_large(gcode)
        traffic('printer_send', lines=gcode.split('\n'), publish_seconds=round(time.time() - t0, 4))
        print(f"G-code sent to printer: {gcode}")
        return True
    except Exception as e:
        traffic('printer_send_failed', lines=gcode.split('\n'), error=str(e))
        print(f"Failed to send G-code to printer: {e}")
        state['connection_error'] = str(e)
        return False


class SendQueue:
    """Everything sent to the printer goes out in the order it was submitted.

    A job with tool commands sends in pieces, holding at each one until the
    laser board confirms it, which can take as long as the job's motion. A
    jog or another job submitted meanwhile must not slip into one of those
    gaps, so it waits here instead, and the caller is not kept waiting.

    When nothing is ahead, a submission runs on the caller's thread and its
    result comes straight back, exactly as a direct send would. Otherwise it
    is queued for the worker thread, and submit() returns at once.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._pending = collections.deque()
        self._busy = False
        self._worker = None

    def submit(self, fn, inline=True):
        """Run or queue fn. Returns (ran, result); result is None when queued."""
        with self._cond:
            if inline and not self._busy and not self._pending:
                self._busy = True
            else:
                self._pending.append(fn)
                if len(self._pending) > 1 or self._busy:
                    print(f"Send queued behind {len(self._pending) - 1 + self._busy} earlier send(s)")
                if self._worker is None:
                    self._worker = threading.Thread(target=self._run, daemon=True, name='printer-send')
                    self._worker.start()
                return False, None
        try:
            return True, fn()
        finally:
            with self._cond:
                self._busy = False
                self._cond.notify_all()

    def _run(self):
        while True:
            with self._cond:
                while self._busy:
                    self._cond.wait()
                if not self._pending:
                    self._worker = None
                    return
                fn = self._pending.popleft()
                self._busy = True
            try:
                fn()
            except Exception as e:
                print(f"Queued send failed: {e}")
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

    def clear(self):
        """Drop everything not yet started. Returns how many were dropped."""
        with self._cond:
            dropped = len(self._pending)
            self._pending.clear()
            return dropped

    def depth(self):
        with self._cond:
            return len(self._pending) + self._busy


send_queue = SendQueue()


def send_tracked(gcode: str) -> bool:
    """Record G-code in the head tracker, then send it if the printer is connected.

    Used by the jog, homing, zeroing and manual-command paths. Direct jobs
    are tracked as a unit by queue_direct_gcode instead. Goes through the
    send queue; True when queued behind earlier sends.
    """
    head.apply(gcode)
    if not state['printer_connected']:
        return True
    ran, success = send_queue.submit(lambda: send_gcode_to_printer(gcode))
    return success if ran else True


def send_gcode_lines_to_printer(lines: list) -> bool:
    """Send several G-code lines to the printer in a single MQTT gcode_line command.

    Batch size is the caller's choice; see NUDGE_PAYLOAD_BYTES for what
    keeps a big one from stalling.
    """
    if not state['printer_connected'] or not printer:
        print(f"Cannot send G-code - printer not connected: {len(lines)} lines")
        return False

    if not lines:
        return True

    try:
        # The library joins the list with newlines into one gcode_line payload.
        # gcode_check=False for the same reason as the single-line send.
        t0 = time.time()
        printer.gcode(list(lines), gcode_check=False)
        _nudge_if_large('\n'.join(lines))
        traffic('printer_send', lines=list(lines), publish_seconds=round(time.time() - t0, 4))
        print(f"G-code batch sent to printer: {len(lines)} lines")
        return True
    except Exception as e:
        traffic('printer_send_failed', lines=list(lines), error=str(e))
        print(f"Failed to send G-code batch to printer: {e}")
        state['connection_error'] = str(e)
        return False


def add_to_history(gcode: str):
    """Add G-code command to history."""
    state['gcode_history'].append(gcode)
    # Keep only last 20 commands
    if len(state['gcode_history']) > 20:
        state['gcode_history'].pop(0)


# ---------------------------------------------------------------------------
# Tool control: standard G-code in, laser board protocol out.
#
# CAM tools emit M3/M4/M5 for a laser and M280 for a servo; the laser board
# only understands M620/M621 on the AMS-Lite bus. Translating here means
# LightBurn, Kiri:Moto and anything else can emit ordinary G-code and stay
# unaware of this rig, rather than every post-processor needing a custom
# profile.
#
#   M3 S<power> / M4 S<power>  ->  M400, M620 P<v> M621 P<v>
#   M5                         ->  M400, M620 P0 M621 P0
#   M280 P<index> S<angle>     ->  M400, M620 R<angle> M621 R<angle>
#
# The M400 is required: the printer runs M620 as soon as it parses it, not
# when the moves ahead of it finish (measured: a marker behind a 3 s move
# reached the board after 0.2 s without M400, 3.2 s with it). Without the
# sync, M3 ... moves ... M5 would switch the tool on and off before the
# moves ran. The head stops briefly at every switch as a result.
#
# S is scaled from the tool's range (LASER_S_MAX, 1000 in LightBurn and
# GRBL by default) onto the board's 0-200. An S already within 0-200 with
# LASER_S_MAX left at 200 passes through unchanged.
LASER_MAX_POWER = 200
SERVO_MAX_ANGLE = 180
LASER_S_MAX_DEFAULT = 1000
TOOL_SYNC = 'M400'

_WORD = {}


def _gcode_word(line: str, letter: str):
    """Value of a G-code word such as S or P, or None when absent."""
    pattern = _WORD.get(letter)
    if pattern is None:
        pattern = _WORD[letter] = re.compile(rf'(?:^|\s){letter}(-?\d+(?:\.\d+)?)', re.I)
    match = pattern.search(line)
    return float(match.group(1)) if match else None


def _bus_command(channel: str, value: int) -> str:
    """One laser-board command. M621 mirrors M620, as the board's docs show."""
    return f"M620 {channel}{value} M621 {channel}{value}"


def translate_tool_gcode(gcode_text: str, s_max: float = None):
    """Rewrite M3/M4/M5 and M280 into the laser board's M620/M621 commands.

    Lines that are not tool commands pass through untouched, including any
    M620 the caller wrote itself. Returns (text, counts).
    """
    s_max = float(s_max or LASER_S_MAX_DEFAULT)
    counts = {'laser': 0, 'servo': 0}
    out = []
    for raw in gcode_text.split('\n'):
        code = strip_inline_comment(raw)
        head = code.split(' ', 1)[0].upper() if code else ''
        if head in ('M3', 'M03', 'M4', 'M04'):
            s = _gcode_word(code, 'S')
            # A bare M3 with no S means full power, as in GRBL.
            power = LASER_MAX_POWER if s is None else s / s_max * LASER_MAX_POWER
            power = max(0, min(LASER_MAX_POWER, int(round(power))))
            out += [TOOL_SYNC, f"{_bus_command('P', power)} ; {code}"]
            counts['laser'] += 1
        elif head in ('M5', 'M05'):
            out += [TOOL_SYNC, f"{_bus_command('P', 0)} ; {code}"]
            counts['laser'] += 1
        elif head == 'M280':
            angle = _gcode_word(code, 'S')
            if angle is None:
                out.append(raw)       # nothing to act on; leave it alone
                continue
            angle = max(0, min(SERVO_MAX_ANGLE, int(round(angle))))
            out += [TOOL_SYNC, f"{_bus_command('R', angle)} ; {code}"]
            counts['servo'] += 1
        else:
            out.append(raw)
    return '\n'.join(out), counts


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


def estimate_gcode_seconds(gcode_text: str, start: MotionState = None) -> float:
    """Seconds the printer needs for the G-code, starting from `start`.

    Defaults to where the queued G-code will leave the head, so a batch sent
    behind others is timed from the right place and in the right G90/G91 mode.
    """
    return simulate_gcode(gcode_text, start or head.target_state()).seconds


def direct_job_timeout_seconds(estimated_seconds: float) -> float:
    """Stall timeout for a direct job: padded estimate, never below the floor."""
    return max(DIRECT_JOB_MIN_TIMEOUT_SECONDS,
               estimated_seconds * DIRECT_JOB_TIMEOUT_FACTOR + DIRECT_JOB_TIMEOUT_PAD_SECONDS)


# Every tracked batch ends in a done marker, and a job completes when one
# comes back. Two markers are appended, and whichever arrives first wins.
#
# The MQTT marker is `M73 P<n> R0`: the printer echoes it as mc_percent in
# its status report, and mc_remaining_time 0 tells it apart from a real
# print's progress. It always works, and it is slow - the printer's snapshot
# picks up an executed M73 one to two seconds late.
#
# The side-channel marker is `M620 R<code> M621 R<code>`, which reaches the
# laser board over the AMS-Lite bus and comes back over USB in milliseconds
# (see bambucuts/marker_link.py). Codes run from SIDE_MARKER_MIN up, above
# the 0-200 the board reads as a real servo angle, so the board can tell
# them apart and leave its outputs alone.
#
# Every batch carries both: the side-channel marker (when the board is
# connected) and then the M73. The job completes on whichever arrives
# first, which is the side-channel one unless the bus lost it.
#
# The M400 before the markers (when used) drains queued motion first.
MARKER_FIELD = 'mc_percent'
SIDE_MARKER_MIN = 201
SIDE_MARKER_MAX = 251

# How many codes each batch sends. One, because redundancy here is not
# achievable: the printer coalesces a gcode_line payload and emits a single
# bus frame carrying only the final value of the channel, whatever is put in
# it. Measured over 30 trials per size, the marker that arrives is the LAST
# of the block every time (30/30 at size 2), repeated and sentinel-separated
# copies both yield a mean of 1.00 frames, and 1000 round-robin batches show
# no latency difference and no reliable loss difference between 1 and 5.
#
# Splitting a block across separate publishes does produce an occasional
# second frame, but costs ~100ms per publish and measured worse overall.
# Loss is covered by the M73 every batch also carries.
SIDE_MARKER_REPEATS = 1

_side_marker_counter = itertools.count()


def next_side_marker_block(count: int = None) -> list:
    """Next run of consecutive marker codes, cycling within the range.

    Blocks advance by their own width, so successive batches never share a
    code. A block only comes back around after the whole range, and the
    matcher also requires a marker to post-date the job it completes, so a
    stale code still on the wire cannot resolve a later job.
    """
    count = count or SIDE_MARKER_REPEATS
    span = SIDE_MARKER_MAX - SIDE_MARKER_MIN + 1 - (count - 1)
    start = SIDE_MARKER_MIN + (next(_side_marker_counter) * count) % span
    return list(range(start, start + count))


def side_channel_ready() -> bool:
    """Whether the laser board is readable right now.

    Checked per batch rather than once at startup: the board resets when its
    code.py is saved and can be unplugged mid-session. When it is away there
    is no point emitting the extra M620, so the batch carries only M73.
    """
    return bool(marker_link is not None and marker_link.status().get('connected'))


_marker_percent_lock = threading.Lock()
_last_marker_percent = None


def next_marker_percent() -> int:
    """Next M73 percent, from one counter cycling through 1-99.

    Every batch and sub-batch takes the next value, so no two markers in
    flight share one and none repeats what the printer last reported.
    The first value steps past the printer's current report.
    """
    global _last_marker_percent
    with _marker_percent_lock:
        last = _last_marker_percent
        if last is None:
            with mqtt_status_lock:
                current = (state['mqtt_status'].get('print') or {}).get(MARKER_FIELD)
            try:
                last = int(current)
            except (TypeError, ValueError):
                last = 0
        _last_marker_percent = last % 99 + 1
        return _last_marker_percent


def add_completion_marker(gcode_text: str, wait_for_motion: bool = True):
    """Append the done markers (with M400 first by default) after the last line.

    The M400 drains queued motion, so a marker coming back means every move
    before it has finished. With wait_for_motion off the markers fire when
    the parser reaches them, a few moves ahead of the pen, which keeps
    motion continuous when another batch is queued behind.

    Both markers go on every batch: the serial one (M620 R<code>, when the
    laser board is connected) and the MQTT one (M73). The job completes on
    whichever arrives first.
    """
    total_commands = sum(1 for _ in extract_executable_gcode(gcode_text))
    if total_commands == 0:
        return gcode_text, {
            'enabled': False,
            'command_count': 0,
            'marker_field': MARKER_FIELD,
            'marker_value': None,
            'side_marker_values': None,
            'side_marker_line': None,
            'marker_lines': [],
            'wait_for_motion': wait_for_motion,
            'estimated_seconds': 0.0,
            'timeout_seconds': 0.0,
        }

    side_codes = next_side_marker_block() if side_channel_ready() else []
    side_lines = [f"M620 R{c} M621 R{c}" for c in side_codes]
    percent = next_marker_percent()
    marker_line = f"M73 P{percent} R0"

    estimated_seconds = estimate_gcode_seconds(gcode_text)
    marker_lines = [f"; bambucuts done marker: {total_commands} command(s)"]
    if wait_for_motion:
        marker_lines.append("M400 ; wait for queued motion to finish")
    for index, line in enumerate(side_lines):
        marker_lines.append(f"{line} ; laser board done marker"
                            + ("" if index == 0 else " (retry)"))
    if marker_line:
        marker_lines.append(marker_line)
    return '\n'.join([gcode_text.rstrip('\n'), *marker_lines]), {
        'enabled': True,
        'command_count': total_commands,
        'marker_line': marker_line,
        'marker_field': MARKER_FIELD,
        'marker_value': str(percent),
        'side_marker_values': [str(c) for c in side_codes] or None,
        'side_marker_line': side_lines[0] if side_lines else None,
        # The executable marker lines, so the send path can tell them from
        # the user's own G-code without matching on command prefixes.
        'marker_lines': [c for c in (strip_inline_comment(l) for l in marker_lines) if c],
        'wait_for_motion': wait_for_motion,
        'estimated_seconds': round(estimated_seconds, 1),
        'timeout_seconds': round(direct_job_timeout_seconds(estimated_seconds), 1),
    }


def _active_direct_jobs():
    """Active jobs in queue order. Caller holds direct_job_lock."""
    jobs = state.setdefault('direct_jobs', {})
    ids = state.setdefault('active_direct_job_ids', [])
    ids[:] = [i for i in ids if i in jobs and jobs[i].get('active')]
    return [jobs[i] for i in ids]


def _finish_direct_job(job, status, message, completed_at=None):
    """Move a job out of the active list. Caller holds direct_job_lock."""
    traffic('job_finished', job_id=job['id'], status=status, message=message,
            source=job.get('detect_source'), detect_seconds=job.get('detect_seconds'),
            estimated_seconds=job.get('estimated_seconds'))
    job['active'] = False
    job['status'] = status
    job['completed_at'] = completed_at or time.time()
    job['message'] = message
    ids = state.setdefault('active_direct_job_ids', [])
    if job['id'] in ids:
        ids.remove(job['id'])
    if (state.get('direct_job') or {}).get('id') == job['id']:
        state['direct_job'] = job
    _store_direct_job(job)


def _store_direct_job(job):
    """Store a job in the UUID-keyed history, trimming old entries. Caller holds direct_job_lock."""
    jobs = state.setdefault('direct_jobs', {})
    jobs[job['id']] = job
    while len(jobs) > DIRECT_JOB_HISTORY_LIMIT:
        del jobs[next(iter(jobs))]


def begin_direct_job(progress_info):
    """Start tracking a direct-send job by its done marker."""
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
        'marker_line': progress_info.get('marker_line'),
        'marker_field': progress_info.get('marker_field', MARKER_FIELD),
        'marker_value': progress_info.get('marker_value'),
        'side_marker_values': progress_info.get('side_marker_values'),
        'side_marker_line': progress_info.get('side_marker_line'),
        'wait_for_motion': progress_info.get('wait_for_motion', True),
        'estimated_seconds': progress_info.get('estimated_seconds', 0.0),
        'timeout_seconds': progress_info.get('timeout_seconds', DIRECT_JOB_MIN_TIMEOUT_SECONDS),
        'last_percent': None,
        'last_remaining_time': None,
        'last_update': None,
        'message': 'Queueing G-code and waiting for the done marker',
    }

    if job['side_marker_values']:
        how = (f"side markers {'/'.join(job['side_marker_values'])}, "
               f"no MQTT marker sent")
    else:
        how = (f"marker '{job['marker_line']}' expecting "
               f"{job['marker_field']}={job['marker_value']}")
    traffic('job_started', job_id=job['id'], command_count=job['command_count'],
            side_markers=job['side_marker_values'], estimated_seconds=job['estimated_seconds'],
            timeout_seconds=job['timeout_seconds'])
    print(f"Direct job {job['id']} started: {job['command_count']} command(s), {how}, "
          f"estimated {job['estimated_seconds']}s, timeout {job['timeout_seconds']}s")

    with direct_job_lock:
        in_flight = _active_direct_jobs()
        job['queue_position'] = len(in_flight)
        state.setdefault('active_direct_job_ids', []).append(job['id'])
        state['direct_job'] = job
        _store_direct_job(job)

    if not in_flight:
        # Nothing else is waiting on the cached value, so drop it: the next
        # report must be a fresh one before it can complete this job.
        with mqtt_status_lock:
            cached_print = dict(state['mqtt_status'].get('print') or {})
            cached_print[job['marker_field']] = None
            if job['marker_field'] == 'mc_percent':
                cached_print['mc_remaining_time'] = None
                cached_print['layer_num'] = None
            state['mqtt_status']['print'] = cached_print

    return job.copy()


def mark_direct_job_queued(job_id, queued_count, errors):
    """Record whether direct G-code was queued successfully."""
    with direct_job_lock:
        job = state.get('direct_jobs', {}).get(job_id)
        if not job:
            return dict(state.get('direct_job') or {})

        job['queued_at'] = time.time()
        job['queued_count'] = queued_count
        job['pushalls_at_queue'] = mqtt_pushall_stats.get('pushall_count', 0)
        if errors:
            job['errors'] = errors
            _finish_direct_job(job, 'queue_error', 'Failed while queueing direct G-code')
            head.drop(job_id)
            return dict(job)

        # A job cannot start until the ones ahead of it are done, so its
        # deadline starts where the previous in-flight job's deadline ends.
        ahead = [j for j in _active_direct_jobs() if j['id'] != job_id]
        base = max([job['queued_at']] + [j.get('deadline') or 0 for j in ahead])
        job['deadline'] = base + (job.get('timeout_seconds') or DIRECT_JOB_MIN_TIMEOUT_SECONDS)
        job['status'] = 'waiting'
        job['message'] = 'Queued; waiting for the printer to reach the done marker'
        traffic('job_queued', job_id=job_id, queued_count=queued_count,
                estimated_seconds=job.get('estimated_seconds'), timeout_seconds=job.get('timeout_seconds'))
        _store_direct_job(job)
        return dict(job)


def _apply_stall_timeout(job, mqtt_progress):
    """Fail a queued job that waited longer than its estimated run time allows.

    Without this a job whose checkpoint never arrives - a dead MQTT feed, a
    cancelled print - stays 'active' forever. Caller holds direct_job_lock.
    """
    queued_at = job.get('queued_at')
    if not job.get('active') or not queued_at:
        return job

    now = time.time()
    deadline = job.get('deadline') or (queued_at + (job.get('timeout_seconds') or DIRECT_JOB_MIN_TIMEOUT_SECONDS))
    if now < deadline:
        return job

    waited = now - queued_at
    reason = mqtt_progress.get('stale_reason') or f'no checkpoint after {waited:.0f}s'
    _finish_direct_job(job, 'stalled', f'Stopped waiting for the final checkpoint: {reason}')
    head.drop(job['id'])
    print(f"Direct job {job.get('id')} stalled: {job['message']}")
    return dict(job)


def _stall_check_all(mqtt_progress):
    """Run the stall check over every in-flight job. Caller holds direct_job_lock."""
    for job in list(_active_direct_jobs()):
        _apply_stall_timeout(job, mqtt_progress)
    return dict(state.get('direct_job') or {})


def _job_expects(job, field, value):
    """Whether this job is waiting for `value` on `field`.

    The field holds a list for the side channel, where a batch sends several
    consecutive codes and any one of them arriving confirms it, and a single
    value for the MQTT marker.
    """
    expected = job.get(field)
    if expected is None:
        return False
    if isinstance(expected, (list, tuple)):
        return any(str(value) == str(e) for e in expected)
    return str(value) == str(expected)


def _find_marker_job(active, value, at, field):
    """Index of the newest in-flight job waiting for this marker, or None.

    The timestamp guard rejects a marker older than the job it would
    complete. That matters most on the side channel, where codes cycle
    through a small range and nothing else separates a stale marker still
    on the wire from the one this job is waiting for.

    Values are unique per job, so newest and first are the same thing; the
    caller completes everything up to the index returned.
    """
    return next((i for i in range(len(active) - 1, -1, -1)
                 if _job_expects(active[i], field, value)
                 and at >= active[i].get('started_at', 0)), None)


def _complete_matched_jobs(active, matched, at, source, detail, annotate=None):
    """Complete the matched job and everything queued ahead of it.

    A job cannot execute before the ones queued in front of it, so a marker
    for job N also proves jobs 0..N-1 ran. Those are completed as inferred,
    which is what keeps a batch resolvable when the MQTT feed skips its
    marker entirely at its ~1 Hz cadence. Caller holds direct_job_lock.
    """
    for index, job in enumerate(active[:matched + 1]):
        inferred = index < matched
        detect_seconds = round(at - job['queued_at'], 3) if job.get('queued_at') else None
        job['last_update'] = at
        job['detect_seconds'] = detect_seconds
        job['detect_inferred'] = inferred
        job['detect_source'] = source
        if annotate:
            annotate(job)
        _finish_direct_job(
            job, 'complete',
            'Completed (inferred from a later checkpoint)' if inferred
            else f'Direct G-code execution reached the final done marker ({source})',
            completed_at=at)
        head.commit(job['id'], at=at)
        print(f"Final checkpoint reached for job {job['id'][:8]}: {detail} at +{detect_seconds}s "
              f"after queue{' (inferred)' if inferred else ''}, via {source}. Done.")


def update_direct_job_from_mqtt(mqtt_progress):
    """Resolve in-flight direct jobs against the latest cached MQTT report."""
    with direct_job_lock:
        active = _active_direct_jobs()
        if not active:
            return dict(state.get('direct_job') or {})

        progress_update = mqtt_progress.get('last_update')
        percent = mqtt_progress.get('percent')
        remaining_time = mqtt_progress.get('remaining_time')
        field = active[0].get('marker_field') or 'mc_percent'
        reported = (mqtt_progress.get('print') or {}).get(field)
        if mqtt_progress.get('stale'):
            return _stall_check_all(mqtt_progress)
        if progress_update is None or reported is None:
            return _stall_check_all(mqtt_progress)
        if field == 'mc_percent' and remaining_time is not None and remaining_time != 0:
            # Our markers always send R0, so a nonzero remaining time is not ours.
            return _stall_check_all(mqtt_progress)

        matched = _find_marker_job(active, reported, progress_update, 'marker_value')
        if matched is None:
            return _stall_check_all(mqtt_progress)

        meta = mqtt_progress.get('report_meta') or {}

        def annotate(job):
            job['last_percent'] = percent
            job['last_remaining_time'] = remaining_time
            job['detect_report_kind'] = 'pushall' if meta.get('pushall_matched') else 'delta'
            job['detect_pushall_rtt'] = round(meta['pushall_rtt'], 3) if meta.get('pushall_rtt') is not None else None
            job['detect_since_pushall'] = round(meta['since_last_pushall'], 3) if meta.get('since_last_pushall') is not None else None
            job['pushalls_sent'] = max(0, meta.get('pushall_count', 0) - job.get('pushalls_at_queue', 0))

        _complete_matched_jobs(active, matched, progress_update, 'mqtt',
                               f"{field}={reported}", annotate=annotate)
        return dict(state.get('direct_job') or {})


def update_direct_job_from_marker(value, at, address=None):
    """Resolve in-flight direct jobs against a marker read off the laser board.

    This is the fast path: the board decodes the M620 within a millisecond
    of the printer executing it, against the one to two seconds the MQTT
    snapshot takes to pick up an M73. Nothing here is required for
    correctness - every job also carries an MQTT marker - so an unmatched
    marker is logged and dropped rather than treated as an error.
    """
    with direct_job_lock:
        active = _active_direct_jobs()
        if not active:
            return dict(state.get('direct_job') or {})

        matched = _find_marker_job(active, value, at, 'side_marker_values')
        if matched is None:
            # A sub-batch marker (see run_tool_batches), or a stale one.
            return dict(state.get('direct_job') or {})

        _complete_matched_jobs(active, matched, at, 'side-channel',
                               f"laser board marker {value}")
        return dict(state.get('direct_job') or {})


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


def _bounded_body_int(data, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(data.get(name, default))
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
    """Serve the main page. The instance name keeps several tabs apart."""
    return render_template(
        'index.html',
        instance_name=config.display_name(),
        is_default_profile=config.get_profile() is None,
    )


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
    with direct_job_lock:
        direct_job['in_flight'] = len(_active_direct_jobs())

    return jsonify({
        'position': head.position,
        'head': head.snapshot(),
        'step_size': state['step_size'],
        'printer_connected': state['printer_connected'],
        'connection_error': state['connection_error'],
        'printer_ip': config.get_config().get('ip', ''),
        'profile': config.profile_label(),
        'printer_name': config.display_name(),
        'printer_state': printer_state,
        'mqtt_progress': mqtt_progress,
        'mqtt_status': mqtt_status,
        'marker_link': marker_link_status(),
        'tool_commands': tool_command_status(),
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
    """Jog an axis by `distance` mm.

    The move goes out absolute, to where the tracker says the head will be
    plus the step, so the printer stays in G90 and a jog cannot drift away
    from what we think the position is. Two cases still jog relative: a
    position we do not know yet (nothing homed or zeroed, or a job left it
    uncertain), and E, which this rig extrudes relative.
    """
    data = request.json
    axis = data.get('axis', '').lower()
    distance = float(data.get('distance', 0))

    if axis not in ['x', 'y', 'z', 'e']:
        return jsonify({'success': False, 'error': 'Invalid axis'}), 400

    target = head.target_state()
    relative = axis == 'e' or head.uncertain
    value = distance if relative else getattr(target, axis) + distance

    # Only switch mode when this jog needs the other one.
    sent = []
    success = True
    if target.absolute is relative:
        mode = "G91" if relative else "G90"
        sent.append(mode)
        add_to_history(mode)
        success = send_tracked(mode)

    gcode_move = f"G1 {axis.upper()}{value:.3f} F{state['feed_rate']:.0f}"
    sent.append(gcode_move)
    add_to_history(gcode_move)
    success = success and send_tracked(gcode_move)

    return jsonify({
        'success': success,
        'position': head.position,
        'gcode': sent
    })


@app.route('/api/home', methods=['POST'])
def home_axes():
    """Home axes: body {"axes": "XY"} (default) or {"axes": "Z"} or {"axes": "XYZ"}."""
    data = request.get_json(silent=True) or {}
    axes = ''.join(ch for ch in str(data.get('axes', 'XY')).upper() if ch in 'XYZ')
    if not axes:
        return jsonify({'success': False, 'error': 'axes must be some of X, Y, Z'}), 400

    # Home in absolute mode. Nothing is sent unless a relative jog left the
    # printer in G91, and it stays absolute afterwards.
    if set_absolute_mode():
        time.sleep(0.1)

    gcode = "G28 " + " ".join(axes)
    add_to_history(gcode)

    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
        'gcode': gcode
    })


@app.route('/api/set-xy-zero', methods=['POST'])
def set_xy_zero():
    """Set current X and Y position as zero."""
    gcode = "G92 X0 Y0"
    add_to_history(gcode)

    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
        'gcode': gcode
    })


@app.route('/api/set-xyze-zero', methods=['POST'])
def set_xyze_zero():
    """Set current X, Y, Z and E positions as zero."""
    gcode = "G92 X0 Y0 Z0 E0"
    add_to_history(gcode)

    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
        'gcode': gcode
    })


@app.route('/api/save-z-zero', methods=['POST'])
def save_z_zero():
    """Save current Z position as zero."""
    gcode = "G92 Z0"
    add_to_history(gcode)

    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
        'gcode': gcode
    })


@app.route('/api/reset-e-zero', methods=['POST'])
def reset_e_zero():
    """Reset E position to zero."""
    gcode = "G92 E0"
    add_to_history(gcode)

    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
        'gcode': gcode
    })


@app.route('/api/motors-off', methods=['POST'])
def motors_off():
    """Disable stepper motors: body {"axes": "E"} or {"axes": "XYZ"} (default all)."""
    data = request.get_json(silent=True) or {}
    axes = ''.join(ch for ch in str(data.get('axes', 'XYZE')).upper() if ch in 'XYZE')
    if not axes:
        return jsonify({'success': False, 'error': 'axes must be some of X, Y, Z, E'}), 400

    gcode = "M18 " + " ".join(axes)
    add_to_history(gcode)
    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
        'head': head.snapshot(),
        'gcode': gcode
    })


ESTOP_COMMANDS = ('stop', 'pause', 'resume')


@app.route('/api/estop', methods=['POST'])
def estop():
    """Out-of-band printer command: body {"command": "stop"} (default), "pause" or "resume".

    Goes over MQTT as a print command rather than gcode_line, so it should not
    queue behind G-code already sent. Whether it cancels streamed gcode_line
    moves is what this endpoint is here to test. It only stops motion: tool
    state (the UV light, the servo) is left as it is. stop/pause also mark
    every in-flight direct job aborted.
    """
    data = request.get_json(silent=True) or {}
    command = str(data.get('command', 'stop')).lower()
    if command not in ESTOP_COMMANDS:
        return jsonify({'success': False, 'error': f"command must be one of {', '.join(ESTOP_COMMANDS)}"}), 400
    if not state['printer_connected'] or not printer:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 503

    sent_at = time.time()
    try:
        success = bool(getattr(printer, f'{command}_print')())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    print(f"E-stop: sent MQTT '{command}' -> {success}")

    aborted = []
    dropped = 0
    if command != 'resume':
        # Sends still waiting their turn are dropped with the jobs.
        dropped = send_queue.clear()
        with direct_job_lock:
            for job in list(_active_direct_jobs()):
                _finish_direct_job(job, 'aborted', f"Aborted by e-stop ({command})")
                head.drop(job['id'])
                aborted.append(job['id'])

    return jsonify({
        'success': success,
        'command': command,
        'sent_at': sent_at,
        'aborted_jobs': aborted,
        'dropped_sends': dropped,
    })


@app.route('/api/move-z-absolute', methods=['POST'])
def move_z_absolute():
    """Move Z to absolute position."""
    data = request.json
    z_position = float(data.get('position', 0))

    # Absolute Z move; nothing is sent unless a relative jog left the printer
    # in G91, and it stays absolute afterwards.
    if set_absolute_mode():
        time.sleep(0.1)

    gcode = f"G1 Z{z_position:.1f} F600"
    add_to_history(gcode)

    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
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

    # Same M3/M5/M280 translation as the direct-send path.
    translated, counts = translate_tool_gcode(gcode)
    if (state['printer_connected'] and side_channel_ready()
            and any(s['kind'] == 'tool' for s in split_tool_batches(translated))):
        # Tool commands get the confirmed, resent-if-lost path.
        result = queue_direct_gcode(gcode, single_call=True)
        return jsonify({'success': result['success'], 'position': head.position,
                        'gcode': translated, 'job_id': result.get('job_id'),
                        'errors': result.get('errors')})
    gcode = translated
    add_to_history(gcode)
    success = send_tracked(gcode)

    return jsonify({
        'success': success,
        'position': head.position,
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
    cfg = config.get_config()
    return jsonify({
        'ip': cfg.get('ip', ''),
        'serial': cfg.get('serial', ''),
        'access_code': cfg.get('access_code', ''),
        'name': cfg.get('name', ''),
        'profile': config.profile_label(),
        'config_file': str(config.config_path())
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
        access_code=data.get('access_code'),
        name=data.get('name')
    )

    if success:
        print(f"Config saved [{config.profile_label()}]: {config.get_config()}")

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


# ---------------------------------------------------------------------------
# Sub-batches: G-code with laser or servo commands in it.
#
# The AMS-Lite tap corrupts the odd frame, and a corrupted laser or servo
# command is simply lost. So when the laser board is connected, G-code with
# tool commands in it is split at each one and sent as a run of batches, one
# after another, each sent only once the one before it is done:
#
#   motion batch:  <lines>, M400, M620 R<code> (serial marker), M73 P<n> R0
#   tool batch:    M400, M620 P/R<value>, M73 P<n> R0
#
# A batch is done when either of its markers comes back: the serial one off
# the laser board within milliseconds of the motion ending, or the M73 over
# MQTT one to two seconds later should the serial one be lost.
#
# A tool batch needs no serial marker of its own: the board echoes every
# command it decodes, so the command itself is its serial marker. (A
# separate one would not even work for the servo: the printer sends one bus
# frame per channel per publish, carrying only the last value, so a marker
# on R would swallow the servo move.) If the M73 comes back and the echo
# never does, the printer ran the command but the board did not get it, so
# the batch is sent again. Commands are absolute (a power, an angle), so a
# repeat is harmless.
#
# Every tool confirmation, miss and failure is logged to the server output
# and appended as JSON lines to tool_log_path().
TOOL_LINE = re.compile(r'^M620\s+([PR])(\d+)\b', re.I)
TOOL_ADDRESS = {'P': 0, 'R': 1}
TOOL_MAX_ATTEMPTS = 4
BOARD_MARK_KEEP = 128
TOOL_LOG_KEEP = 200

board_mark_cond = threading.Condition()
board_marks = []        # (address, value, at) as read off the board, newest last
tool_log = []           # recent tool events, newest last
tool_stats = {'confirmed': 0, 'missed': 0, 'resent_ok': 0, 'failed': 0}


def tool_log_path():
    profile = config.get_profile()
    return config.CONFIG_DIR / f".bambucuts{'.' + profile if profile else ''}.tool-commands.log"


def note_board_mark(address, value, at):
    """Anything the board reported: a done marker or a command echo. Reader thread."""
    with board_mark_cond:
        board_marks.append((address, value, at))
        del board_marks[:-BOARD_MARK_KEEP]
        board_mark_cond.notify_all()


def _find_board_mark(address, value, since):
    """Arrival time of a matching report at or after `since`. Caller holds board_mark_cond.

    address None matches either channel, as done markers are matched on
    their value alone.
    """
    return next((at for a, v, at in reversed(board_marks)
                 if v == value and (address is None or a == address) and at >= since), None)


def _log_tool_event(event, segment, **fields):
    entry = {'at': time.time(), 'event': event, 'command': segment['command'],
             'address': segment['address'], 'value': segment['value'], **fields}
    detail = ' '.join(f"{k}={v}" for k, v in fields.items() if v is not None)
    traffic('tool_' + event, command=segment['command'], **fields)
    print(f"Tool command {event}: {segment['command']} {detail}".rstrip())
    with board_mark_cond:
        tool_log.append(entry)
        del tool_log[:-TOOL_LOG_KEEP]
        if event in tool_stats:
            tool_stats[event] += 1
    try:
        with open(tool_log_path(), 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError as e:
        print(f"Cannot write tool log: {e}")


def tool_command_status():
    with board_mark_cond:
        return {'stats': dict(tool_stats), 'recent': tool_log[-10:], 'log_path': str(tool_log_path())}


def split_tool_batches(gcode_text: str):
    """Split translated G-code into motion segments and single tool commands.

    Returns [{'kind': 'gcode', 'lines': [...]}, {'kind': 'tool', 'lines':
    ['M400', <M620 line>], 'address', 'value', 'command'}, ...], executable
    lines only. Any M620 P/R below the marker range counts as a tool command,
    hand-written ones included. An M400 right before one is folded into it.
    """
    segments, current = [], []
    for _, code in extract_executable_gcode(gcode_text):
        match = TOOL_LINE.match(code)
        if not match or int(match.group(2)) >= SIDE_MARKER_MIN:
            current.append(code)
            continue
        if current and current[-1].upper() == TOOL_SYNC:
            current.pop()
        if current:
            segments.append({'kind': 'gcode', 'lines': current})
            current = []
        channel, value = match.group(1).upper(), int(match.group(2))
        segments.append({'kind': 'tool', 'lines': [TOOL_SYNC, code],
                         'address': TOOL_ADDRESS[channel], 'value': value,
                         'command': f"{channel}{value}"})
    if current:
        segments.append({'kind': 'gcode', 'lines': current})
    return segments


def _job_still_active(job_id):
    if not job_id:
        return True
    with direct_job_lock:
        return bool((state.get('direct_jobs', {}).get(job_id) or {}).get('active'))


def _mqtt_reported(percent, since):
    """Whether the printer has reported this M73 percent since `since`."""
    _, progress = current_mqtt_progress()
    return (not progress.get('stale')
            and (progress.get('last_update') or 0) >= since
            and str(progress.get('percent')) == str(percent)
            and progress.get('remaining_time') in (0, None))


def send_sub_batch(lines, serial, percent, timeout, single_call, job_id):
    """Send one batch and wait for either of its done markers.

    `serial` is (address or None, value) as the board will report it.
    Returns ('serial' | 'mqtt' | None, seconds); None when neither came
    back within the timeout, the send failed, or the job was aborted.
    """
    sent_at = time.time()
    if not _send_segment_lines(lines, single_call):
        return None, 0.0
    deadline = sent_at + timeout
    while _job_still_active(job_id):
        with board_mark_cond:
            at = _find_board_mark(serial[0], serial[1], sent_at)
            if at is not None:
                return 'serial', round(at - sent_at, 3)
            board_mark_cond.wait(0.05)
        if _mqtt_reported(percent, sent_at):
            # The serial report comes milliseconds after execution, the MQTT
            # one 1-2 s after, so one not in by now is lost, not late.
            with board_mark_cond:
                at = _find_board_mark(serial[0], serial[1], sent_at)
            if at is not None:
                return 'serial', round(at - sent_at, 3)
            return 'mqtt', round(time.time() - sent_at, 3)
        if time.time() >= deadline:
            break
    return None, round(time.time() - sent_at, 3)


def _send_segment_lines(lines, single_call):
    """Send lines as one publish or one per line, as the job asked."""
    for line in lines:
        add_to_history(line)
    if single_call:
        return send_gcode_lines_to_printer(lines)
    ok = True
    for line in lines:
        ok = send_gcode_to_printer(line) and ok
        time.sleep(0.05)
    return ok


def run_tool_batches(job_id, segments, single_call, start_state, wait_for_motion):
    """Send a job's batches one after another, each once the one before is done.

    Runs on the send queue's worker. Completes the job itself when the last
    batch is done; the job carries no markers of its own for the matchers.
    """
    motion = start_state
    errors, source, queued = [], None, 0
    try:
        for segment in segments:
            if not _job_still_active(job_id):
                print(f"Direct job {(job_id or 'untracked')[:8]}: stopped sending, job no longer active")
                return      # aborted: the e-stop already finished the job
            percent = next_marker_percent()
            mqtt_line = f"M73 P{percent} R0"
            if segment['kind'] == 'gcode':
                sim = simulate_gcode('\n'.join(segment['lines']), motion)
                motion = sim.end
                code = next_side_marker_block()[0]
                sync = [TOOL_SYNC] if wait_for_motion else []
                lines = segment['lines'] + sync + [f"M620 R{code} M621 R{code}", mqtt_line]
                source, took = send_sub_batch(lines, (None, code), percent,
                                              direct_job_timeout_seconds(sim.seconds), single_call, job_id)
                queued += len(lines)
                if source is None:
                    errors.append(f"No done marker for a {len(segment['lines'])}-line batch after {took}s")
                    break
                continue
            for attempt in range(1, TOOL_MAX_ATTEMPTS + 1):
                lines = segment['lines'] + [mqtt_line]
                source, took = send_sub_batch(lines, (segment['address'], segment['value']), percent,
                                              DIRECT_JOB_MIN_TIMEOUT_SECONDS, True, job_id)
                queued += len(lines)
                if source == 'serial':
                    _log_tool_event('confirmed', segment, attempt=attempt, latency=took)
                    if attempt > 1:
                        with board_mark_cond:
                            tool_stats['resent_ok'] += 1
                    break
                if source == 'mqtt':
                    _log_tool_event('missed', segment, attempt=attempt, waited=took)
                    percent = next_marker_percent()
                    mqtt_line = f"M73 P{percent} R0"
                    continue
                if _job_still_active(job_id):
                    _log_tool_event('failed', segment, attempt=attempt, reason=f'no done marker after {took}s')
                break
            if source != 'serial':
                if source == 'mqtt':
                    _log_tool_event('failed', segment, attempt=TOOL_MAX_ATTEMPTS,
                                    reason='still missing after resends')
                errors.append(f"Tool command {segment['command']} not confirmed")
                break
    except Exception as e:
        errors.append(f"Batch sender crashed: {e}")
    finally:
        if errors:
            print(f"Direct job {(job_id or 'untracked')[:8]} stopped: {'; '.join(errors)}")
        if job_id is not None and _job_still_active(job_id):
            mark_direct_job_queued(job_id, queued, errors)
            if not errors:
                at = time.time()
                with direct_job_lock:
                    job = state['direct_jobs'].get(job_id)
                    if job and job.get('active'):
                        job['detect_source'] = 'side-channel' if source == 'serial' else source
                        job['detect_seconds'] = round(at - job['started_at'], 3)
                        _finish_direct_job(job, 'complete', f'All batches done (last via {source})', completed_at=at)
                        head.commit(job_id, at=at)


def _send_direct_lines(executable_lines, marker_lines, single_call, job_id):
    """Send a job with no tool commands. Returns (sent, queued, payload_bytes, errors)."""
    if not _job_still_active(job_id):
        return 0, 0, 0, []
    sent_count = queued_count = payload_bytes = 0
    errors = []
    if single_call:
        # Ship every line in a single MQTT gcode_line command instead of one
        # publish per line.
        lines = [line for _, line in executable_lines]
        for line in lines:
            add_to_history(line)

        queued_count = len(lines)
        sent_count = sum(1 for line in lines if line not in marker_lines)
        payload_bytes = len('\n'.join(lines).encode('utf-8'))

        if state['printer_connected']:
            if not send_gcode_lines_to_printer(lines):
                errors.append(f"Failed to send {queued_count} lines in one call")
    else:
        for line_num, line in executable_lines:
            add_to_history(line)
            if state['printer_connected']:
                if not send_gcode_to_printer(line):
                    errors.append(f"Line {line_num}: Failed to send")

            queued_count += 1
            if line not in marker_lines:
                sent_count += 1
            time.sleep(0.05)  # Small delay between commands

    if job_id is not None:
        mark_direct_job_queued(job_id, queued_count, errors)
    return sent_count, queued_count, payload_bytes, errors


def queue_direct_gcode(gcode_text: str, single_call: bool = False, progress_markers: bool = True,
                       wait_for_motion: bool = True, timeout_seconds: float = None):
    """Queue G-code on the printer as a tracked direct job.

    Returns the same dict the send-all endpoint reports. When progress
    markers are on and the printer is connected, the returned 'direct_job'
    can be followed to completion with wait_for_direct_job(). The head
    tracker simulates the batch from wherever the queued G-code leaves the
    head, and commits it when the done marker arrives. timeout_seconds
    replaces the stall timeout worked out from the estimate.

    The G-code goes through the send queue, so it never overtakes or
    interleaves with anything submitted before it. With nothing ahead it is
    sent before this returns, as it always was. Otherwise the job is
    returned in 'queueing' and goes out when its turn comes.
    """
    progress_info = {
        'enabled': False,
        'command_count': sum(1 for _ in extract_executable_gcode(gcode_text)),
        'marker_value': None,
        'estimated_seconds': round(estimate_gcode_seconds(gcode_text), 1),
        'timeout_seconds': 0.0,
    }
    # Tool commands become laser board commands before anything else looks
    # at the G-code, so history, the head tracker and the done markers all
    # see what is actually sent.
    gcode_text, tool_counts = translate_tool_gcode(gcode_text)
    if tool_counts['laser'] or tool_counts['servo']:
        print(f"Translated {tool_counts['laser']} laser and {tool_counts['servo']} "
              f"servo command(s) into laser board commands")

    segments = split_tool_batches(gcode_text)
    tool_segments = sum(1 for s in segments if s['kind'] == 'tool')
    split = bool(tool_segments and state['printer_connected'] and side_channel_ready())
    if tool_segments and state['printer_connected'] and not split:
        print(f"Laser board not connected: {tool_segments} tool command(s) sent unconfirmed")

    gcode_to_send = gcode_text
    if split:
        # Sent as a run of batches, each with its own markers; the job has
        # none of its own and is completed by run_tool_batches.
        progress_info = dict(progress_info, enabled=True, marker_field=MARKER_FIELD,
                             side_marker_values=None, marker_lines=[], batches=len(segments),
                             timeout_seconds=round(direct_job_timeout_seconds(progress_info['estimated_seconds']), 1))
    elif progress_markers:
        gcode_to_send, progress_info = add_completion_marker(gcode_text, wait_for_motion=wait_for_motion)
    if timeout_seconds and progress_info.get('enabled'):
        progress_info['timeout_seconds'] = round(float(timeout_seconds), 1)

    direct_job = None
    if state['printer_connected'] and progress_info.get('enabled'):
        direct_job = begin_direct_job(progress_info)
    job_id = direct_job['id'] if direct_job else None

    # The head tracker is updated in submission order, here, so whatever is
    # submitted next plans from where this job leaves the head (and in the
    # G90/G91 mode it leaves) even while this one is still waiting to send.
    # A tracked job is committed when its marker arrives; anything else is
    # assumed to execute right away.
    start_state = head.target_state()
    head.apply(gcode_to_send, job_id=job_id)

    # Which of the lines about to be queued are our own done markers. Matched
    # exactly, not by command prefix: the side-channel marker is an M620, and
    # the user's G-code legitimately contains M620s of its own for the laser.
    marker_lines = list(progress_info.get('marker_lines') or ())
    executable_lines = list(extract_executable_gcode(gcode_to_send))
    planned = {
        'sent_count': len(executable_lines) - len(marker_lines),
        'queued_count': len(executable_lines),
        'payload_bytes': len('\n'.join(l for _, l in executable_lines).encode('utf-8')) if single_call else 0,
    }

    if split:
        # Never inline: the batches go out one after another as each is
        # done, which takes as long as the job's motion, and the caller
        # follows the job instead.
        send_queue.submit(lambda: run_tool_batches(job_id, segments, single_call, start_state,
                                                   wait_for_motion), inline=False)
        ran, outcome = False, None
    else:
        ran, outcome = send_queue.submit(
            lambda: _send_direct_lines(executable_lines, set(marker_lines), single_call, job_id))

    if ran:
        sent_count, queued_count, payload_bytes, errors = outcome
    else:
        sent_count, queued_count, payload_bytes, errors = (
            planned['sent_count'], planned['queued_count'], planned['payload_bytes'], [])
    if job_id:
        with direct_job_lock:
            direct_job = dict(state['direct_jobs'].get(job_id) or direct_job)
    result = {
        'success': len(errors) == 0,
        'sent_count': sent_count,
        'queued_count': queued_count,
        'single_call': single_call,
        'payload_bytes': payload_bytes,
        'progress': progress_info,
        'direct_job': direct_job,
        'job_id': job_id,
        'errors': errors,
    }
    if tool_segments:
        result['tool_commands'] = tool_segments
        result['tool_confirmation'] = split
    return result


def wait_for_direct_job(job_id: str, poll_seconds: float = 0.02, stop_event=None, timeout: float = None):
    """Block until the direct job leaves the active state, then return it.

    The MQTT callback resolves completion on its own; this loop also re-runs
    the stall check so a dead feed still ends the wait. With a timeout the
    job is returned as-is when it expires, possibly still active.
    """
    deadline = (time.monotonic() + timeout) if timeout else None
    while True:
        with direct_job_lock:
            job = dict(state.get('direct_jobs', {}).get(job_id) or {})
        if not job:
            return None
        if not job.get('active'):
            return job
        if stop_event is not None and stop_event.is_set():
            return job
        if deadline is not None and time.monotonic() >= deadline:
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
    # m400=false drops the motion wait before the marker: use it for batches
    # that have another batch queued behind them so the pen never stops.
    wait_for_motion = bool(data.get('m400', True))
    # wait=true blocks until the job completes, stalls, or wait_seconds pass
    # (default: the job's own timeout plus a margin) and returns the final job.
    wait = bool(data.get('wait', False))
    # timeout_seconds overrides how long the job may run before it counts as
    # stalled (default: estimate x DIRECT_JOB_TIMEOUT_FACTOR + pad).
    try:
        timeout_seconds = float(data.get('timeout_seconds') or 0) or None
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'timeout_seconds must be a number'}), 400

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to send'}), 400

    result = queue_direct_gcode(gcode_text, single_call=single_call, progress_markers=progress_markers,
                                wait_for_motion=wait_for_motion, timeout_seconds=timeout_seconds)
    if wait and result.get('job_id'):
        try:
            wait_seconds = float(data.get('wait_seconds') or 0) or None
        except (TypeError, ValueError):
            wait_seconds = None
        job = result['direct_job'] or {}
        if wait_seconds is None and not job.get('deadline'):
            # Still waiting to send, or still sending: the sender ends the
            # job one way or the other, and the stall check takes over once
            # it is queued.
            timeout = None
        else:
            if wait_seconds is None:
                wait_seconds = (job.get('deadline') or time.time() + DIRECT_JOB_MIN_TIMEOUT_SECONDS) - time.time() + 5.0
            timeout = max(0.0, wait_seconds)
        t0 = time.perf_counter()
        job = wait_for_direct_job(result['job_id'], timeout=timeout) or result['direct_job']
        result['direct_job'] = {k: v for k, v in job.items() if k != 'reports'}
        result['completed'] = job.get('status') == 'complete'
        result['waited_seconds'] = round(time.perf_counter() - t0, 3)
        result['success'] = result['success'] and result['completed']
        if job.get('active'):
            result['error'] = f"Still running after {result['waited_seconds']:.0f}s wait"
        elif job.get('status') != 'complete':
            result['error'] = job.get('message')
    return jsonify(result)


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


def _benchmark_one(gcode_text: str, single_call: bool, stop_event):
    """Queue one batch, wait for its done signal, and time each phase."""
    t_send = time.perf_counter()
    result = queue_direct_gcode(gcode_text, single_call=single_call)
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
        'detect_source': job.get('detect_source'),
        'detect_report_kind': job.get('detect_report_kind'),
        'detect_pushall_rtt': job.get('detect_pushall_rtt'),
        'detect_since_pushall': job.get('detect_since_pushall'),
        'pushalls_sent': job.get('pushalls_sent'),
        'reports': list(job.get('reports') or []),
    }


def run_direct_latency_benchmark(iterations: int = 10, line_counts=(1, 10), feed_mm_min=None,
                                 single_call: bool = True, stop_event=None, distance_mm=None,
                                 pushall_interval=None):
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
                setup = _benchmark_one(setup_gcode, single_call, stop_event)
                if not setup['ok']:
                    raise RuntimeError(f"setup move failed on run {run_index}: {setup.get('error')}")
                run = _benchmark_one(batch_gcode, single_call, stop_event)
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
    estimated = estimate_gcode_seconds(
        gcode_text, MotionState(x=BENCHMARK_TARGET_XY[0], y=BENCHMARK_TARGET_XY[1], feed=feed_mm_min))

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
            'marker_value': None,
            'estimated_seconds': round(estimate_gcode_seconds(gcode_text), 1),
            'timeout_seconds': 0.0,
        }
        gcode_to_package = gcode_text
        if progress_markers:
            gcode_to_package, progress_info = add_completion_marker(gcode_text)

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
        # No done signal is followed for file prints; assume the file runs.
        head.apply(gcode_to_package)

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
            gcode_to_package, _ = add_completion_marker(gcode_text)

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


def start_server(host='0.0.0.0', port=None, debug=False):
    """Start the Bambu Cuts web server (port defaults to the profile's own)."""
    port = int(port or config.default_port())
    print("Starting Bambu Cuts - Cutter and Plotter API...")
    print(f"Profile: {config.profile_label()}  ({config.config_path()})")
    print(f"Printer IP: {config.get_config().get('ip', 'Not configured')}")
    print(f"Server will run at: http://{host}:{port}")
    print()

    # Auto-connect on startup
    print("Attempting to connect to printer on startup...")
    connect_printer()
    start_marker_link()

    try:
        socketio.run(app, host=host, port=port, debug=debug, use_reloader=False, allow_unsafe_werkzeug=True)
    finally:
        # Clean up
        stop_camera_stream()
        stop_marker_link()
        disconnect_printer()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Bambu Cuts web server')
    parser.add_argument('--profile', default=None,
                        help='Printer profile to use (default: $BAMBUCUTS_PROFILE or the default profile)')
    parser.add_argument('--host', default=None, help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=None,
                        help="Port to bind to (default: the profile's 'port', else 5425 for the "
                             "default profile and one port up per extra profile)")
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()

    if args.profile is not None:
        config.set_profile(args.profile)

    cfg = config.get_config()
    host = args.host or os.environ.get('BAMBUCUTS_HOST') or cfg.get('host') or '0.0.0.0'
    port = args.port or int(os.environ.get('BAMBUCUTS_PORT') or cfg.get('port')
                           or config.default_port())

    start_server(host=host, port=port, debug=args.debug)
