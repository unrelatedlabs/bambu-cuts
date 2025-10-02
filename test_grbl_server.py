#!/usr/bin/env python3
"""
Test script for GRBL Server.

This script tests the GRBL server functionality by:
- Connecting to the server
- Sending various G-code commands
- Verifying responses
- Testing coordinate tracking
"""

import socket
import time
import threading
import sys


class GRBLTester:
    """Test client for GRBL server."""
    
    def __init__(self, host='localhost', port=2217):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
    
    def connect(self):
        """Connect to the GRBL server."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            print(f"Connected to GRBL server at {self.host}:{self.port}")
            
            # Read welcome message
            response = self.receive_response()
            print(f"Server: {response}")
            
        except Exception as e:
            print(f"Failed to connect: {e}")
            return False
        
        return True
    
    def disconnect(self):
        """Disconnect from the server."""
        if self.socket:
            self.socket.close()
            self.connected = False
            print("Disconnected from server")
    
    def send_command(self, command):
        """Send a command to the server."""
        if not self.connected:
            print("Not connected to server")
            return None
        
        try:
            self.socket.send((command + '\n').encode('utf-8'))
            print(f"Sent: {command}")
            return True
        except Exception as e:
            print(f"Failed to send command: {e}")
            return False
    
    def receive_response(self, timeout=1.0):
        """Receive response from server."""
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
    
    def send_and_receive(self, command, timeout=1.0):
        """Send command and receive response."""
        if self.send_command(command):
            response = self.receive_response(timeout)
            if response:
                print(f"Response: {response}")
            return response
        return None
    
    def test_basic_commands(self):
        """Test basic GRBL commands."""
        print("\n=== Testing Basic Commands ===")
        
        # Test status query
        print("\n1. Testing status query (?)")
        response = self.send_and_receive("?")
        if response and response.startswith("<"):
            print("✓ Status query working")
        else:
            print("✗ Status query failed")
        
        # Test help
        print("\n2. Testing help ($)")
        response = self.send_and_receive("$")
        if response and "Grbl settings" in response:
            print("✓ Help command working")
        else:
            print("✗ Help command failed")
        
        # Test parameters
        print("\n3. Testing parameters ($#)")
        response = self.send_and_receive("$#")
        if response and "G54:" in response:
            print("✓ Parameters command working")
        else:
            print("✗ Parameters command failed")
    
    def test_movement_commands(self):
        """Test movement commands."""
        print("\n=== Testing Movement Commands ===")
        
        # Test absolute positioning
        print("\n1. Setting absolute positioning (G90)")
        response = self.send_and_receive("G90")
        if response == "ok":
            print("✓ Absolute positioning set")
        else:
            print("✗ Absolute positioning failed")
        
        # Test rapid move
        print("\n2. Testing rapid move (G0)")
        response = self.send_and_receive("G0 X10 Y10 Z5")
        if response == "ok":
            print("✓ Rapid move successful")
        else:
            print("✗ Rapid move failed")
        
        # Check position
        print("\n3. Checking position after move")
        response = self.send_and_receive("?")
        if response and "MPos:10.000,10.000,5.000" in response:
            print("✓ Position tracking working")
        else:
            print(f"✗ Position tracking failed - got: {response}")
        
        # Test linear move
        print("\n4. Testing linear move (G1)")
        response = self.send_and_receive("G1 X20 Y20 Z10 F1000")
        if response == "ok":
            print("✓ Linear move successful")
        else:
            print("✗ Linear move failed")
        
        # Check position again
        print("\n5. Checking position after linear move")
        response = self.send_and_receive("?")
        if response and "MPos:20.000,20.000,10.000" in response:
            print("✓ Position tracking working")
        else:
            print(f"✗ Position tracking failed - got: {response}")
    
    def test_relative_movement(self):
        """Test relative movement commands."""
        print("\n=== Testing Relative Movement ===")
        
        # Set relative positioning
        print("\n1. Setting relative positioning (G91)")
        response = self.send_and_receive("G91")
        if response == "ok":
            print("✓ Relative positioning set")
        else:
            print("✗ Relative positioning failed")
        
        # Move relative
        print("\n2. Testing relative move")
        response = self.send_and_receive("G1 X5 Y5 Z2")
        if response == "ok":
            print("✓ Relative move successful")
        else:
            print("✗ Relative move failed")
        
        # Check position (should be 25, 25, 12)
        print("\n3. Checking position after relative move")
        response = self.send_and_receive("?")
        if response and "MPos:25.000,25.000,12.000" in response:
            print("✓ Relative positioning working")
        else:
            print(f"✗ Relative positioning failed - got: {response}")
        
        # Reset to absolute
        print("\n4. Resetting to absolute positioning")
        response = self.send_and_receive("G90")
        if response == "ok":
            print("✓ Reset to absolute positioning")
        else:
            print("✗ Reset failed")
    
    def test_set_position(self):
        """Test G92 set position command."""
        print("\n=== Testing Set Position (G92) ===")
        
        # Set position to origin
        print("\n1. Setting position to origin (G92 X0 Y0 Z0)")
        response = self.send_and_receive("G92 X0 Y0 Z0")
        if response == "ok":
            print("✓ Position set to origin")
        else:
            print("✗ Set position failed")
        
        # Check position
        print("\n2. Checking position after G92")
        response = self.send_and_receive("?")
        if response and "MPos:0.000,0.000,0.000" in response:
            print("✓ Position reset successful")
        else:
            print(f"✗ Position reset failed - got: {response}")
    
    def test_spindle_commands(self):
        """Test spindle commands."""
        print("\n=== Testing Spindle Commands ===")
        
        # Start spindle
        print("\n1. Starting spindle (M3 S1000)")
        response = self.send_and_receive("M3 S1000")
        if response == "ok":
            print("✓ Spindle started")
        else:
            print("✗ Spindle start failed")
        
        # Check status
        print("\n2. Checking spindle status")
        response = self.send_and_receive("?")
        if response and "FS:1000,1000" in response:
            print("✓ Spindle status correct")
        else:
            print(f"✗ Spindle status incorrect - got: {response}")
        
        # Stop spindle
        print("\n3. Stopping spindle (M5)")
        response = self.send_and_receive("M5")
        if response == "ok":
            print("✓ Spindle stopped")
        else:
            print("✗ Spindle stop failed")
    
    def test_feed_hold_and_cycle_start(self):
        """Test feed hold and cycle start."""
        print("\n=== Testing Feed Hold and Cycle Start ===")
        
        # Start a move
        print("\n1. Starting a move")
        response = self.send_and_receive("G1 X10 Y10 F500")
        if response == "ok":
            print("✓ Move started")
        else:
            print("✗ Move start failed")
        
        # Feed hold
        print("\n2. Testing feed hold (!)")
        response = self.send_and_receive("!")
        if response == "ok":
            print("✓ Feed hold activated")
        else:
            print("✗ Feed hold failed")
        
        # Check status
        print("\n3. Checking hold status")
        response = self.send_and_receive("?")
        if response and "Hold" in response:
            print("✓ Machine in hold state")
        else:
            print(f"✗ Hold state not detected - got: {response}")
        
        # Cycle start
        print("\n4. Testing cycle start (~)")
        response = self.send_and_receive("~")
        if response == "ok":
            print("✓ Cycle start activated")
        else:
            print("✗ Cycle start failed")
    
    def test_home_command(self):
        """Test home command."""
        print("\n=== Testing Home Command ===")
        
        # Move to some position first
        print("\n1. Moving to test position")
        response = self.send_and_receive("G0 X50 Y50 Z25")
        if response == "ok":
            print("✓ Moved to test position")
        else:
            print("✗ Move failed")
        
        # Home command
        print("\n2. Testing home command (G28)")
        response = self.send_and_receive("G28")
        if response == "ok":
            print("✓ Home command successful")
        else:
            print("✗ Home command failed")
        
        # Check position
        print("\n3. Checking position after home")
        response = self.send_and_receive("?")
        if response and "MPos:0.000,0.000,0.000" in response:
            print("✓ Position reset to origin")
        else:
            print(f"✗ Position not at origin - got: {response}")
    
    def run_all_tests(self):
        """Run all tests."""
        print("GRBL Server Test Suite")
        print("=" * 50)
        
        if not self.connect():
            print("Failed to connect to server. Make sure the server is running.")
            return False
        
        try:
            self.test_basic_commands()
            self.test_movement_commands()
            self.test_relative_movement()
            self.test_set_position()
            self.test_spindle_commands()
            self.test_feed_hold_and_cycle_start()
            self.test_home_command()
            
            print("\n" + "=" * 50)
            print("All tests completed!")
            
        except KeyboardInterrupt:
            print("\nTest interrupted by user")
        finally:
            self.disconnect()
        
        return True


def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Test GRBL Server')
    parser.add_argument('--host', default='localhost', help='Server host (default: localhost)')
    parser.add_argument('--port', type=int, default=2217, help='Server port (default: 2217)')
    
    args = parser.parse_args()
    
    tester = GRBLTester(host=args.host, port=args.port)
    success = tester.run_all_tests()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

