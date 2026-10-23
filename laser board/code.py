"""
Wemos ESP32 S2 Mini board

https://www.wemos.cc/en/latest/s2/s2_mini.html

GPIO

GPIO 1  = Uart RX. TOP RIGH AMS Lite PIN. Tx+ on the AMS Lite port on A1 mini
GPIO 2  = pull up, connect 510ohm resitor to GPIO1

GPIO 6 and 7  = LED + on 7, - on 6. Follow light outout

GPIP 39 = PWM output for laser power.

GPIO 33 = Servo signal (50 Hz PWM). Controlled with M620 R{angle}, angle 0-180.

Values 0-200 set laser power (P) or servo angle (R). Values from MARKER_MIN
up are completion markers for the host instead, and never touch the outputs:
see report_marker() below.

I used 2 DC/DC buck converters to get 12V and 5V from the 24V input from the printer.


AMS Lite pin out


      +-------------------+
      |      AMS Lite     |
      +-------------------+
      | [Tx-]     [Tx+]   |
      | [GND]     [24V]   |
      +-------------------+
             [Ret]

"""

import board
import busio
import time
import digitalio
import microcontroller
import pwmio
import usb_cdc

# Raw frame tracing ([SHORT]/[LONG] headers and payload hex) on the console.
# Off by default: the UART loop reads at 1.2 Mbaud and the servo needs a pulse
# every 20 ms, so printing per bus frame costs frames and jitters the servo.
# The decoded `a=/v=` line is always printed - see process_packet().
DEBUG = False

# Capture frames that fail validation and dump their hex on the next status
# line, to tell a line error from a dropped byte: a single flipped bit leaves
# the frame structurally intact, while a missing byte makes the accumulator
# swallow the start of the following frame. Only an append happens in the
# frame path, so this barely perturbs what it measures.
DUMP_BAD_FRAMES = False
BAD_FRAME_KEEP = 6
bad_frames = []


def keep_bad_frame(buf, why):
    if DUMP_BAD_FRAMES and len(bad_frames) < BAD_FRAME_KEEP:
        bad_frames.append((why, bytes(buf)))

# Try board.LED first; if it doesn't exist, manually set GPIO15

led = digitalio.DigitalInOut(board.IO15)
led2a = digitalio.DigitalInOut(microcontroller.pin.GPIO6)

# Create a PWM output on GPIO39 at 1 kHz
pwm = pwmio.PWMOut(
    microcontroller.pin.GPIO39,       # Pin
    frequency=1000,   # 1 kHz
    duty_cycle=0      # Start at 0% duty
)

# Create a PWM output on GPIO39 at 1 kHz
pwmLed = pwmio.PWMOut(
    microcontroller.pin.GPIO7,       # Pin
    frequency=1000,   # 1 kHz
    duty_cycle=0      # Start at 0% duty
)

# ===== Servo on GPIO33 (standard hobby servo, 50 Hz) =====
SERVO_FREQ = 50            # 20 ms period
SERVO_MIN_US = 500         # pulse width at 0 degrees
SERVO_MAX_US = 2500        # pulse width at 180 degrees
SERVO_MAX_ANGLE = 180
SERVO_MOVE_TIME = 0.33      # seconds for a full move, eased in and out
SERVO_MOVE_NS = int(SERVO_MOVE_TIME * 1_000_000_000)

servo = pwmio.PWMOut(
    microcontroller.pin.GPIO33,
    frequency=SERVO_FREQ,
    duty_cycle=0      # no pulses until the first M620 R command
)

# Easing state: the command sets a target, servo_update() moves toward it
servo_angle = None         # current angle; None until the first command
servo_from = 0.0
servo_to = 0.0
servo_move_start = 0       # time.monotonic_ns() when the move began
servo_moving = False
# Diagnostics: longest gap between servo_update() calls, printed in [status]
servo_last_update_ns = 0
servo_max_gap_ns = 0

def servo_write(angle):
    pulse_us = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * angle / SERVO_MAX_ANGLE
    period_us = 1000000 / SERVO_FREQ
    servo.duty_cycle = int(pulse_us * 0xFFFF / period_us)

def set_servo(angle):
    """Start an eased move to angle. The first command jumps directly."""
    global servo_angle, servo_from, servo_to, servo_move_start, servo_moving
    angle = max(0, min(SERVO_MAX_ANGLE, angle))
    if servo_angle is None:
        servo_angle = float(angle)
        servo_write(servo_angle)
        return
    servo_from = servo_angle
    servo_to = float(angle)
    servo_move_start = time.monotonic_ns()
    servo_moving = True

