# boot.py
"""
Wemos ESP32 S2 Mini board 

https://www.wemos.cc/en/latest/s2/s2_mini.html

GPIO
 
GPIO 1  = Uart RX. TOP RIGH AMS Lite PIN. Tx+ on the AMS Lite port on A1 mini 
GPIO 2  = pull up, connect 510ohm resitor to GPIO1 

GPIO 6 and 7  = LED + on 7, - on 6. Follow light outout 

GPIP 39 = PWM output for laser power.

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

# Try board.LED first; if it doesn't exist, manually set GPIO15

led = digitalio.DigitalInOut(board.D15)
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




led.direction = digitalio.Direction.OUTPUT

led2a.direction = digitalio.Direction.OUTPUT

led2a.value=0

led.value = 1 


pullup = digitalio.DigitalInOut(microcontroller.pin.GPIO2)
pullup.direction = digitalio.Direction.OUTPUT
pullup.value=1

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

# ===== UART Setup =====

rx_pin = microcontroller.pin.GPIO1

# Enable the internal pull-up on RX
rx = digitalio.DigitalInOut(rx_pin)
rx.direction = digitalio.Direction.INPUT
rx.pull = digitalio.Pull.UP
rx.deinit()  # release it so UART

uart = busio.UART(
    board.D17, microcontroller.pin.GPIO1,
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
    pwm.duty_cycle = int(power * 0xFFFF // 200);
    pwmLed.duty_cycle = int(power * 0xFFFF // 200);
    
def process_packet(payload):
    global last_power_countdown
    try:
        print("Payload",payload.hex(' '));
        value = payload[0] * 4 + payload[1]
        address = payload[2]
        
        print(f"a={address}, v={value}")
        
        if address == 0:
            set_power(value)
            last_power_countdown = 60
            
    except e:
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
            print("Bad length (long):", len(buf), "expected:", L)
            return False

        if crc8_0x39(bytes(buf[0:6])) != buf[6]:
            crc_err += 1
            print("Bad CRC8 (long)")
            return False

        crc_region = bytes(buf[0:L - 2])
        crc_calc = crc16_ccitt_0x1021(crc_region)
        crc_seen = buf[L - 2] | (buf[L - 1] << 8)
        if crc_calc != crc_seen:
            crc_err += 1
            print("Bad CRC16 (long)")
            return False

        seq = (buf[2] << 8) | buf[3]
        tgt = (buf[7] << 8) | buf[8]
        src = (buf[9] << 8) | buf[10]
        payload = bytes(buf[11:L - 2])
        if payload.hex(" ") not in ["03 01 fe","03 01 ff"]:
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
            print("Bad length (short):", len(buf), "expected:", L)
            return False
        c8 = crc8_0x39(bytes(buf[0:3]))
        if c8 != buf[3]:
            crc_err += 1
            print(f"Bad CRC8 (short) {c8} != {buf[3]}")
            return False

        crc_region = bytes(buf[0:L - 2])
        crc_calc = crc16_ccitt_0x1021(crc_region)
        crc_seen = buf[L - 2] | (buf[L - 1] << 8)
        if crc_calc != crc_seen:
            crc_err += 1
            print(f"Bad CRC16 (short) {crc_calc} != {crc_seen}")
            return False

        pkt_type = buf[4]
        payload = bytes(buf[5:L - 2])
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
    now = time.monotonic()
    if now - last_report >= 1:
        print(f"[status] ok={ok_frames} crc_err={crc_err} len_err={len_err} buf={len(frame)}")
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
