"""Every batch carries both done markers; whichever arrives first completes it. No hardware."""
import sys, time
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import bambucuts.webui.app
A = sys.modules['bambucuts.webui.app']

fail = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail and not cond else ''}")
    if not cond: fail.append(label)

sent = []
A.send_gcode_lines_to_printer = lambda lines: (sent.append(list(lines)), True)[1]
A.send_gcode_to_printer = lambda line: (sent.append([line]), True)[1]

class Link:
    def __init__(self, c=True): self.c = c
    def status(self): return {'connected': self.c}

def reset(board=True):
    sent.clear()
    A.state['direct_jobs'] = {}; A.state['active_direct_job_ids'] = []
    A.state['direct_job'] = {'active': False, 'status': 'idle'}
    A.state['printer_connected'] = True
    A.state['mqtt_status'] = {'connected': True, 'error': None,
                              'last_update': time.time(), 'print': {'mc_percent': 30}}
    A.head.reset(); A.marker_link = Link(board)

def start(gcode="G1 X10 Y10 F3000"):
    text, info = A.add_completion_marker(gcode)
    job = A.begin_direct_job(info)
    A.mark_direct_job_queued(job['id'], info['command_count'], [])
    return text, info, job['id']

def job(jid): return A.state['direct_jobs'][jid]

def mqtt_says(percent):
    A.state['mqtt_status']['print'] = {'mc_percent': int(percent), 'mc_remaining_time': 0}
    A.state['mqtt_status']['last_update'] = time.time()
    _, prog = A.current_mqtt_progress()
    A.update_direct_job_from_mqtt(prog)

print("board connected: both markers go out with the batch")
reset()
text, info, jid = start()
tail = text.strip().split("\n")[1:]
print("      " + " | ".join(tail))
code = [strip for strip in (A.strip_inline_comment(l) for l in tail) if strip]
check("M400, then the serial marker, then the MQTT marker",
      code[0] == 'M400' and code[1].startswith('M620 R') and code[2].startswith('M73 P'), str(code))
check("job expects both", job(jid)['side_marker_values'] and job(jid)['marker_value'] is not None)

print("\nserial marker arrives first: completes on it, nothing else sent")
A.update_direct_job_from_marker(int(info['side_marker_values'][0]), time.time(), address=1)
check("complete via side channel", job(jid)['status'] == 'complete'
      and job(jid)['detect_source'] == 'side-channel', str(job(jid).get('detect_source')))
check("nothing extra sent to the printer", sent == [], str(sent))

print("\nserial marker lost: the M73 in the batch completes it")
reset()
_, info, jid = start()
mqtt_says(job(jid)['marker_value'])
check("complete via MQTT", job(jid)['status'] == 'complete'
      and job(jid)['detect_source'] == 'mqtt', str(job(jid).get('detect_source')))
check("nothing injected", sent == [], str(sent))

print("\nport closes mid-job: nothing to inject, the M73 is already on its way")
reset()
_, info, jid = start()
A._handle_marker_lost('marker port closed')
check("nothing sent", sent == [], str(sent))
mqtt_says(job(jid)['marker_value'])
check("completes on MQTT", job(jid)['status'] == 'complete', job(jid)['status'])

print("\nseveral jobs in flight: each has its own percent")
reset()
_, i1, j1 = start("G1 X20 Y20")
_, i2, j2 = start("G1 X30 Y30")
check("different percents", job(j1)['marker_value'] != job(j2)['marker_value'],
      f"{job(j1)['marker_value']} {job(j2)['marker_value']}")
mqtt_says(job(j2)['marker_value'])
check("the later marker completes both", job(j1)['status'] == 'complete' and job(j2)['status'] == 'complete',
      f"{job(j1)['status']}/{job(j2)['status']}")

print("\nboard absent: only the M73")
reset(board=False)
text, info, jid = start()
check("M73 in the batch", info['marker_line'] and info['marker_line'] in text)
check("no M620", "M620" not in text)
mqtt_says(info['marker_value'])
check("completes on MQTT", job(jid)['status'] == 'complete', job(jid)['status'])

print("\npercents cycle through 1-99 and never repeat back to back")
seen = [A.next_marker_percent() for _ in range(250)]
check("all within 1-99", all(1 <= p <= 99 for p in seen), str(sorted(set(seen))[:3]))
check("no repeats back to back", all(a != b for a, b in zip(seen, seen[1:])))

print("\n" + ("FAILURES: " + ", ".join(fail) if fail else "all marker checks passed"))
sys.exit(1 if fail else 0)
