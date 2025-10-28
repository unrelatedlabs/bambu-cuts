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
Copy over the code.py 

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