def servo_update():
    """Advance the current move. Must be called at least every 20 ms (one
    servo pulse); the servo only samples one target per pulse."""
    global servo_angle, servo_moving, servo_last_update_ns, servo_max_gap_ns
    now_ns = time.monotonic_ns()
    if servo_last_update_ns:
        gap = now_ns - servo_last_update_ns
        if gap > servo_max_gap_ns:
            servo_max_gap_ns = gap
    servo_last_update_ns = now_ns
    if not servo_moving:
        return
    t = (now_ns - servo_move_start) / SERVO_MOVE_NS
    if t >= 1.0:
        t = 1.0
        servo_moving = False
    eased = t * t * (3.0 - 2.0 * t)   # smoothstep: ease in, ease out
    servo_angle = servo_from + (servo_to - servo_from) * eased
    servo_write(servo_angle)


# ===== Completion markers back to the host =====
# Values 0-200 are real laser power or servo angle. From MARKER_MIN up they
# are completion markers sent by bambucuts, which appends one `M620 R<code>`
# to a batch of G-code and waits to see it arrive here. This board decodes
# the AMS-Lite frame within a millisecond of the printer executing the
# command, where the printer's own MQTT progress report lags an executed M73
# by 1-2 seconds, so the marker confirms the batch far sooner.
#
# The marker range is checked before the address is dispatched, so a marker
# never reaches set_servo() or set_power(). That matters: set_servo() clamps
# to 180, so an unintercepted marker would park the servo at full deflection.
MARKER_MIN = 201

# Markers and log lines share the one CDC console. The ESP32-S2 has too few
# USB endpoints to add a second CDC interface alongside the CIRCUITPY drive,
# so there is nothing to be gained by trying.
#
# Sharing it means output must have exactly one writer. A marker written in
# two parts with a log line in between would reach the host as a corrupt
# line, so every line goes through one queue, drained only by flush_out().
marker_port = usb_cdc.console
# Never block the UART loop waiting for the host to drain its buffer: it
# reads at 1.2 Mbaud and the servo needs a pulse every 20 ms.
marker_port.timeout = 0
marker_port.write_timeout = 0

markers_sent = 0
markers_dropped = 0
marker_seq = 0

# Lines waiting for the console to accept them, each [bytes, keep]. The host
# may rely on this channel alone, so a marker must survive a momentarily
# full buffer rather than being thrown away.
out_backlog = []

# Cap the backlog so a host that stopped reading cannot exhaust memory. One
# marker per batch means this is never approached in normal use.
OUT_BACKLOG_MAX = 48


def emit(text, keep=False):
    """Queue one line for the console. Never blocks.

    keep marks a line that must not be shed under pressure: a marker is
    never dropped to make room for a log line.
    """
    global markers_dropped
    data = text.encode("ascii") if isinstance(text, str) else text
    if len(out_backlog) >= OUT_BACKLOG_MAX:
        shed = None
        for i in range(len(out_backlog)):
            if not out_backlog[i][1]:
                shed = i
                break
        if shed is None:
            # All markers: drop the oldest, which is the least useful now,
            # and count it so the status line shows the channel is failing.
            del out_backlog[0]
            markers_dropped += 1
        else:
            del out_backlog[shed]
    out_backlog.append([data, keep])
    flush_out()


