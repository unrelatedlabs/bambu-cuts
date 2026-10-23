"""Exercise dual-marker completion without a printer or a board."""
import sys, time, types
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import bambucuts.webui.app
# webui/__init__ re-exports the Flask object as `app`, shadowing the module.
A = sys.modules['bambucuts.webui.app']

fail = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail and not cond else ''}")
    if not cond: fail.append(label)

def reset():
    A.state['direct_jobs'] = {}
    A.state['active_direct_job_ids'] = []
    A.state['direct_job'] = {'active': False, 'status': 'idle'}
    A.state['mqtt_status'] = {'connected': True, 'error': None,
                              'last_update': time.time(), 'print': {'mc_percent': 7}}
    A.head.reset()

class FakeLink:
    """Stands in for MarkerLink; only status()['connected'] is consulted."""
    def __init__(self, connected): self.connected = connected
    def status(self): return {'connected': self.connected}

def start_job(gcode="G1 X10 Y10 F3000", wait_for_motion=True):
    text, info = A.add_completion_marker(gcode, wait_for_motion=wait_for_motion)
    job = A.begin_direct_job(info)
    A.mark_direct_job_queued(job['id'], info['command_count'], [])
    return text, info, job['id']

def job(jid):
    return A.state['direct_jobs'][jid]

print("board connected: side marker only")
reset(); A.marker_link = FakeLink(True)
text, info, jid = start_job()
check("M400 present", "M400" in text)
check("side marker is an M620/M621 pair", info['side_marker_line'].startswith("M620 R")
      and "M621 R" in info['side_marker_line'], str(info['side_marker_line']))
check("side code in 201-251", 201 <= int(info['side_marker_values'][0]) <= 251, info['side_marker_values'][0])

print("\nunmatched code leaves the job alone; the watchdog covers it")
A.update_direct_job_from_marker(999, time.time(), address=1)
check("job still waiting", job(jid)['status'] == 'waiting', job(jid)['status'])

print("\nstale marker (predating the job) is rejected")
reset(); A.marker_link = FakeLink(True)
_, info, jid = start_job()
A.update_direct_job_from_marker(int(info['side_marker_values'][0]), time.time() - 600, address=1)
check("job still waiting", job(jid)['status'] == 'waiting', job(jid)['status'])

print("\nboard absent: no M620 emitted, MQTT-only as before")
reset(); A.marker_link = FakeLink(False)
text, info, jid = start_job()
check("no side marker", info['side_marker_values'] is None, str(info['side_marker_values']))
check("no M620 in the batch", "M620" not in text)
check("M73 still there", info['marker_line'] in text)
A.state['mqtt_status']['print'] = {'mc_percent': int(info['marker_value']), 'mc_remaining_time': 0}
A.state['mqtt_status']['last_update'] = time.time()
_, prog = A.current_mqtt_progress()
A.update_direct_job_from_mqtt(prog)
check("completes on MQTT", job(jid)['status'] == 'complete', job(jid)['status'])

print("\nqueued jobs: a later marker completes the ones ahead of it")
reset(); A.marker_link = FakeLink(True)
_, i1, j1 = start_job("G1 X20 Y20")
_, i2, j2 = start_job("G1 X30 Y30")
check("distinct side codes", set(i1['side_marker_values']).isdisjoint(i2['side_marker_values']),
      f"{i1['side_marker_values']} vs {i2['side_marker_values']}")
A.update_direct_job_from_marker(int(i2['side_marker_values'][0]), time.time(), address=1)
check("second job complete", job(j2)['status'] == 'complete', job(j2)['status'])
check("first job inferred complete", job(j1)['status'] == 'complete' and job(j1)['detect_inferred'],
      f"{job(j1)['status']} inferred={job(j1).get('detect_inferred')}")

print("\nsent_count excludes our markers, keeps the user's own M620")
reset(); A.marker_link = FakeLink(True)
user = "M620 P150 M621 P150 M400\nG1 X5 Y5\nM73 P50 R30"
text, info = A.add_completion_marker(user)
lines = [l for _, l in A.extract_executable_gcode(text)]
markers = set(info['marker_lines'])
counted = [l for l in lines if l not in markers]
print("      counted: " + " | ".join(counted))
check("user's laser M620 counted", "M620 P150 M621 P150 M400" in counted)
check("user's own M73 counted", "M73 P50 R30" in counted)
check("our side marker not counted", info['side_marker_line'] not in counted)
check("our M73 not counted", info['marker_line'] not in counted)
check("count equals the user's three lines", len(counted) == 3, str(len(counted)))

print("\nmarker code allocation cycles the whole range")
blocks = [A.next_side_marker_block() for _ in range(40)]
check("blocks are consecutive runs", all(b == list(range(b[0], b[0] + len(b))) for b in blocks))
check("block width matches the setting",
      all(len(b) == A.SIDE_MARKER_REPEATS for b in blocks), str(blocks[0]))
check("codes stay inside 201-251",
      all(201 <= c <= 251 for b in blocks for c in b), str(blocks[:3]))
check("consecutive blocks never share a code",
      all(set(a).isdisjoint(b) for a, b in zip(blocks, blocks[1:])), str(blocks[:4]))

print("\n" + ("FAILURES: " + ", ".join(fail) if fail else "all host checks passed"))
sys.exit(1 if fail else 0)
