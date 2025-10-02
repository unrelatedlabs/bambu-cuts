import ssl, json
from paho.mqtt.client import Client
import sys, os, time
import threading
from config import PRINTER_IP, SERIAL, ACCESS_CODE

VERBOSE_STATUS = False  # Set True to see push_status spam

# MQTT status listener for A1 Mini Bambu printer
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker for status updates.")
        # Subscribe to the status topic (adjust topic as needed)
        client.subscribe(f"device/{SERIAL}/report")
    else:
        print(f"Failed to connect, return code {rc}")

# Global variable to track G-code acknowledgments
gcode_ack_received = threading.Event()

# Reuse the same ack event for any print command (gcode_line, gcode_file, project_file)
print_cmd_ack_received = gcode_ack_received

# Global variable to track when device is ready (first status message received)
device_ready = threading.Event()

# Global MQTT client for sending G-code commands
gcode_client = None

def on_message(client, userdata, msg):
    payload = msg.payload.decode('utf-8')
    # print(f"[STATUS UPDATE] Topic: {msg.topic}\nPayload: {payload}")
    
    # Set device ready on first status message
    if not device_ready.is_set():
        device_ready.set()
        print("Device is ready - first status message received")
    
    try:
        data = json.loads(payload)
        if 'print' in data:
            pr = data['print']
            command = pr.get('command', '')
            if command in ('gcode_line','gcode_file','project_file'):
                result = pr.get('result', '')
                param = pr.get('param', '') or pr.get('url', '')
                label = 'G-CODE' if command == 'gcode_line' else ('GCODE_FILE' if command == 'gcode_file' else 'PROJECT_FILE')
                print(f"[{label} CONFIRM] {param} -> {result}")
                if command == 'gcode_line' and param == 'M114' and result == 'success':
                    print("M114 position query completed - check printer display for coordinates")
                print_cmd_ack_received.set()
            elif command == 'push_status':
                if VERBOSE_STATUS:
                    # print a compact subset to avoid trashing the REPL input
                    subset_keys = ('nozzle_temper','bed_temper','wifi_signal','state','mc_print_state','busy','move','motion')
                    subset = {k: pr[k] for k in subset_keys if k in pr}
                    print(f"[PUSH_STATUS] {subset}" if subset else "[PUSH_STATUS]")
                # otherwise stay quiet
    except json.JSONDecodeError:
        # If it's not JSON, print it (might be other important messages)
        print(f"[STATUS UPDATE] Topic: {msg.topic}\nPayload: {payload}")

def listen_for_status():
    client = Client()
    client.username_pw_set("bblp", ACCESS_CODE)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(PRINTER_IP, 8883, 60)
    print("Listening for status updates. Press Ctrl+C to stop.")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nStopped listening for status updates.")
        client.disconnect()


# Credentials moved to config.py

def init_gcode_client():
    """Initialize the persistent MQTT client for sending G-code commands"""
    global gcode_client
    if gcode_client is None:
        gcode_client = Client()
        gcode_client.username_pw_set("bblp", ACCESS_CODE)
        gcode_client.tls_set(cert_reqs=ssl.CERT_NONE)
        gcode_client.tls_insecure_set(True)
        gcode_client.connect(PRINTER_IP, 8883, 60)
        print("G-code client connected.")

def cleanup_gcode_client():
    """Clean up the persistent MQTT client"""
    global gcode_client
    if gcode_client is not None:
        gcode_client.disconnect()
        gcode_client = None
        print("G-code client disconnected.")

