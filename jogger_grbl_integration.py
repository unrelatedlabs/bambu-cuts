#!/usr/bin/env python3
"""
Jogger GRBL Integration Example

This example shows how to integrate the jogger module with the GRBL server
for real-time control of a CNC machine or 3D printer.
"""

import socket
import threading
import time
from jogger import JoggerController


class JoggerGRBLIntegration:
    """Integration between jogger and GRBL server."""
    
    def __init__(self, host='localhost', port=2217):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self.jogger = JoggerController()
        
        # Override jogger's move_axis to send commands to GRBL
        self.jogger.move_axis = self.send_gcode_command
        
    def connect(self):
        """Connect to GRBL server."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            print(f"✅ Connected to GRBL server at {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to GRBL server: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from GRBL server."""
        if self.socket:
            self.socket.close()
            self.connected = False
            print("🔌 Disconnected from GRBL server")
    
    def send_gcode_command(self, axis: str, distance: float):
        """Send G-code command to GRBL server."""
        if not self.connected:
            print("⚠️ Not connected to GRBL server")
            return
        
        # Generate G-code command
        gcode = self.jogger.generate_gcode(axis, distance)
        
        try:
            # Send command to GRBL server
            self.socket.send(f"{gcode}\n".encode())
            
            # Update position tracking
            self.jogger.position[axis.lower()] += distance
            
            # Add to history
            self.jogger.gcode_history.append(gcode)
            if len(self.jogger.gcode_history) > 20:
                self.jogger.gcode_history.pop(0)
            
            print(f"📤 Sent: {gcode}")
            
        except Exception as e:
            print(f"❌ Failed to send command: {e}")
            self.connected = False
    
    def send_special_command(self, gcode: str):
        """Send special G-code command (like G28, G92)."""
        if not self.connected:
            print("⚠️ Not connected to GRBL server")
            return
        
        try:
            self.socket.send(f"{gcode}\n".encode())
            self.jogger.gcode_history.append(gcode)
            if len(self.jogger.gcode_history) > 20:
                self.jogger.gcode_history.pop(0)
            
            print(f"📤 Sent: {gcode}")
            
        except Exception as e:
            print(f"❌ Failed to send special command: {e}")
            self.connected = False
    
    def home_xy(self):
        """Home X and Y axes via GRBL."""
        self.jogger.position['x'] = 0.0
        self.jogger.position['y'] = 0.0
        self.send_special_command("G28 X Y")
    
    def save_z_zero(self):
        """Save current Z as zero via GRBL."""
        self.jogger.position['z'] = 0.0
        self.send_special_command("G92 Z0")
    
    def reset_e_zero(self):
        """Reset E to zero via GRBL."""
        self.jogger.position['e'] = 0.0
        self.send_special_command("G92 E0")
    
    def demo_connection(self):
        """Demo the GRBL integration."""
        print("🎮 Jogger GRBL Integration Demo")
        print("=" * 50)
        
        if not self.connect():
            print("❌ Cannot proceed without GRBL connection")
            return
        
        print("\n🎯 Testing basic movements...")
        time.sleep(1)
        
        # Test basic movements
        self.jogger.move_axis('x', 1.0)
        time.sleep(0.5)
        self.jogger.move_axis('y', 1.0)
        time.sleep(0.5)
        self.jogger.move_axis('z', 0.5)
        time.sleep(0.5)
        
        print("\n🔧 Testing special functions...")
        time.sleep(1)
        
        # Test special functions
        self.home_xy()
        time.sleep(1)
        self.save_z_zero()
        time.sleep(1)
        self.reset_e_zero()
        
        print("\n📝 Sent G-code commands:")
        for i, gcode in enumerate(self.jogger.gcode_history, 1):
            print(f"{i:2d}. {gcode}")
        
        print(f"\n📍 Final position: {self.jogger.position}")
        print("\n✅ Demo completed!")
        
        self.disconnect()


def main():
    """Main function."""
    print("🚀 Starting Jogger GRBL Integration")
    print("Make sure GRBL server is running on localhost:2217")
    print()
    
    integration = JoggerGRBLIntegration()
    
    try:
        integration.demo_connection()
    except KeyboardInterrupt:
        print("\n🛑 Demo interrupted by user")
    finally:
        integration.disconnect()


if __name__ == "__main__":
    main()
