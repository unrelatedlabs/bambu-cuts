#!/usr/bin/env python3
"""
GRBL Server Integration Example

This example shows how to integrate the GRBL server with a larger CNC control system.
It demonstrates:
- Starting the GRBL server in a separate thread
- Connecting to it as a client
- Sending G-code commands programmatically
- Monitoring machine state
- Error handling and recovery
"""

import threading
import time
import socket
import json
from typing import Optional, Dict, Any, List
from grbl_server import GRBLServer


class CNCController:
    """Example CNC controller that uses the GRBL server."""
    
    def __init__(self, host='localhost', port=2217):
        self.host = host
        self.port = port
        self.grbl_server = None
        self.server_thread = None
        self.socket = None
        self.connected = False
        self.machine_state = "Unknown"
        self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.feed_rate = 1000.0
        self.spindle_speed = 0
        
    def start_server(self):
        """Start the GRBL server in a separate thread."""
        print("Starting GRBL server...")
        self.grbl_server = GRBLServer(host=self.host, port=self.port)
        self.server_thread = threading.Thread(target=self.grbl_server.start)
        self.server_thread.daemon = True
        self.server_thread.start()
        
        # Wait a moment for server to start
        time.sleep(1)
        print(f"GRBL server started on {self.host}:{self.port}")
    
    def connect(self) -> bool:
        """Connect to the GRBL server."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            
            # Read welcome message
            response = self._receive_response()
            print(f"Connected to GRBL: {response}")
            
            # Initialize machine
            self._initialize_machine()
            
            return True
            
        except Exception as e:
            print(f"Failed to connect to GRBL server: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the GRBL server."""
        if self.socket:
            self.socket.close()
            self.connected = False
            print("Disconnected from GRBL server")
    
    def _send_command(self, command: str) -> bool:
        """Send a command to the GRBL server."""
        if not self.connected:
            print("Not connected to server")
            return False
        
        try:
            self.socket.send((command + '\n').encode('utf-8'))
            print(f"Sent: {command}")
            return True
        except Exception as e:
            print(f"Failed to send command: {e}")
            return False
    
    def _receive_response(self, timeout: float = 1.0) -> Optional[str]:
        """Receive response from GRBL server."""
        if not self.connected:
            return None
        
        try:
            self.socket.settimeout(timeout)
            response = self.socket.recv(1024).decode('utf-8').strip()
            return response
        except socket.timeout:
            return None
        except Exception as e:
            print(f"Failed to receive response: {e}")
            return None
    
    def _send_and_receive(self, command: str) -> Optional[str]:
        """Send command and receive response."""
        if self._send_command(command):
            return self._receive_response()
        return None
    
    def _initialize_machine(self):
        """Initialize the machine with standard settings."""
        print("Initializing machine...")
        
        # Set units to mm
        self._send_and_receive("G21")
        
        # Set absolute positioning
        self._send_and_receive("G90")
        
        # Set XY plane
        self._send_and_receive("G17")
        
        # Home the machine
        self._send_and_receive("G28")
        
        print("Machine initialized")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current machine status."""
        response = self._send_and_receive("?")
        if response and response.startswith("<"):
            # Parse status response
            # Format: <State|MPos:x,y,z|WPos:x,y,z|Buf:15|FS:1000,0>
            parts = response[1:-1].split('|')
            
            status = {}
            for part in parts:
                if ':' in part:
                    key, value = part.split(':', 1)
                    if key == 'MPos':
                        coords = value.split(',')
                        status['machine_position'] = {
                            'x': float(coords[0]),
                            'y': float(coords[1]),
                            'z': float(coords[2])
                        }
                    elif key == 'WPos':
                        coords = value.split(',')
                        status['work_position'] = {
                            'x': float(coords[0]),
                            'y': float(coords[1]),
                            'z': float(coords[2])
                        }
                    elif key == 'FS':
                        speeds = value.split(',')
                        status['feed_rate'] = float(speeds[0])
                        status['spindle_speed'] = int(speeds[1])
                else:
                    status['state'] = part
            
            return status
        
        return {}
    
    def move_to(self, x: float, y: float, z: float, feed_rate: Optional[float] = None) -> bool:
        """Move to absolute position."""
        if feed_rate:
            self.feed_rate = feed_rate
            command = f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f} F{feed_rate:.0f}"
        else:
            command = f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f}"
        
        response = self._send_and_receive(command)
        return response == "ok"
    
    def rapid_move_to(self, x: float, y: float, z: float) -> bool:
        """Rapid move to absolute position."""
        command = f"G0 X{x:.3f} Y{y:.3f} Z{z:.3f}"
        response = self._send_and_receive(command)
        return response == "ok"
    
    def set_position(self, x: float, y: float, z: float) -> bool:
        """Set current position without moving."""
        command = f"G92 X{x:.3f} Y{y:.3f} Z{z:.3f}"
        response = self._send_and_receive(command)
        return response == "ok"
    
    def home(self) -> bool:
        """Home the machine."""
        response = self._send_and_receive("G28")
        return response == "ok"
    
    def start_spindle(self, speed: int) -> bool:
        """Start spindle at specified speed."""
        command = f"M3 S{speed}"
        response = self._send_and_receive(command)
        if response == "ok":
            self.spindle_speed = speed
        return response == "ok"
    
    def stop_spindle(self) -> bool:
        """Stop spindle."""
        response = self._send_and_receive("M5")
        if response == "ok":
            self.spindle_speed = 0
        return response == "ok"
    
    def feed_hold(self) -> bool:
        """Activate feed hold."""
        response = self._send_and_receive("!")
        return response == "ok"
    
    def cycle_start(self) -> bool:
        """Resume from feed hold."""
        response = self._send_and_receive("~")
        return response == "ok"
    
    def execute_gcode_file(self, filename: str) -> bool:
        """Execute a G-code file."""
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
            
            print(f"Executing G-code file: {filename}")
            
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if not line or line.startswith(';'):
                    continue
                
                print(f"Line {line_num}: {line}")
                response = self._send_and_receive(line)
                
                if response != "ok":
                    print(f"Error at line {line_num}: {response}")
                    return False
                
                # Small delay to prevent overwhelming the server
                time.sleep(0.01)
            
            print("G-code file execution completed")
            return True
            
        except Exception as e:
            print(f"Error executing G-code file: {e}")
            return False
    
    def run_demo_sequence(self):
        """Run a demonstration sequence."""
        print("\n=== Running Demo Sequence ===")
        
        # Get initial status
        status = self.get_status()
        print(f"Initial status: {status}")
        
        # Home the machine
        print("\n1. Homing machine...")
        if self.home():
            print("✓ Machine homed")
        else:
            print("✗ Homing failed")
        
        # Move to start position
        print("\n2. Moving to start position...")
        if self.rapid_move_to(10, 10, 5):
            print("✓ Moved to start position")
        else:
            print("✗ Move failed")
        
        # Start spindle
        print("\n3. Starting spindle...")
        if self.start_spindle(1000):
            print("✓ Spindle started at 1000 RPM")
        else:
            print("✗ Spindle start failed")
        
        # Draw a square
        print("\n4. Drawing a square...")
        square_points = [
            (20, 10, 2),  # Lower left
            (20, 20, 2),  # Upper left
            (30, 20, 2),  # Upper right
            (30, 10, 2),  # Lower right
            (20, 10, 2),  # Back to start
        ]
        
        for i, (x, y, z) in enumerate(square_points):
            print(f"  Moving to point {i+1}: ({x}, {y}, {z})")
            if not self.move_to(x, y, z, 500):
                print(f"  ✗ Move to point {i+1} failed")
                break
            time.sleep(0.1)  # Small delay for visibility
        
        # Raise tool
        print("\n5. Raising tool...")
        if self.move_to(30, 10, 5, 1000):
            print("✓ Tool raised")
        else:
            print("✗ Tool raise failed")
        
        # Stop spindle
        print("\n6. Stopping spindle...")
        if self.stop_spindle():
            print("✓ Spindle stopped")
        else:
            print("✗ Spindle stop failed")
        
        # Return to home
        print("\n7. Returning to home...")
        if self.home():
            print("✓ Returned to home")
        else:
            print("✗ Return home failed")
        
        # Final status
        status = self.get_status()
        print(f"\nFinal status: {status}")
        
        print("\n=== Demo Sequence Complete ===")


def main():
    """Main function demonstrating GRBL server integration."""
    print("GRBL Server Integration Example")
    print("=" * 50)
    
    # Create CNC controller
    cnc = CNCController()
    
    try:
        # Start GRBL server
        cnc.start_server()
        
        # Wait for server to be ready
        time.sleep(2)
        
        # Connect to server
        if not cnc.connect():
            print("Failed to connect to GRBL server")
            return
        
        # Run demo sequence
        cnc.run_demo_sequence()
        
    except KeyboardInterrupt:
        print("\nDemo interrupted by user")
    except Exception as e:
        print(f"Error during demo: {e}")
    finally:
        # Cleanup
        cnc.disconnect()
        if cnc.grbl_server:
            cnc.grbl_server.stop()
        print("Demo completed")


if __name__ == '__main__':
    main()

