"""MarkerLink against a pty: parsing, sequence gaps, port loss, shutdown."""
import os, pty, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from bambucuts.marker_link import MarkerLink, parse_marker

fail = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail and not cond else ''}")
    if not cond: fail.append(label)

print("parse_marker")
check("full line", parse_marker('MARK 1 201 1234 7') ==
      {'address': 1, 'value': 201, 'board_ms': 1234, 'seq': 7})
check("older board without seq", parse_marker('MARK 1 201 1234')['seq'] is None)
check("board status line is not a marker", parse_marker('[status] ok=5 marks=1/0/0') is None)
check("malformed is not a marker", parse_marker('MARK x y') is None)

master, slave = pty.openpty()
dev = os.ttyname(slave)
got, lost = [], []
link = MarkerLink(device=dev,
                  on_marker=lambda a, v, at: got.append((a, v)),
                  on_lost=lambda reason: lost.append(reason))
assert link.start()
time.sleep(0.4)
check("connects", link.status()['connected'], str(link.status()['error']))

def feed(*lines):
    for line in lines:
        os.write(master, line.encode() if isinstance(line, str) else line)
        time.sleep(0.05)
    time.sleep(0.25)

print("\nreading markers, ignoring the board's other output")
feed('[status] ok=12 crc_err=0 len_err=0 buf=0 servo_gap=3ms marks=0/0/0 host=up\n',
     'MARK 1 201 1000 1\n', 'a=1, v=201\n', 'MARK 1 202 1050 2\n', 'MARK 1 203 1100 3\n')
check("markers delivered in order", got == [(1, 201), (1, 202), (1, 203)], str(got))
check("no loss reported", lost == [], str(lost))
st = link.status()
check("counters", st['marker_count'] == 3 and st['last_seq'] == 3
      and st['gap_count'] == 0, str({k: st[k] for k in ('marker_count','last_seq','gap_count')}))

print("\nsequence gap is detected and reported as loss")
feed('MARK 1 206 1200 6\n')          # 4 and 5 never arrived
check("marker still delivered", got[-1] == (1, 206), str(got[-1]))
check("loss reported once", len(lost) == 1, str(lost))
check("reason names the gap", lost and '2 marker(s) lost' in lost[0], str(lost))
st = link.status()
check("gap counted", st['gap_count'] == 1 and st['lost_estimate'] == 2,
      str({k: st[k] for k in ('gap_count', 'lost_estimate')}))

print("\nconsecutive markers after a gap are clean again")
lost.clear()
feed('MARK 1 207 1250 7\n', 'MARK 1 208 1300 8\n')
check("no further loss", lost == [], str(lost))

print("\nboard restart (sequence goes backwards) is not counted as loss")
lost.clear()
feed('MARK 1 209 10 1\n')
check("not treated as loss", lost == [] and link.status()['gap_count'] == 1, str(lost))

print("\nbus health is read from the board's status line")
feed('[status] ok=100 crc_err=2 len_err=0 buf=0 servo_gap=3ms marks=1/0/0 '
     'fixed=0/0 orphan=0/0 host=up\n')
time.sleep(1.1)
feed('[status] ok=116 crc_err=2 len_err=0 buf=0 servo_gap=3ms marks=1/0/0 '
     'fixed=0/0 orphan=0/0 host=up\n')
st = link.status()
check("bus reported alive when frames are flowing", st['bus_alive'] is True,
      str({k: st.get(k) for k in ('bus_alive', 'bus_fps')}))
check("frame rate measured", (st['bus_fps'] or 0) > 1.0, str(st.get('bus_fps')))

feed('[status] ok=116 crc_err=2 len_err=0 buf=0 servo_gap=3ms marks=1/0/0 '
     'fixed=0/0 orphan=2767/1 host=up\n')
time.sleep(1.1)
feed('[status] ok=116 crc_err=2 len_err=0 buf=0 servo_gap=3ms marks=1/0/0 '
     'fixed=0/0 orphan=2767/1 host=up\n')
st = link.status()
check("bus reported dead when ok stops climbing", st['bus_alive'] is False,
      str({k: st.get(k) for k in ('bus_alive', 'bus_fps')}))
check("and says what to check", 'signal wire' in (st.get('bus_error') or ''),
      str(st.get('bus_error')))
check("orphan bytes surfaced", st.get('orphan_bytes') == 2767, str(st.get('orphan_bytes')))

print("\nport loss reports loss and keeps the reader alive for reconnect")
lost.clear()
os.close(master); os.close(slave)
time.sleep(0.8)
check("port loss reported", len(lost) >= 1, str(lost))
check("disconnected", not link.status()['connected'])
check("reader still trying", link.alive())
check("sequence tracking reset", link.status()['last_seq'] is None)

link.stop()
check("stops cleanly", not link.alive())

print("\n" + ("FAILURES: " + ", ".join(fail) if fail else "all marker link checks passed"))
sys.exit(1 if fail else 0)