def wait_after_send(seconds: float):
    """Software-side barrier: sleep for a fixed time to let the printer execute the queued block."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return
    if seconds > 0:
        time.sleep(seconds)

def send_gcode_and_wait(gcode, wait_s: float = 0.0):
    """
    Send G-code (which may be multi-line) and optionally wait a fixed time on the client side.
    This is a workaround because Bambu's MQTT 'gcode_line' ack is for QUEUING, not COMPLETION.
    """
    ok = send_gcode(gcode)
    if ok and wait_s and wait_s > 0:
        wait_after_send(wait_s)
    return ok

def normalize_sdcard_path(path: str) -> str:
    """Return an absolute on-device path for the A1 Mini SD card."""
    if not path:
        return "/data/sdcard/"
    p = path.strip().lstrip()
    # Allow forms like 'filename.gcode', 'sdcard/filename.gcode', '/sdcard/filename.gcode', '/data/sdcard/filename.gcode'
    if p.startswith("/data/sdcard/"):
        return p
    if p.startswith("/sdcard/"):
        return "/data" + p
    if p.startswith("sdcard/"):
        return "/data/" + p
    if p.startswith("/"):
        # Treat as already-absolute elsewhere; leave as-is
        return p
    # bare filename -> place at root of sdcard
    return f"/data/sdcard/{p}"

def send_sdcard_file(path_on_sd: str) -> bool:
    """
    Ask the printer to run a G-code file that already exists on the device SD card.
    Uses the MQTT 'gcode_file' command.
    """
    global gcode_client
    # Wait for readiness
    if not device_ready.is_set():
        if not wait_for_device_ready():
            print("Cannot start SD card file - device not ready")
            return False

    # Ensure client
    if gcode_client is None:
        init_gcode_client()

    fullpath = normalize_sdcard_path(path_on_sd)
    payload = {
        "print": {
            "sequence_id": "0",
            "command": "gcode_file",
            "param": fullpath
        }
    }
    print(f"Requesting SD card print: {fullpath}")
    print_cmd_ack_received.clear()
    gcode_client.publish(f"device/{SERIAL}/request", json.dumps(payload), qos=1)
    if print_cmd_ack_received.wait(timeout=10):
        print(f"SD card file command acknowledged: {fullpath}")
        return True
    else:
        print(f"WARNING: No acknowledgment for SD card file: {fullpath}")
        return False

def wait_for_device_ready(timeout=30):
    """Wait for device to be ready (first status message received)"""
    print("Waiting for device to be ready...")
    if device_ready.wait(timeout=timeout):
        print("Device is ready!")
        return True
    else:
        print(f"WARNING: Device not ready after {timeout} seconds")
        return False

def query_xyz_position():
    """Query current XYZ position of the printer"""
    print("Querying XYZ position...")
    
    # Send M114 command to get current position
    if send_gcode("M114"):
        print("XYZ position query sent successfully")
        return True
    else:
        print("Failed to query XYZ position")
        return False

def send_gcode(gcode):
    """Send G-code command using the persistent client"""
    global gcode_client
    
    # Wait for device to be ready first
    if not device_ready.is_set():
        if not wait_for_device_ready():
            print("Cannot send G-code - device not ready")
            return False
    
    # Initialize client if not already done
    if gcode_client is None:
        init_gcode_client()
    
    payload = {
        "print": {
            "sequence_id": "0",
            "command": "gcode_line",
            "param": gcode
        }
    }
    
    # Clear previous acknowledgment and send command
    gcode_ack_received.clear()
    gcode_client.publish(f"device/{SERIAL}/request", json.dumps(payload), qos=1)
    
    # Wait for G-code acknowledgment with timeout
    if gcode_ack_received.wait(timeout=10):  # 10 second timeout
        print(f"G-code acknowledged: {gcode}")
        return True
    else:
        print(f"WARNING: No acknowledgment received for: {gcode}")
        return False




def send_gcode_file(filepath, chunk_size=1):
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        return
    
    # Wait for device to be ready before starting
    if not device_ready.is_set():
        if not wait_for_device_ready():
            print("Cannot send G-code file - device not ready")
            return
    
    print(f"Sending G-code from file in chunks of {chunk_size} lines: {filepath}")
    with open(filepath) as f:
        # keep comments so we can process WAIT directives
        raw_lines = [line.rstrip('\n') for line in f if line.strip()]
    total = len(raw_lines)
    i = 0
    pending = []
    def flush_pending():
        nonlocal i, pending
        if pending:
            gcode_block = '\n'.join(l for l in pending if not l.strip().startswith(';WAIT_')).upper()
            success = send_gcode(gcode_block) if gcode_block.strip() else True
            sent_count = len(pending)
            i += sent_count
            print(f"Sent lines {i - sent_count + 1}-{i} of {total}.")
            pending = []
            return success
        return True

    for line in raw_lines:
        line_stripped = line.strip()
        # Inline wait directive handling
        if line_stripped.upper().startswith(';WAIT_MS=') or line_stripped.upper().startswith(';WAIT_S='):
            # Flush any accumulated G-code first
            if not flush_pending():
                print("Stopping file send due to G-code failure (before WAIT)")
                break
            # Parse wait time
            try:
                if line_stripped.upper().startswith(';WAIT_MS='):
                    ms = float(line_stripped.split('=',1)[1])
                    wait_after_send(ms/1000.0)
                    print(f"[WAIT] Slept {ms} ms")
                else:
                    secs = float(line_stripped.split('=',1)[1])
                    wait_after_send(secs)
                    print(f"[WAIT] Slept {secs} s")
            except ValueError:
                print(f"Invalid WAIT directive ignored: {line_stripped}")
            # account for the directive line as 'sent' for progress display
            i += 1
            continue

        # add to pending block
        pending.append(line)
        if len(pending) >= chunk_size:
            if not flush_pending():
                print("Stopping file send due to G-code failure")
                break

    # flush any remaining lines
    if pending:
        flush_pending()


# Always start the MQTT status listener in a background thread
status_thread = threading.Thread(target=listen_for_status, daemon=True)
status_thread.start()

# Wait for device to be ready before proceeding
if not wait_for_device_ready():
    print("Failed to connect to device. Exiting.")
    sys.exit(1)

# Initialize the G-code client
init_gcode_client()

# CLI helpers:
#  - `python fiel.py somefile.gcode`  -> stream and send lines over MQTT
#  - `python fiel.py sd:filename.gcode` or `python fiel.py sd:/path/on/sdcard/file.gcode` -> run from SD
if len(sys.argv) > 1:
    arg1 = sys.argv[1]
    if arg1.lower().startswith('sd:'):
        sd_path = arg1[3:]
        send_sdcard_file(sd_path)
    elif arg1.endswith('.gcode'):
        gcode_file = arg1
        send_gcode_file(gcode_file)


def get_multiline_input():
    """Get multiline input from user, terminated by empty line"""
    lines = []
    print("Enter G-code lines (press Enter twice to send, or type 'exit'/'quit' to stop):")
    print("Special commands: 'xyz' to query position, 'help' for more info")
    while True:
        try:
            line = input('G-code> ' if not lines else '      > ').strip()
        except (EOFError, KeyboardInterrupt):
            return None
        
        if line.lower() in ("exit", "quit"):
            return "EXIT"
        elif line.lower() == "xyz":
            query_xyz_position()
            continue
        elif line.lower() == "help":
            print("Available commands:")
            print("  xyz         - Query current XYZ position (best-effort; may not return over MQTT)")
            print("  wait &lt;S&gt;   - Client-side wait (e.g., 'wait 2.5' sleeps 2.5s)")
            print("  exit/quit   - Exit the program")
            print("  help        - Show this help")
            print("  <gcode>     - Send G-code command(s)")
            print("  (empty line) - Send accumulated G-code lines")
            print("Inline in .gcode files: use ';WAIT_S=2.0' or ';WAIT_MS=500' to insert waits.")
            print("  sd <path>    - Run a .gcode that already exists on the printer SD (e.g., 'sd filename.gcode')")
            continue

        # one-line client-side wait command
        if line.lower().startswith("wait "):
            try:
                secs = float(line.split(None,1)[1])
                wait_after_send(secs)
                print(f"[WAIT] Slept {secs} s")
            except Exception:
                print("Usage: wait &lt;seconds&gt;")
            continue
        
        # run from SD card
        if line.lower().startswith("sd "):
            sd_path = line.split(None,1)[1]
            send_sdcard_file(sd_path)
            continue
        
        if not line:
            if lines:  # Empty line after some input - send the accumulated lines
                return '\n'.join(lines)
            else:  # Empty line with no input - continue waiting
                continue
        
        lines.append(line)

print("Enter G-code lines to send to the printer. Type 'exit' or 'quit' to stop.")
print("You can enter multiple lines - press Enter twice to send them all at once.")
print("Special commands: 'xyz' to query position, 'help' for more info")
try:
    while True:
        multiline_input = get_multiline_input()
        
        if multiline_input is None:  # EOF or KeyboardInterrupt
            print("\nExiting.")
            break
        elif multiline_input == "EXIT":  # User typed exit/quit
            print("Exiting.")
            break
        
        if multiline_input:
            send_gcode_and_wait(multiline_input, wait_s=0.0)
            print(f"Sent: {multiline_input}")
finally:
    # Clean up the G-code client on exit
    cleanup_gcode_client()