from svg_to_gcode.svg_parser import parse_file
from svg_to_gcode.compiler import Compiler, interfaces
from svg_to_gcode.formulas import linear_map

class CustomInterface(interfaces.Gcode):
    def __init__(self):
        super().__init__()
        self.fan_speed = 1

    # Override the laser_off method such that it also powers off the fan.
    def laser_off(self):
        return "G1 Z100 F1000 "  # Turn off the fan + turn off the laser

    def laser_on(self):
        return "G1 Z5 F1000 \nG1 Z2.1 F1000 "  # Turn off the fan + turn off the laser

    # Override the set_laser_power method
    def set_laser_power(self, power):
        if power < 0 or power > 1:
            raise ValueError(f"{power} is out of bounds. Laser power must be given between 0 and 1. "
                             f"The interface will scale it correctly.")

        if power == 0:
            return self.laser_off()
        else:
            return self.laser_on()
        
# Instantiate a compiler, specifying the custom interface and the speed at which the tool should move.
gcode_compiler = Compiler(CustomInterface, movement_speed=10000, cutting_speed=1000, pass_depth=5)

curves = parse_file("badge.svg") # Parse an svg file into geometric curves

gcode_compiler.append_curves(curves) 
gcode_compiler.compile_to_file("badge.gcode")