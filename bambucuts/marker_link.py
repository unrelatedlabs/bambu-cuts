"""Completion markers from the laser board over USB serial.

The printer's MQTT status report picks up an executed `M73` one to two
seconds late, which puts a floor of roughly 1.7 s under how fast a direct
job can be confirmed (see the latency notes in README.md). That delay is
pure reporting lag: the motion itself has already finished.

The laser board sits on the AMS-Lite bus and decodes `M620` within a
millisecond of the printer executing it, so a marker routed through the
board and back over USB confirms the batch in single-digit milliseconds.
The host appends both markers to a batch and completes the job off
whichever arrives first, so this is an accelerator: with no board attached,
or with a wedged one, jobs still complete on the MQTT marker as before.

The board emits one line per marker on its USB data port:

    MARK <address> <value> <board_uptime_ms>

`address` is 0 for the laser channel (`M620 P`) and 1 for the servo
(`M620 R`); `value` is the marker code, 201 and up. Markers go out on the
servo channel, but a marker is matched on its value alone and the address is
only recorded: no real command ever reaches 201, so there is nothing to be
gained by being strict about which channel carried it.

The board's uptime is carried for debugging only - arrival here is what gets
timestamped, since the two clocks share no origin.
"""

import glob
import threading
import time

# USB CDC ignores the line speed, but pyserial wants one.
BAUDRATE = 115200

# How long to wait before reopening a port that errored or went away. The
# board resets whenever code.py is saved, so this happens in normal use.
RECONNECT_DELAY = 2.0

# Blocking read timeout, which also bounds how fast stop() is noticed.
READ_TIMEOUT = 0.2

# Recent markers kept for the status endpoint.
MARKER_LOG_LIMIT = 50

# Where to look for the board when no device is configured. The data port
# that boot.py enables is preferred; -if00 is the console, which code.py
# falls back to when the data port is not enabled.
DEVICE_GLOBS = (
    '/dev/serial/by-id/usb-Lolin_S2_Mini_*-if02',
    '/dev/serial/by-id/usb-*S2_Mini*-if02',
    '/dev/serial/by-id/usb-Lolin_S2_Mini_*-if00',
    '/dev/serial/by-id/usb-*S2_Mini*-if00',
)


def find_device():
    """First matching board path, preferring the dedicated data port.

    Paths under /dev/serial/by-id carry the board's UID, so they survive a
    replug where /dev/ttyACM<n> can renumber.
    """
    for pattern in DEVICE_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def parse_marker(line: str):
    """Parse one `MARK <address> <value> <uptime_ms>` line, else None.

    Anything else on the port - the board's own status and debug output when
    markers fall back to the console - is not a marker.
    """
    parts = line.split()
    if len(parts) < 3 or parts[0] != 'MARK':
        return None
    try:
        address = int(parts[1])
        value = int(parts[2])
    except ValueError:
        return None
    try:
        board_ms = int(parts[3]) if len(parts) > 3 else None
    except ValueError:
        board_ms = None
    return {'address': address, 'value': value, 'board_ms': board_ms}


class MarkerLink:
    """Background reader that reports markers from the board as they arrive.

    `on_marker(address, value, at)` is called from the reader thread, so it
    must be quick and must not assume the Flask request context.
    """

    def __init__(self, device=None, on_marker=None, enabled=True):
        self._configured_device = device or None
        self._on_marker = on_marker
        self._enabled = enabled
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = {
            'enabled': enabled,
            'connected': False,
            'device': None,
            'error': None,
            'marker_count': 0,
            'last_marker': None,
            'last_update': None,
            'opened_at': None,
            'log': [],
        }

    # ----- control -----

    def start(self):
        """Start the reader thread. Returns False if disabled or already up."""
        if not self._enabled:
            self._set(error='Marker side channel disabled')
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='marker-link', daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=RECONNECT_DELAY + READ_TIMEOUT)
        self._thread = None
        self._set(connected=False)

    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def status(self):
        """Snapshot for the status endpoint."""
        with self._lock:
            snapshot = dict(self._state)
        snapshot['log'] = list(snapshot.get('log') or ())
        snapshot['listener_alive'] = self.alive()
        return snapshot

    # ----- internals -----

    def _set(self, **changes):
        with self._lock:
            self._state.update(changes)

    def _record(self, marker, at):
        with self._lock:
            self._state['marker_count'] += 1
            self._state['last_marker'] = dict(marker, at=at)
            self._state['last_update'] = at
            log = self._state.setdefault('log', [])
            log.append({'at': at, 'address': marker['address'], 'value': marker['value']})
            if len(log) > MARKER_LOG_LIMIT:
                del log[:len(log) - MARKER_LOG_LIMIT]

    def _open(self):
        """Open the board's port, or return None with the reason recorded."""
        try:
            import serial
        except ImportError as e:
            self._set(connected=False, error=f'Marker side channel needs pyserial: {e}')
            return None

        device = self._configured_device or find_device()
        if not device:
            self._set(connected=False, device=None,
                      error='Laser board not found on any /dev/serial/by-id path')
            return None

        try:
            port = serial.Serial(device, BAUDRATE, timeout=READ_TIMEOUT)
        except Exception as e:
            self._set(connected=False, device=device,
                      error=f'Cannot open {device}: {e}')
            return None

        self._set(connected=True, device=device, error=None, opened_at=time.time())
        print(f"Marker side channel open on {device}")
        return port

    def _read_lines(self, port):
        """Read markers until the port errors or we are asked to stop."""
        while not self._stop.is_set():
            try:
                raw = port.readline()
            except Exception as e:
                self._set(connected=False, error=f'Marker read failed: {e}')
                print(f"Marker side channel read failed: {e}")
                return
            if not raw:
                continue        # read timeout, nothing waiting
            try:
                line = raw.decode('ascii', 'replace').strip()
            except Exception:
                continue
            marker = parse_marker(line)
            if marker is None:
                continue
            at = time.time()
            self._record(marker, at)
            if self._on_marker:
                try:
                    self._on_marker(marker['address'], marker['value'], at)
                except Exception as e:
                    print(f"Marker callback failed: {e}")

    def _run(self):
        while not self._stop.is_set():
            port = self._open()
            if port is None:
                if self._stop.wait(RECONNECT_DELAY):
                    break
                continue
            try:
                self._read_lines(port)
            finally:
                try:
                    port.close()
                except Exception:
                    pass
                self._set(connected=False)
            if self._stop.wait(RECONNECT_DELAY):
                break
