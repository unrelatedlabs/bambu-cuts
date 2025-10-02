#!/usr/bin/env python3
"""
Test script for the virtual serial port utility
"""

import socket
import time
import sys


def test_virtual_serial(host='localhost', port=9999):
    """Test the virtual serial port by sending commands"""
    try:
        # Connect to the virtual serial port server
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect((host, port))
        
        print(f"Connected to virtual serial port at {host}:{port}")
        print("Sending test commands...")
        print()
        
        # Test commands
        test_commands = [
            "?",
            "HELP",
            "STATUS", 
            "UNKNOWN_COMMAND",
            "QUIT"
        ]
        
        for command in test_commands:
            print(f"Sending: '{command}'")
            client_socket.send((command + '\n').encode('utf-8'))
            
            # Receive response
            response = client_socket.recv(1024).decode('utf-8').strip()
            print(f"Response: '{response}'")
            print("-" * 40)
            
            time.sleep(0.5)  # Small delay between commands
        
        client_socket.close()
        print("Test completed successfully!")
        
    except ConnectionRefusedError:
        print(f"Error: Could not connect to {host}:{port}")
        print("Make sure the virtual serial port server is running.")
        print("Run: python3 virtual_serial_simple.py")
        sys.exit(1)
    except Exception as e:
        print(f"Error during test: {e}")
        sys.exit(1)


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else 'localhost'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9999
    
    print("Virtual Serial Port Test")
    print("=" * 30)
    test_virtual_serial(host, port)


