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


