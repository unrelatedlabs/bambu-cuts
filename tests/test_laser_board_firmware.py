"""Run laser board/code.py under mocked CircuitPython modules."""
import os, sys, types, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

class Pin:
    def __init__(self, n): self.name = n
class PinBank:
    def __getattr__(self, n): return Pin(n)

board = types.ModuleType("board"); board.IO15 = Pin("IO15"); board.IO17 = Pin("IO17")
microcontroller = types.ModuleType("microcontroller"); microcontroller.pin = PinBank()

class PWMOut:
    def __init__(self, pin, frequency=0, duty_cycle=0):
        self.pin, self.frequency, self._duty = pin, frequency, duty_cycle
    @property
    def duty_cycle(self): return self._duty
    @duty_cycle.setter
    def duty_cycle(self, v):
        if not 0 <= v <= 0xFFFF:
            raise ValueError(f"duty_cycle must be 0-65535, got {v}")
        self._duty = v
pwmio = types.ModuleType("pwmio"); pwmio.PWMOut = PWMOut

class DigitalInOut:
    def __init__(self, pin): self.pin, self.value, self.direction, self.pull = pin, 0, None, None
    def deinit(self): pass
digitalio = types.ModuleType("digitalio")
digitalio.DigitalInOut = DigitalInOut
digitalio.Direction = types.SimpleNamespace(OUTPUT="out", INPUT="in")
digitalio.Pull = types.SimpleNamespace(UP="up")

class UART:
    Parity = types.SimpleNamespace(EVEN="even")
    def __init__(self, *a, **k): self.queue = []
    def read(self, n): return self.queue.pop(0) if self.queue else None
busio = types.ModuleType("busio"); busio.UART = UART

class CDC:
    """Console mock with a settable write capacity, to exercise backpressure."""
    def __init__(self):
        self.written, self.connected, self.timeout, self.write_timeout = b"", True, None, None
        self.accept = None      # None = accept everything; int = bytes per write
        self.fail = False       # raise OSError on write
    def write(self, data):
        if self.fail: raise OSError("busy")
        n = len(data) if self.accept is None else min(self.accept, len(data))
        self.written += data[:n]
        return n
usb_cdc = types.ModuleType("usb_cdc"); usb_cdc.console = CDC(); usb_cdc.data = None

for name, mod in [("board", board), ("microcontroller", microcontroller), ("pwmio", pwmio),
                  ("digitalio", digitalio), ("busio", busio), ("usb_cdc", usb_cdc)]:
    sys.modules[name] = mod

src = open("laser board/code.py", encoding="utf-8").read()
# Strip the infinite main loop; everything above it is what we exercise.
src = src.split("while True:")[0]
ns = {"__name__": "__main__"}
exec(compile(src, "code.py", "exec"), ns)

fail = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail and not cond else ''}")
    if not cond: fail.append(label)

pwm, servo, cdc = ns["pwm"], ns["servo"], usb_cdc.console
process_packet, set_power, flush_out = ns["process_packet"], ns["set_power"], ns["flush_out"]
backlog = ns["out_backlog"]

