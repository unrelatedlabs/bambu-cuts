"""Enable a second USB serial port, for completion markers.

CircuitPython exposes one CDC port by default and uses it for the console
(REPL and print output). Completion markers need a channel that a terminal
attached to the REPL cannot consume and that debug output cannot garble, so
enable the optional data port alongside the console. code.py writes markers
to usb_cdc.data when it exists and falls back to the console when it does
not, so this file is an optimisation rather than a requirement.

Two ports then appear on the host, by stable path:

    /dev/serial/by-id/usb-Lolin_S2_Mini_<uid>-if00   console / REPL
    /dev/serial/by-id/usb-Lolin_S2_Mini_<uid>-if02   markers

boot.py runs only on hard reset or power-up, not when code.py is saved.
"""

import usb_cdc

usb_cdc.enable(console=True, data=True)
