"""Track where the print head is from the G-code we have sent.

Bambu printers do not report head position over MQTT, so the only way to
know where the head is during a plot is to simulate the G-code we sent and
commit it as the printer confirms execution. The tracker keeps three views:

- ``position``: where the head is known to be, after the last G-code whose
  execution was confirmed (immediate commands are assumed to execute as
  soon as nothing else is queued ahead of them).
- ``target``: where the head will be once everything queued has executed.
- ``estimated``: a live guess interpolated along the in-flight path by the
  time elapsed since it started moving.

The simulator carries the printer's modal state (G90/G91, feed rate) across
sends, which matters because this app leaves the printer in G91 for jogging.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

AXES = ('x', 'y', 'z', 'e')
XYZ = ('x', 'y', 'z')


@dataclass
class MotionState:
    """Printer modal state plus position, as left by a stretch of G-code."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    absolute: bool = True
    feed: float = 1000.0  # mm/min

    def position(self) -> Dict[str, float]:
        return {'x': self.x, 'y': self.y, 'z': self.z, 'e': self.e}

    def xyz(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class Segment:
    """One linear move: head travels start -> end between t0 and t1 seconds."""
    t0: float
    t1: float
    start: Tuple[float, float, float]
    end: Tuple[float, float, float]


@dataclass
class Simulation:
    end: MotionState
    seconds: float
    segments: List[Segment] = field(default_factory=list)


def _words(code: str):
    out = []
    for part in code.split():
        letter = part[0].upper()
        try:
            out.append((letter, float(part[1:])))
        except ValueError:
            continue
    return out


def _executable_lines(gcode_text: str):
    for raw in gcode_text.split('\n'):
        code = raw.split(';', 1)[0].strip()
        if code:
            yield code


def simulate(gcode_text: str, start: MotionState) -> Simulation:
    """Run G-code through a kinematic model and return the resulting state.

    Handles G0/G1 (with modal F), G4 dwells, G90/G91, G92 and G28. Time is
    distance over feed with acceleration ignored, so short segments run a
    little slower on the real machine. E is treated as relative (M83), which
    is how this rig is configured, and never contributes to travel time.
    """
    state = replace(start)
    seconds = 0.0
    segments: List[Segment] = []

    for code in _executable_lines(gcode_text):
        upper = code.upper()
        cmd = upper.split()[0]

        if cmd == 'G90':
            state.absolute = True
        elif cmd == 'G91':
            state.absolute = False
        elif cmd == 'G92':
            for letter, value in _words(upper)[1:]:
                if letter.lower() in AXES:
                    setattr(state, letter.lower(), value)
        elif cmd == 'G28':
            flagged = [a[0].lower() for a in upper.split()[1:] if a[0].lower() in XYZ]
            for axis in flagged or XYZ:
                setattr(state, axis, 0.0)
        elif cmd == 'G4':
            for letter, value in _words(upper)[1:]:
                if letter == 'P':
                    seconds += value / 1000.0
                elif letter == 'S':
                    seconds += value
        elif cmd in ('G0', 'G1'):
            before = state.xyz()
            squared = 0.0
            for letter, value in _words(upper)[1:]:
                axis = letter.lower()
                if letter == 'F':
                    if value > 0:
                        state.feed = value
                    continue
                if axis == 'e':
                    state.e = state.e + value  # relative extrusion on this rig
                    continue
                if axis not in XYZ:
                    continue
                current = getattr(state, axis)
                new = value if state.absolute else current + value
                squared += (new - current) ** 2
                setattr(state, axis, new)
            if squared:
                duration = math.sqrt(squared) / (max(1.0, state.feed) / 60.0)
                segments.append(Segment(seconds, seconds + duration, before, state.xyz()))
                seconds += duration

    return Simulation(end=state, seconds=seconds, segments=segments)


@dataclass
class PendingEntry:
    id: Optional[str]          # direct job id, or None for an immediate command
    sim: Simulation
    queued_at: float
    started_at: Optional[float] = None
    lines: int = 0


class HeadTracker:
    """Position bookkeeping for everything the app sends to the printer."""

    def __init__(self, feed: float = 1000.0, absolute: bool = True):
        self._lock = threading.Lock()
        self.executed = MotionState(feed=feed, absolute=absolute)
        self.pending: List[PendingEntry] = []
        # True until a G28 or G92 establishes where the head really is, and
        # again after a job stalls without confirming its motion.
        self.uncertain = True

    # -- state -----------------------------------------------------------

    @property
    def position(self) -> Dict[str, float]:
        with self._lock:
            return self.executed.position()

    def target_state(self) -> MotionState:
        with self._lock:
            return self._target_state()

    def _target_state(self) -> MotionState:
        return self.pending[-1].sim.end if self.pending else self.executed

    # -- sending ---------------------------------------------------------

    def apply(self, gcode_text: str, job_id: Optional[str] = None, queued_at: Optional[float] = None) -> Simulation:
        """Record G-code that has been sent to the printer.

        Immediate commands (no job_id) are committed at once when nothing is
        queued ahead of them; otherwise they wait behind the in-flight jobs.
        Direct jobs always wait for commit() or drop().
        """
        now = queued_at if queued_at is not None else time.time()
        with self._lock:
            sim = simulate(gcode_text, self._target_state())
            self._note_reference_reset(gcode_text)
            if job_id is None and not self.pending:
                self.executed = sim.end
                return sim
            entry = PendingEntry(id=job_id, sim=sim, queued_at=now,
                                 lines=sum(1 for _ in _executable_lines(gcode_text)))
            self.pending.append(entry)
            self._start_head(now)
            return sim

    def simulate_from_target(self, gcode_text: str) -> Simulation:
        """Simulate without recording, from where the queue will leave the head."""
        with self._lock:
            return simulate(gcode_text, self._target_state())

    def commit(self, job_id: str, at: Optional[float] = None) -> bool:
        """A job's done marker arrived: it and everything queued before it executed."""
        now = at if at is not None else time.time()
        with self._lock:
            index = next((i for i, e in enumerate(self.pending) if e.id == job_id), None)
            if index is None:
                return False
            for entry in self.pending[:index + 1]:
                self.executed = entry.sim.end
            del self.pending[:index + 1]
            self._commit_leading_immediates()
            self._start_head(now)
            return True

    def drop(self, job_id: str) -> bool:
        """A job stalled or failed to queue: its motion is unconfirmed."""
        with self._lock:
            index = next((i for i, e in enumerate(self.pending) if e.id == job_id), None)
            if index is None:
                return False
            del self.pending[index]
            # Later entries were simulated from this job's end; keep them (the
            # printer may well have run it) but flag that we no longer know.
            self.uncertain = True
            self._commit_leading_immediates()
            self._start_head(time.time())
            return True

    def reset(self, feed: Optional[float] = None, absolute: Optional[bool] = None):
        """Forget the queue, e.g. on reconnect; position stays but is uncertain."""
        with self._lock:
            self.pending = []
            if feed is not None:
                self.executed.feed = feed
            if absolute is not None:
                self.executed.absolute = absolute
            self.uncertain = True

    # -- queries ---------------------------------------------------------

    def estimate(self, now: Optional[float] = None) -> Dict[str, float]:
        """Best guess of the live head position."""
        now = now if now is not None else time.time()
        with self._lock:
            if not self.pending:
                return self.executed.position()
            head = self.pending[0]
            elapsed = now - (head.started_at or head.queued_at)
            position = self.executed.position()
            if elapsed >= head.sim.seconds:
                position.update(zip(XYZ, head.sim.end.xyz()))
                return position
            for seg in head.sim.segments:
                if elapsed < seg.t0:
                    position.update(zip(XYZ, seg.start))
                    return position
                if seg.t0 <= elapsed < seg.t1:
                    f = (elapsed - seg.t0) / (seg.t1 - seg.t0) if seg.t1 > seg.t0 else 1.0
                    position.update({a: s + (e - s) * f for a, s, e in zip(XYZ, seg.start, seg.end)})
                    return position
            position.update(zip(XYZ, head.sim.end.xyz()))
            return position

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = now if now is not None else time.time()
        estimated = self.estimate(now)
        with self._lock:
            target = self._target_state()
            head = self.pending[0] if self.pending else None
            remaining = sum(e.sim.seconds for e in self.pending)
            if head is not None:
                remaining -= min(head.sim.seconds, now - (head.started_at or head.queued_at))
            return {
                'position': self.executed.position(),
                'target': target.position(),
                'estimated': estimated,
                'absolute': target.absolute,
                'feed': target.feed,
                'in_flight': len(self.pending),
                'remaining_seconds': round(max(0.0, remaining), 1),
                'uncertain': self.uncertain,
            }

    # -- internals -------------------------------------------------------

    def _commit_leading_immediates(self):
        while self.pending and self.pending[0].id is None:
            self.executed = self.pending[0].sim.end
            self.pending.pop(0)

    def _start_head(self, now: float):
        if self.pending and self.pending[0].started_at is None:
            self.pending[0].started_at = now

    def _note_reference_reset(self, gcode_text: str):
        """G28/G92 establish the position; M18/M84 on X, Y or Z free the axes."""
        for code in _executable_lines(gcode_text):
            words = code.upper().split()
            cmd = words[0]
            if cmd in ('G28', 'G92'):
                self.uncertain = False
            elif cmd in ('M18', 'M84'):
                axes = [w[0] for w in words[1:] if w[0] in 'XYZE']
                if not axes or any(a in 'XYZ' for a in axes):
                    self.uncertain = True