def report_marker(address, value):
    """Queue one marker for the host. Never blocks, never silently drops one."""
    global marker_seq, markers_dropped
    marker_seq = (marker_seq + 1) % 1000000
    if not marker_port.connected:
        # Nobody is listening, so no job is waiting on this marker: the host
        # saw the port close and falls back to MQTT for anything in flight.
        # Queueing it would only deliver it stale later.
        markers_dropped += 1
        return
    emit("MARK %d %d %d %d\n" % (address, value,
                                 time.monotonic_ns() // 1000000, marker_seq),
         keep=True)


def flush_out():
    """Hand as much of the backlog to the console as it will take right now.

    write_timeout is 0, so a write returns immediately having accepted some
    or none of the bytes. A partial write leaves the remainder at the head
    of the queue to be retried, and since this is the only writer, nothing
    can be interleaved into the gap.
    """
    global markers_sent
    while out_backlog:
        if not marker_port.connected:
            return
        data, keep = out_backlog[0]
        try:
            written = marker_port.write(data) or 0
        except OSError:
            return          # console busy; try again on the next pass
        if written < len(data):
            out_backlog[0][0] = data[written:]
            return
        del out_backlog[0]
        if keep:
            markers_sent += 1




led.direction = digitalio.Direction.OUTPUT

led2a.direction = digitalio.Direction.OUTPUT

led2a.value=0

led.value = 1


# GPIO2 feeds GPIO1 (UART RX) through the 510 ohm resistor, and so sets the
# idle level of this single-ended tap off a differential pair. How hard it
# pulls matters: every corrupted bit observed on this link is 0 -> 1, which
# is what lows failing to reach the input threshold look like, i.e. a pull-up
# the bus driver cannot overcome.
#
#   "strong"   - GPIO2 driven high: a stiff 510 ohm pull-up (~6.5 mA)
#   "weak"     - GPIO2 an input with its internal pull-up, ~45k in series
#                with the 510 ohm, so a gentle pull-up
#   "off"      - GPIO2 high-Z: no bias at all
#   "down"     - GPIO2 driven low: a stiff 510 ohm pull-down, which pulls
#                lows firmly down at the cost of fighting the highs
#   "weakdown" - GPIO2 an input with its internal pull-down
#
# Measured on this rig: "strong" gives ~5% frame errors, "weak" corrupts
# every frame and "off" receives nothing at all, so the 510 ohm is carrying
# the signal level rather than merely biasing an idle line.
PULLUP_MODE = "strong"

pullup = digitalio.DigitalInOut(microcontroller.pin.GPIO2)
if PULLUP_MODE in ("strong", "down"):
    pullup.direction = digitalio.Direction.OUTPUT
    pullup.value = 1 if PULLUP_MODE == "strong" else 0
else:
    pullup.direction = digitalio.Direction.INPUT
    pullup.pull = (digitalio.Pull.UP if PULLUP_MODE == "weak"
                   else digitalio.Pull.DOWN if PULLUP_MODE == "weakdown"
                   else None)

# ===== CRC Helpers (no xor, no reverse) =====
def crc8_0x39(data: bytes, init: int = 0x66) -> int:
    poly = 0x39
    crc = init & 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc & 0xFF

def crc16_ccitt_0x1021(data: bytes, init: int = 0x913D) -> int:
    poly = 0x1021
    crc = init & 0xFFFF
    for b in data:
        crc ^= (b << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF  # transmitted little-endian (low byte first)

# ===== Single-bit repair of corrupted frames =====
# Every corrupted bit measured on this link is 0 -> 1 and almost all frames
# fail by exactly one bit, so most are recoverable. CRC16 is linear: flipping
# the bit d places from the end of the CRC region always changes the CRC by
# the same amount, so one table maps that difference back to d and a repair
# costs one extra CRC pass instead of a search.
#
# Each entry is the previous one advanced a single CRC step, because shifting
# the error pattern one bit multiplies it by x modulo the polynomial.
REPAIR_FRAMES = True
REPAIR_MAX_BITS = 8 * 40

# A repaired frame is only ever accepted when it decodes into the marker
# range. Searching until some candidate passes CRC is a good way to
# manufacture a plausible wrong value, and a wrong laser power is a safety
# problem where a wrong done marker is not: the host's timestamp guard
# rejects a stale marker, and a spurious one at worst completes a job early.
repairs_ok = 0
repairs_rejected = 0


def build_syndrome_table(maxbits=REPAIR_MAX_BITS):
    table = {}
    msg = bytearray(1)
    base = crc16_ccitt_0x1021(msg)
    msg[0] ^= 1
    syn = crc16_ccitt_0x1021(msg) ^ base
    for d in range(maxbits):
        if d:
            syn = ((syn << 1) ^ 0x1021) & 0xFFFF if syn & 0x8000 else (syn << 1) & 0xFFFF
        table[syn] = d
    return table


SYNDROME = build_syndrome_table() if REPAIR_FRAMES else {}


def repair_frame(buf, L, crc_calc, crc_seen):
    """Return a repaired copy if one flipped bit explains the CRC failure.

    Only frames decoding into the marker range are returned; anything else
    is left to fail, which is what keeps this away from laser power.
    """
    global repairs_ok, repairs_rejected
    d = SYNDROME.get(crc_calc ^ crc_seen)
    region_len = L - 2
    if d is None or d >= 8 * region_len:
        return None
    out = bytearray(buf)
    out[region_len - 1 - (d >> 3)] ^= 1 << (d & 7)
    if crc16_ccitt_0x1021(bytes(out[0:region_len])) != (out[L - 2] | (out[L - 1] << 8)):
        return None
    payload = bytes(out[5:region_len])
    if len(payload) < 3 or payload[0] * 4 + payload[1] < MARKER_MIN:
        repairs_rejected += 1
        return None
    repairs_ok += 1
    return out


# ===== UART Setup =====

rx_pin = microcontroller.pin.GPIO1

# Enable the internal pull-up on RX
rx = digitalio.DigitalInOut(rx_pin)
rx.direction = digitalio.Direction.INPUT
rx.pull = digitalio.Pull.UP
rx.deinit()  # release it so UART

uart = busio.UART(
    board.IO17, microcontroller.pin.GPIO1,
    baudrate=1228800,
    parity=busio.UART.Parity.EVEN,
    bits=8,
    stop=1,
    timeout=0.001,          # short/read 1 byte at a time
    receiver_buffer_size=4096
)

KNOWN=[
    "3dc51f5805000000303345303641343731393034333036000000000000f3a9",
    "3dc51f5805010000000000000000000000000000000000000000000000ea0e",
    "3dc51e612000000000000000000000000000000000000000000000006266",
    "3d05000013008e000f00031a02000000001be7",
    "3d05b201100023000f00090301fe44b4",
    "3d05b1011000f1000e00090301ff195d",
    "3d05ad0210004f000e00090301ff8321",
]

print("Bambu-Bus RX byte-by-byte @1228800 8E1...")

START = 0x3D

# Single accumulate state
frame = bytearray()
expected_len = 0
flag_byte = None

ok_frames = 0
crc_err = 0
len_err = 0
last_report = time.monotonic()


def reset():
    global frame, expected_len, flag_byte
    frame = bytearray()
    expected_len = 0
    flag_byte = None

def finish_if_complete():
    """Returns True if frame was complete and processed (and reset)."""
    global ok_frames, crc_err, len_err
    if expected_len and len(frame) == expected_len:
        # Process and reset
        #print("full frame", frame.hex())
        if process_frame(frame):
            ok_frames += 1
        reset()
        servo_update()   # keep the servo fed while a burst of frames is parsed
        flush_out()
        return True
    elif expected_len and len(frame) > expected_len:
        print("Bad frame", frame.hex())
        # Overshoot -> resync
        len_err += 1
        # Try to resync from current byte if it's a start byte, else full reset
        last = frame[-1]
        reset()
        if last == START:
            frame.append(last)
        return False
    return False

last_power_countdown = 0

def set_power(power):
    # Clamp: duty_cycle is 16-bit, so an unclamped power above 200 overflows
    # it and raises ValueError instead of setting anything.
    power = max(0, min(200, power))
    duty = int(power * 0xFFFF // 200)
    pwm.duty_cycle = duty
    pwmLed.duty_cycle = duty

def process_packet(payload):
    global last_power_countdown
    try:
        value = payload[0] * 4 + payload[1]
        address = payload[2]

        # The decoded command, always. Only non-KNOWN short frames reach here,
        # so this is one line per real command rather than per bus frame.
        emit(f"a={address}, v={value}\n")

        if value >= MARKER_MIN:
            # A host completion marker, not a power or angle. Report it and
            # leave the laser and servo exactly as they are.
            report_marker(address, value)
            return

        if address == 0:
            set_power(value)
            last_power_countdown = 60
        elif address == 1:
            set_servo(value)

    except Exception as e:
        print(e)



def process_frame(buf: bytearray) -> bool:
    """Validate and print a decoded frame. Returns True if valid."""
    global crc_err, len_err
    if len(buf) < 4:
        len_err += 1
        return False

    if buf.hex() in KNOWN:
        #print("ping")
        return True

    #print(f"frame {len(buf)}", buf[:100].hex())

    flag = buf[1]

    # ---------- Long header ----------
    if flag < 0x80:
        if len(buf) < 13:
            len_err += 1
            return False
        L = (buf[5] << 8) | buf[4]
        if L != len(buf):
            len_err += 1
            keep_bad_frame(buf, "len-long")
            print("Bad length (long):", len(buf), "expected:", L)
            return False

        if crc8_0x39(bytes(buf[0:6])) != buf[6]:
            crc_err += 1
            keep_bad_frame(buf, "crc8-long")
            print("Bad CRC8 (long)")
            return False

        crc_region = bytes(buf[0:L - 2])
        crc_calc = crc16_ccitt_0x1021(crc_region)
        crc_seen = buf[L - 2] | (buf[L - 1] << 8)
        if crc_calc != crc_seen:
            crc_err += 1
            keep_bad_frame(buf, "crc16-long")
            print("Bad CRC16 (long)")
            return False

        seq = (buf[2] << 8) | buf[3]
        tgt = (buf[7] << 8) | buf[8]
        src = (buf[9] << 8) | buf[10]
        payload = bytes(buf[11:L - 2])
        if DEBUG and payload.hex(" ") not in ["03 01 fe","03 01 ff"]:
            print(f"[LONG] seq={seq} src=0x{src:04X}->dst=0x{tgt:04X} len={L} payload={payload.hex(' ')}")

        return True

    # ---------- Short header ----------
    else:
        if len(buf) < 7:
            len_err += 1
            return False
        L = buf[2]
        if L != len(buf):
            len_err += 1
            keep_bad_frame(buf, "len-short")
            print("Bad length (short):", len(buf), "expected:", L)
            return False
        c8 = crc8_0x39(bytes(buf[0:3]))
        if c8 != buf[3]:
            crc_err += 1
            keep_bad_frame(buf, "crc8-short")
            print(f"Bad CRC8 (short) {c8} != {buf[3]}")
            return False

        crc_region = bytes(buf[0:L - 2])
        crc_calc = crc16_ccitt_0x1021(crc_region)
        crc_seen = buf[L - 2] | (buf[L - 1] << 8)
        if crc_calc != crc_seen:
            repaired = repair_frame(buf, L, crc_calc, crc_seen) if REPAIR_FRAMES else None
            if repaired is None:
                crc_err += 1
                keep_bad_frame(buf, "crc16-short")
                print(f"Bad CRC16 (short) {crc_calc} != {crc_seen}")
                return False
            buf = repaired

        pkt_type = buf[4]
        payload = bytes(buf[5:L - 2])
        if DUMP_BAD_FRAMES and len(payload) >= 3 and payload[0] * 4 + payload[1] >= MARKER_MIN:
            # Capture accepted marker frames too, so the host has an exact
            # template to diff a corrupted one against.
            keep_bad_frame(buf, "good")
        if DEBUG:
            print(f"[SHORT] type=0x{pkt_type:02X} len={L} payload={payload.hex(' ')}")
        process_packet(payload)
        return True

# ===== Main loop: read ONE BYTE at a time =====
reset()

print("Hello")
ticks = 0
def tick():
    global ticks
    global last_power_countdown
    ticks += 1
    led.value = (ticks & 1) == 0
    if last_power_countdown > 0:
        last_power_countdown -=1
        if last_power_countdown == 0:
            set_power(0)

    pass

while True:
    b = uart.read(100)
    servo_update()
    flush_out()
    now = time.monotonic()
    if now - last_report >= 1:
        emit(f"[status] ok={ok_frames} crc_err={crc_err} len_err={len_err} buf={len(frame)} "
             f"servo_gap={servo_max_gap_ns // 1_000_000}ms "
             f"marks={markers_sent}/{markers_dropped}/{len(out_backlog)} "
             f"fixed={repairs_ok}/{repairs_rejected} "
             f"host={'up' if marker_port.connected else 'down'}\n")
        while bad_frames:
            why, raw = bad_frames.pop(0)
            emit("BADFRAME %s %s\n" % (why, raw.hex()))
        servo_max_gap_ns = 0
        last_report = now
        tick()

    if not b:
        continue

    for val in b:
        if not frame:
            # Waiting for start byte
            if val == START:
                frame.append(val)
            # else ignore
            continue

        # We have started a frame; keep accumulating
        frame.append(val)

        # Capture flag early
        if len(frame) >= 2:
            flag_byte = frame[1]

            if flag_byte < 0x80:
                # Long: need bytes up through length field (index 5)
                if len(frame) >= 6:
                    L = (frame[5] << 8) | frame[4]
                    if L < 7 or L > 5000:
                        # invalid; resync: if current is start, keep it
                        print(f"Invalid L={L}",frame)
                        bad = frame[-1]
                        reset()
                        if bad == START:
                            frame.append(bad)
                        continue
                    expected_len = L
            else:
                # Short: length at index 2
                if len(frame) >= 3:
                    L = frame[2]
                    if L < 7 or L > 255:
                        print("Invalid short",frame)
                        bad = frame[-1]
                        reset()
                        if bad == START:
                            frame.append(bad)
                        continue
                    expected_len = L

        # If we now have the full frame, finish immediately (no extra read)
        finished = finish_if_complete()
        if finished:
            # already reset; ready for next byte
            continue
