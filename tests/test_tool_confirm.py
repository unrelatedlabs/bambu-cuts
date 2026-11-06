"""G-code with tool commands goes out as a run of batches, each with its done markers;
tool batches are confirmed by the board's echo and resent when only MQTT saw them."""
import os, sys, time, threading, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import bambucuts.webui.app
A = sys.modules['bambucuts.webui.app']

fail = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail and not cond else ''}")
    if not cond: fail.append(label)

LOG = os.path.join(tempfile.mkdtemp(), "tool.log")
A.tool_log_path = lambda: LOG
A.DIRECT_JOB_MIN_TIMEOUT_SECONDS = 1.0
A.DIRECT_JOB_TIMEOUT_PAD_SECONDS = 1.0

class Link:
    def status(self): return {'connected': True}

# The fake printer: every publish is recorded; every M620 reaches the "board"
# and is reported back unless the next frame is set to be lost; M73 shows up
# in MQTT a little later.
sent = []
lose = []          # True per upcoming M620 frame (command or marker) to drop
mqtt_dead = [False]

def printer(lines):
    sent.append(list(lines))
    for line in lines:
        m = A.TOOL_LINE.match(line)
        if m:
            if not (lose and lose.pop(0)):
                threading.Timer(0.02, A._handle_marker,
                                (A.TOOL_ADDRESS[m.group(1).upper()], int(m.group(2)), time.time() + 0.02)).start()
        elif line.startswith("M73") and not mqtt_dead[0]:
            p = int(line.split()[1][1:])
            def report(p=p):
                A.state['mqtt_status']['print'] = {'mc_percent': p, 'mc_remaining_time': 0}
                A.state['mqtt_status']['last_update'] = time.time()
            threading.Timer(0.1, report).start()
    return True

A.send_gcode_lines_to_printer = printer
A.send_gcode_to_printer = lambda line: printer([line])

def reset():
    sent.clear(); lose.clear(); mqtt_dead[0] = False
    A.state['direct_jobs'] = {}; A.state['active_direct_job_ids'] = []
    A.state['direct_job'] = {'active': False, 'status': 'idle'}
    A.state['printer_connected'] = True
    A.state['mqtt_status'] = {'connected': True, 'error': None,
                              'last_update': time.time(), 'print': {'mc_percent': 30}}
    A.head.reset(); A.marker_link = Link()
    for k in A.tool_stats: A.tool_stats[k] = 0
    del A.board_marks[:]

def run(gcode, timeout=10):
    r = A.queue_direct_gcode(gcode, single_call=True)
    job = A.wait_for_direct_job(r['job_id'], timeout=timeout)
    return r, job

print("splitting")
segs = A.split_tool_batches(A.translate_tool_gcode("G1 X1\nM3 S500\nG1 X2\nM5\nM280 P0 S90")[0])
check("motion / tool / motion / tool / tool",
      [s['kind'] for s in segs] == ['gcode', 'tool', 'gcode', 'tool', 'tool'], str(segs))
check("each tool batch is M400 + its M620",
      segs[1]['lines'][0] == 'M400' and segs[1]['lines'][1].startswith('M620 P100'), str(segs[1]))
check("the M400 is not left behind in the motion", segs[0]['lines'] == ['G1 X1'], str(segs[0]))
check("servo addressed on 1", (segs[4]['address'], segs[4]['value']) == (1, 90), str(segs[4]))
check("markers are not tool commands",
      all(s['kind'] == 'gcode' for s in A.split_tool_batches("M620 R230 M621 R230")))

print("\nall echoed: 4 batches, one after another, each with its markers")
reset()
r, job = run("G1 X1 F6000\nM3 S1000\nG1 X2\nM5")
check("job complete", job and job['status'] == 'complete', str(job and job.get('message')))
check("four publishes", len(sent) == 4, str(sent))
check("motion batch: lines, M400, serial marker, MQTT marker",
      sent[0][0] == 'G1 X1 F6000' and sent[0][1] == 'M400' and sent[0][2].startswith('M620 R2')
      and sent[0][3].startswith('M73 P'), str(sent[0]))
