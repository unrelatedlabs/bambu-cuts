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

Per-frame tracing is behind `DEBUG` in `code.py`, off by default: the UART loop
reads at 1.2 Mbaud and the servo needs a pulse every 20 ms, so three `print()`
calls per decoded frame are enough to cost frames and jitter the servo. The
once-a-second `[status]` line is always on and reports frame counts, the worst
gap between servo updates, markers sent and dropped, and whether the host is
reading.

## GCode File preparation 

LightBurn with the attached settings. Or Kiri:Moto (roll your own settings)

## GCODE 

Laser power range is 0-200 

0%
    M620 P0 M621 P0 M400

50%
    M620 P100 M621 P100 M400

100%
    M620 P200 M621 P200 M400


Also issue M400 before turning on/off to complete the move 

## Servo

    M620 R{angle} M621 R{angle} M400

Angle range is 0-180. Moves are eased in and out over 0.33 s.

## Completion markers

Values from 201 up are not power or angle: they are completion markers for the
host, reported over USB serial and leaving the laser and servo untouched. The
range check happens before the channel dispatch, which matters because
`set_servo()` clamps to 180 — an unintercepted marker would park the servo at
full deflection.

    M620 R{201-251} M621 R{201-251}

The board emits one line per marker on its data port:

    MARK <address> <value> <board_uptime_ms>

bambucuts appends one of these to each batch of direct G-code and completes the
job when it comes back, which takes milliseconds against the 1 to 2 seconds the
printer's own MQTT progress report needs. See "G-code progress over MQTT" in
the top-level README.

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


