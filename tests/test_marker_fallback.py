"""Side-channel-only markers plus lazy MQTT fallback, no hardware."""
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

print("board connected: no M73 goes out at all")
reset()
text, info, jid = start()
tail = text.strip().split("\n")[1:]
print("      " + " | ".join(tail))
check("M620 side marker present", any("M620" in l for l in tail))
check("no M73 anywhere in the batch", "M73" not in text, text)
check("job has no MQTT marker yet", job(jid)['marker_value'] is None)
check("fallback not yet sent", job(jid)['fallback_sent'] is False)

print("\nhappy path: side marker completes it, still no M73 ever sent")
A.update_direct_job_from_marker(int(info['side_marker_values'][0]), time.time(), address=1)
check("complete via side channel", job(jid)['status'] == 'complete'
      and job(jid)['detect_source'] == 'side-channel', str(job(jid).get('detect_source')))
check("nothing extra sent to the printer", sent == [], str(sent))

print("\nport closes mid-job: fallback injected, job completes on MQTT")
reset()
_, info, jid = start()
A._handle_marker_lost('marker port closed')
check("M400 + M73 injected", len(sent) == 1 and sent[0][0] == 'M400'
      and sent[0][1].startswith('M73 P'), str(sent))
check("fallback recorded on the job", job(jid)['fallback_sent'] is True
      and job(jid)['marker_value'] is not None)
check("reason recorded", job(jid).get('fallback_reason') == 'marker port closed',
      str(job(jid).get('fallback_reason')))
mqtt_says(job(jid)['marker_value'])
check("completes on the injected marker", job(jid)['status'] == 'complete'
      and job(jid)['detect_source'] == 'mqtt', str(job(jid).get('detect_source')))

print("\nsequence gap triggers the same fallback")
reset()
_, info, jid = start()
A._handle_marker_lost('sequence gap: 1 marker(s) lost between seq 4 and 6')
check("fallback injected on a gap", len(sent) == 1 and job(jid)['fallback_sent'])

print("\nno double injection when triggered twice")
reset()
_, info, jid = start()
A._handle_marker_lost('port closed')
A._handle_marker_lost('port closed again')
A.check_side_channel_watchdog()
check("exactly one fallback sent", len(sent) == 1, str(sent))

print("\nwatchdog injects once the marker is overdue")
reset()
_, info, jid = start()
A.check_side_channel_watchdog()
check("nothing injected while still within grace", sent == [], str(sent))
with A.direct_job_lock:                      # backdate past estimate + grace
    j = A.state['direct_jobs'][jid]
    j['queued_at'] -= (j['estimated_seconds'] + A.SIDE_CHANNEL_GRACE_SECONDS + 0.5)
A.check_side_channel_watchdog()
check("injected once overdue", len(sent) == 1 and sent[0][1].startswith('M73 P'), str(sent))
mqtt_says(job(jid)['marker_value'])
check("job completes", job(jid)['status'] == 'complete', job(jid)['status'])

print("\nseveral jobs in flight: one injected marker covers them all")
reset()
_, i1, j1 = start("G1 X20 Y20")
_, i2, j2 = start("G1 X30 Y30")
A._handle_marker_lost('port closed')
check("one marker for both jobs", len(sent) == 1, str(sent))
check("both claimed", job(j1)['fallback_sent'] and job(j2)['fallback_sent'])
check("both share the marker value", job(j1)['marker_value'] == job(j2)['marker_value'])
mqtt_says(job(j2)['marker_value'])
check("both complete", job(j1)['status'] == 'complete' and job(j2)['status'] == 'complete',
      f"{job(j1)['status']}/{job(j2)['status']}")

print("\nboard absent: M73 goes out with the batch exactly as before")
reset(board=False)
text, info, jid = start()
check("M73 in the batch", info['marker_line'] and info['marker_line'] in text)
check("no M620", "M620" not in text)
check("already covered, watchdog is a no-op", job(jid)['fallback_sent'] is True)
A.check_side_channel_watchdog()
check("nothing injected", sent == [], str(sent))
mqtt_says(info['marker_value'])
check("completes on MQTT", job(jid)['status'] == 'complete', job(jid)['status'])

print("\nprinter offline: injection declines rather than pretending")
reset()
_, info, jid = start()
A.state['printer_connected'] = False
check("no marker sent", A.inject_mqtt_fallback('port closed') is None and sent == [], str(sent))
check("job left uncovered for the stall timeout", job(jid)['fallback_sent'] is False)

print("\n" + ("FAILURES: " + ", ".join(fail) if fail else "all fallback checks passed"))
sys.exit(1 if fail else 0)
