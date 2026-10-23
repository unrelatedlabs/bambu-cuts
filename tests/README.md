# Tests

No framework, no hardware, no printer. Run them all:

    tests/run.sh

Each file is also runnable on its own and prints a PASS/FAIL line per check.

- `test_laser_board_firmware.py` — executes `laser board/code.py` with
  `board`, `busio`, `digitalio`, `microcontroller`, `pwmio` and `usb_cdc`
  replaced by mocks, then drives `process_packet()` with synthesised
  AMS-Lite payloads. Covers the marker range check, that markers never
  touch the laser or servo, the `set_power()` clamp, and the marker
  backlog: queued while the port refuses, retried after a partial write,
  capped when the host stops reading.
- `test_marker_link.py` — `MarkerLink` against a pty. Parsing, sequence-gap
  detection, board restart not counted as loss, port loss, clean shutdown.
- `test_markers.py` — marker allocation and matching, stale-marker
  rejection, inferred completion of queued jobs, and that `sent_count`
  excludes our own marker lines while still counting the user's `M620`s.
- `test_marker_fallback.py` — the side-channel-only path: no `M73` is sent
  while the board is connected, and the lazy fallback fires on port loss,
  on a sequence gap, and from the watchdog, exactly once, covering every
  job in flight.

The firmware test strips the `while True:` loop and executes everything
above it, so top-level setup is exercised as written. Keep that loop last
in `code.py`.
