# USE PROTECTION
OD6+ Laser googles for 450nm, good ventilation and a respirator! Some materials are very toxic!!!!

# A1 Mini laser cutter

3.5W 12V 450nm blue laser module 

esp32 s2 mini board 

2x DC/DC converter for 24V to 5V and 12V


## Wemos ESP32 S2 Mini board 

https://www.wemos.cc/en/latest/s2/s2_mini.html

### GPIO
 
GPIO 1  = Uart RX. TOP RIGH AMS Lite PIN. Tx+ on the AMS Lite port on A1 mini 
GPIO 2  = pull up, connect 510ohm resitor to GPIO1 

GPIO 6 and 7  = LED + on 7, - on 6. Follow light outout 

GPIP 39 = PWM output for laser power.

GPIO 33 = Servo signal (50 Hz PWM).

I used 2 DC/DC buck converters to get 12V and 5V from the 24V input from the printer.


AMS Lite pin out 


      +-------------------+
      |      AMS Lite     |
      +-------------------+
      | [Tx-]     [Tx+]   |
      | [GND]     [24V]   |
      +-------------------+
             [Ret]

## Firmware 

Install CircuitPython on your ESP32 Board
Copy over the code.py and boot.py

`boot.py` enables a second USB serial port for completion markers, so they do
not share the console with debug output. It only takes effect on a hard reset
or power cycle. `code.py` falls back to the console when that port is absent,
so `boot.py` is optional.

Raw frame tracing (`[SHORT]`/`[LONG]` headers and payload hex) is behind `DEBUG`
in `code.py`, off by default: the UART loop reads at 1.2 Mbaud and the servo
needs a pulse every 20 ms, so printing per bus frame costs frames and jitters
the servo. It also floods the USB console badly enough that the board stops
accepting input at all, which locks you out of the REPL.

The decoded `a=<address>, v=<value>` line is always printed. Only non-`KNOWN`
short frames reach it, so that is one line per real command, not per bus frame.

The once-a-second `[status]` line reports frame counts, the worst gap between
servo updates, `marks=<sent>/<dropped>/<backlog>`, and whether the host is
reading.

## GCode File preparation 

LightBurn with the attached settings. Or Kiri:Moto (roll your own settings)

## Tool control G-code

Write standard G-code: `M3`/`M5` for the light, `M280` for the servo. The
bambucuts server translates them into the board's bus commands on the way to
the printer, on every path (the editor's send, `/api/gcode/send-all`, the
manual G-code box and `/api/gcode`).

    M3 S<power>              light on; S is 0-1000 (bare M3 = full power)
    M4 S<power>              same as M3
    M5                       light off
    M280 P<index> S<angle>   servo to 0-180 degrees (P is ignored: one servo)

Each one becomes an `M400` followed by the bus command:

    M3 S500       ->  M400
                      M620 P100 M621 P100 ; M3 S500
    M280 P0 S90   ->  M400
                      M620 R90 M621 R90 ; M280 P0 S90

The `M400` matters. The printer runs `M620` the moment it parses it, not when
the moves ahead of it finish: a command behind a 3 s move reached the board
0.2 s after sending without `M400`, and 3.2 s with it. The translation adds
the sync for you, so the head pauses briefly at every switch.

With the board plugged in, every tool command is confirmed. G-code with
tool commands in it is split at each one and sent as a run of batches, one
after another, each sent only once the one before it is done: moves, then a
servo move, then more moves is three batches. Every batch ends with two done
markers, a serial one for the board and an `M73` for MQTT. A tool batch is
`M400`, the command, and the `M73`; the board's echo of the command is its
serial marker. If the `M73` comes back (1-2 s) and the echo never did, the
command was lost on the bus and the batch is sent again, up to 4 tries. If
neither comes back, the job stops there. Each extra batch costs about 0.2 s.
Jogs and jobs sent while one is still going out wait their turn behind it;
the API calls return straight away as usual. Every confirmation, miss and
failure is appended to `~/.bambucuts.tool-commands.log` (one JSON line each)
and counted under `tool_commands` in `/api/status`.

Servo moves are eased in and out over 0.33 s. The light has an auto-off
watchdog: it switches off 60 s after the last power command, so one `M3`
holds it for at most a minute. For a longer exposure, repeat the `M3` within
each minute.

### Switching from direct M620 commands

Replace each hand-written switch, together with its `M400`s, by the standard
command. The server adds the `M400` before the `M620` itself.

| Old (direct)                              | New                 |
|-------------------------------------------|---------------------|
| `M400` + `M620 P0 M621 P0`                | `M5`                |
| `M400` + `M620 P100 M621 P100` (50%)      | `M3 S500`           |
| `M400` + `M620 P200 M621 P200` (100%)     | `M3 S1000` or `M3`  |
| `M400` + `M620 P<v> M621 P<v>`            | `M3 S<v x 5>`       |
| `M400` + `M620 R<angle> M621 R<angle>`    | `M280 P0 S<angle>`  |

- **Power is 0-1000 now**, not 0-200: multiply old `P` values by 5. CAM
  tools work as-is with a GRBL-style laser profile set to S max 1000.
- **No `M400` of your own.** Every translated switch gets one in front.
- **Old files still work** while you move over: hand-written `M620` passes
  through untouched, but needs its own `M400` *before* it. One after it does
  not wait for the preceding move.
- **Avoid 201-251** on either channel: those are completion markers.

## Completion markers

Values from 201 up are not power or angle: they are completion markers for the
host, reported over USB serial and leaving the laser and servo untouched. The
range check happens before the channel dispatch, which matters because
`set_servo()` clamps to 180 — an unintercepted marker would park the servo at
full deflection.

    M620 R{201-251} M621 R{201-251}

The board emits one line per marker on its data port:

    MARK <address> <value> <board_uptime_ms> <seq>

`seq` is monotonic so the host can detect a lost marker as a gap. Markers are
queued and retried until the port accepts them, never dropped on a busy port
and never blocking the UART loop.

bambucuts appends one of these to each batch of direct G-code and completes the
job when it comes back, roughly 200 ms against the 2 to 3.5 s the printer's own
MQTT progress report needs. With the board connected no `M73` is sent at all.
See "G-code progress over MQTT" in the top-level README.

## Protocol details 

Send data over wire. 



    M620 P100 M621 P100 M400


## M620 R1

[SHORT] type=0x07 len=13 payload=00 02 01 00 00 00
R{x}
[SHORT] type=0x07 len=13 payload={x/4} {x%4} 01 00 00 00

## M620 P255
[SHORT] type=0x07 len=13 payload=3f 03 00 00 00 00


with P 3rd byte is 0
with R 3rd byte is 1


Pause

G4 S2