def packet(value, address):
    """Printer's encoding: payload[0]*4 + payload[1] == value."""
    return bytes([value // 4, value % 4, address, 0, 0, 0])

def marks():
    return [l for l in cdc.written.decode().splitlines() if l.startswith("MARK")]

def clear():
    cdc.written = b""; cdc.accept = None; cdc.fail = False; backlog.clear()

print("value encoding (what R argument produces which decoded value)")
check("R201 decodes to 201", packet(201, 1)[0] * 4 + packet(201, 1)[1] == 201)
check("R251 decodes to 251", packet(251, 1)[0] * 4 + packet(251, 1)[1] == 251)
check("payload[1] stays 0-3 across 201-251",
      all(packet(v, 1)[1] <= 3 for v in range(201, 252)))

print("\nmarker interception on the servo channel")
clear()
servo_before, pwm_before = servo.duty_cycle, pwm.duty_cycle
for code in (201, 225, 251):
    process_packet(packet(code, 1))
check("servo untouched by markers", servo.duty_cycle == servo_before,
      f"{servo_before} -> {servo.duty_cycle}")
check("laser untouched by markers", pwm.duty_cycle == pwm_before)
check("one MARK line per marker", len(marks()) == 3, repr(cdc.written))
check("MARK lines carry address and value",
      [l.split()[:3] for l in marks()] == [["MARK","1","201"],["MARK","1","225"],["MARK","1","251"]],
      repr(marks()))
seqs = [int(l.split()[4]) for l in marks()]
check("sequence numbers increment", seqs == [seqs[0], seqs[0]+1, seqs[0]+2], str(seqs))
check("decoded command is logged too", "a=1, v=201" in cdc.written.decode())

print("\nmarkers on the laser channel are intercepted too")
clear()
process_packet(packet(210, 0))
check("laser channel marker reported", marks() and marks()[0].startswith("MARK 0 210"), repr(marks()))
check("laser still untouched", pwm.duty_cycle == pwm_before)

print("\nreal commands still work")
process_packet(packet(100, 0))
check("P100 sets laser to half", pwm.duty_cycle == int(100 * 0xFFFF // 200), str(pwm.duty_cycle))
process_packet(packet(200, 0))
check("P200 sets laser to full", pwm.duty_cycle == 0xFFFF, str(pwm.duty_cycle))
process_packet(packet(90, 1))
check("R90 moves the servo", ns["servo_angle"] == 90.0, str(ns["servo_angle"]))

print("\nset_power clamp (the pre-existing 16-bit overflow)")
try:
    set_power(255); ok = pwm.duty_cycle == 0xFFFF
except ValueError as e:
    ok = False; print(f"      raised {e}")
check("set_power(255) clamps instead of raising", ok)
try:
    set_power(-5); ok2 = pwm.duty_cycle == 0
except ValueError:
    ok2 = False
check("set_power(-5) clamps to 0", ok2)

print("\nhost gone: marker dropped rather than delivered stale later")
clear()
cdc.connected = False
before = ns["markers_dropped"]
process_packet(packet(205, 1))
check("counted as dropped", ns["markers_dropped"] == before + 1)
check("no marker queued", not any(e[1] for e in backlog), str(backlog))
cdc.connected = True
clear()

print("\nbusy console: marker is queued and retried, never lost")
cdc.fail = True
process_packet(packet(211, 1))
check("marker queued while the console refuses", any(e[1] for e in backlog), str(backlog))
check("nothing written yet", marks() == [])
cdc.fail = False
flush_out()
check("delivered on the next flush", marks() and marks()[0].startswith("MARK 1 211"), repr(marks()))
check("backlog drained", backlog == [], str(backlog))

print("\npartial write: remainder retried, line not split or duplicated")
clear()
cdc.accept = 5
process_packet(packet(212, 1))
check("partial write leaves a remainder", backlog != [], str(backlog))
cdc.accept = None
flush_out()
got = cdc.written.decode()
check("exactly one complete MARK line", len(marks()) == 1
      and marks()[0].startswith("MARK 1 212"), repr(got))
check("no log line spliced into it", "MARKa=" not in got.replace("\n", "") , repr(got))
check("backlog drained", backlog == [], str(backlog))

print("\nbacklog pressure: log lines are shed, markers are kept")
clear()
cdc.fail = True
for _ in range(ns["OUT_BACKLOG_MAX"]):
    ns["emit"]("[status] filler\n")
process_packet(packet(213, 1))
check("backlog capped", len(backlog) == ns["OUT_BACKLOG_MAX"], str(len(backlog)))
check("the marker survived", any(e[1] for e in backlog), str([e[1] for e in backlog]))
cdc.fail = False
flush_out()
check("marker delivered once the console frees up",
      marks() and marks()[0].startswith("MARK 1 213"), repr(marks()))
clear()

print("\n" + ("FAILURES: " + ", ".join(fail) if fail else "all firmware checks passed"))
sys.exit(1 if fail else 0)
