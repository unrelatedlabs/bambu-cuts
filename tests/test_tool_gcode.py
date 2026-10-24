"""M3/M4/M5 and M280 translated into the laser board's M620/M621 protocol."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import bambucuts.webui.app
A = sys.modules['bambucuts.webui.app']
translate = A.translate_tool_gcode

fail = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail and not cond else ''}")
    if not cond: fail.append(label)

def one(line, s_max=None):
    """The bus command a single tool line becomes (the line after its M400)."""
    return translate(line, s_max)[0].strip().split("\n")[-1]

print("laser power, S scaled from the tool's range onto 0-200")
check("M3 S1000 is full power", one("M3 S1000").startswith("M620 P200 M621 P200"), one("M3 S1000"))
check("M3 S500 is half", one("M3 S500").startswith("M620 P100 M621 P100"), one("M3 S500"))
check("M3 S0 is off", one("M3 S0").startswith("M620 P0 M621 P0"), one("M3 S0"))
check("M4 behaves like M3", one("M4 S250").startswith("M620 P50 M621 P50"), one("M4 S250"))
check("M5 turns it off", one("M5").startswith("M620 P0 M621 P0"), one("M5"))
check("bare M3 is full power (GRBL)", one("M3").startswith("M620 P200"), one("M3"))
check("s_max=200 passes S straight through",
      one("M3 S150", 200).startswith("M620 P150"), one("M3 S150", 200))
check("S above the range clamps", one("M3 S99999").startswith("M620 P200"), one("M3 S99999"))
check("negative S clamps to 0", one("M3 S-5").startswith("M620 P0"), one("M3 S-5"))

print("\nservo")
check("M280 P0 S90", one("M280 P0 S90").startswith("M620 R90 M621 R90"), one("M280 P0 S90"))
check("angle clamps to 180", one("M280 P0 S999").startswith("M620 R180"), one("M280 P0 S999"))
check("M280 without S is left alone", one("M280 P0") == "M280 P0", one("M280 P0"))
check("the P index is not mistaken for the angle",
      "R0" not in one("M280 P0 S45"), one("M280 P0 S45"))

print("\nevery tool command waits for the moves ahead of it")
for src in ("M3 S500", "M5", "M280 P0 S90"):
    out = translate(src)[0].split("\n")
    check(f"{src} is preceded by M400", out[0] == "M400" and len(out) == 2, str(out))

print("\nthe original command is kept as a comment")
check("M3 keeps its source", "; M3 S500" in one("M3 S500"), one("M3 S500"))

print("\neverything else passes through untouched")
text, counts = translate("G0 X0 Y0\nM3 S500\nG1 X10 F600\nM5\nM280 P0 S90\n"
                         "M620 P50 M621 P50\n; a comment\nM73 P5 R0")
lines = text.split("\n")
check("motion untouched", lines[0] == "G0 X0 Y0" and lines[3] == "G1 X10 F600", str(lines[:4]))
check("hand-written M620 untouched", lines[8] == "M620 P50 M621 P50", lines[8])
check("comments untouched", lines[9] == "; a comment", lines[9])
check("M73 untouched", lines[10] == "M73 P5 R0", lines[10])
check("counts reported", counts == {'laser': 2, 'servo': 1}, str(counts))
check("one M400 added per tool command", len(lines) == 8 + 3, str(len(lines)))

print("\nM3 inside a program keeps its position")
text, _ = translate("G1 X1\nM3 S1000\nG1 X2\nM5\nG1 X3")
check("order preserved",
      [l.split()[0] for l in text.split("\n")] == ["G1", "M400", "M620", "G1", "M400", "M620", "G1"],
      str(text.split("\n")))

print("\n" + ("FAILURES: " + ", ".join(fail) if fail else "all tool-gcode checks passed"))
sys.exit(1 if fail else 0)