check("tool batch: M400, the command, MQTT marker only",
      sent[1][:2] == ['M400', 'M620 P200 M621 P200'] and len(sent[1]) == 3 and sent[1][2].startswith('M73 P'),
      str(sent[1]))
check("last batch is the M5", sent[3][:2] == ['M400', 'M620 P0 M621 P0'], str(sent[3]))
percents = [l for b in sent for l in b if l.startswith('M73')]
check("every M73 percent is different", len(set(percents)) == len(percents), str(percents))
check("two confirmations counted", A.tool_stats['confirmed'] == 2, str(A.tool_stats))

print("\nmotion batch whose serial marker is lost: its M73 completes it")
reset()
lose[:] = [True]
r, job = run("G1 X1 F6000\nM3 S500")
check("job complete", job and job['status'] == 'complete', str(job and job.get('message')))
check("no resend of the motion", sum('G1 X1 F6000' in b for b in sent) == 1, str(sent))

print("\na lost echo: the M73 came back but the board never saw it, so it is resent")
reset()
lose[:] = [True]
r, job = run("M3 S500\nG1 X5 F6000")
check("job complete", job and job['status'] == 'complete', str(job and job.get('message')))
idx = [i for i, b in enumerate(sent) if 'M620 P100 M621 P100' in b]
check("sent twice", len(idx) == 2, str(sent))
check("with a fresh M73 each time", len(idx) == 2 and sent[idx[0]][-1] != sent[idx[1]][-1], str(sent))
check("motion only after the confirmation",
      len(idx) == 2 and any('G1 X5 F6000' in b for b in sent[idx[1]:])
      and not any('G1 X5 F6000' in b for b in sent[:idx[1]]), str(sent))
check("missed + resent_ok counted",
      A.tool_stats['missed'] == 1 and A.tool_stats['resent_ok'] == 1 and A.tool_stats['confirmed'] == 1,
      str(A.tool_stats))

print("\nno echo and no MQTT report: the job fails, nothing more is sent")
reset()
lose[:] = [True]; mqtt_dead[0] = True
r, job = run("M3 S500\nG1 X5 F6000")
check("job failed", job and job['status'] == 'queue_error', str(job and job.get('status')))
check("the move after it never went out", not any('G1 X5 F6000' in b for b in sent), str(sent))
check("failure counted", A.tool_stats['failed'] == 1, str(A.tool_stats))

print("\nsends submitted mid-job queue behind it, and the calls return at once")
reset()
lose[:] = [True]          # first marker lost: the job holds for the MQTT marker
r1 = A.queue_direct_gcode("G1 X1 F6000\nM3 S500\nG1 X2\nM5", single_call=True)
time.sleep(0.05)
t0 = time.time()
jog_ok = A.send_tracked("G1 Y7 F6000")
r2 = A.queue_direct_gcode("G1 X9 F6000", single_call=True)
check("the calls did not wait for the tool job", time.time() - t0 < 0.1, f"{time.time() - t0:.2f}s")
check("and report success as usual", jog_ok and r2['success'] and r2['job_id'], str(r2))
j1 = A.wait_for_direct_job(r1['job_id'], timeout=10)
j2 = A.wait_for_direct_job(r2['job_id'], timeout=10)
check("both jobs complete", j1['status'] == 'complete' and j2['status'] == 'complete',
      f"{j1['status']} {j2['status']}")
flat = [l for b in sent for l in b]
last_tool = max(i for i, l in enumerate(flat) if l.startswith("M620 P0"))  # the M5
check("the jog went out after the whole tool job", flat.index("G1 Y7 F6000") > last_tool, str(sent))
check("then the second job", flat.index("G1 X9 F6000") > flat.index("G1 Y7 F6000"), str(sent))

print("\nlog file has every event")
import json
events = [json.loads(l)['event'] for l in open(LOG)]
check("confirmed, missed and failed all logged",
      {'confirmed', 'missed', 'failed'} <= set(events), str(events))

print("\n" + ("FAILURES: " + ", ".join(fail) if fail else "all tool confirmation checks passed"))
sys.exit(1 if fail else 0)
