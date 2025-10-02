#!/usr/bin/env python3
"""
Virtual Serial Port Utility

Creates a virtual serial port that listens for commands and responds appropriately.
When receiving "?" command, returns "<Idle|MPos:433.000,23.000,231.000|FS:0,0>"
"""

import serial
import serial.tools.list_ports
import threading
import time
import sys
import argparse
import os
from typing import Optional


class VirtualSerialPort:
    def __init__(self, port_name: str, baudrate: int = 115200):
        self.port_name = port_name
        self.baudrate = baudrate
        self.serial_connection: Optional[serial.Serial] = None
        self.running = False
        self.lock = threading.Lock()
        
    def start(self):
        """Start the virtual serial port server"""
        try:
            # Create a virtual serial port using pty (pseudo-terminal)
            import pty
            import tty
            
            # Create master and slave file descriptors
            master_fd, slave_fd = pty.openpty()
            
            # Get the slave device name
            slave_name = os.ttyname(slave_fd)
            
            print(f"Virtual serial port created: {slave_name}")
            print(f"Master FD: {master_fd}, Slave FD: {slave_fd}")
            
            # Set up the master side for reading/writing
            self.master_fd = master_fd
            self.slave_fd = slave_fd
            
            self.running = True
            
            # Start listening thread
            listen_thread = threading.Thread(target=self._listen_for_commands, daemon=True)
            listen_thread.start()
            
            print(f"Virtual serial port '{slave_name}' is listening for commands...")
            print("Commands will be printed to console.")
            print("Send '?' to get status response.")
            print("Press Ctrl+C to stop.")
            
            # Keep the main thread alive
            try:
                while self.running:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                print("\nShutting down...")
                self.stop()
                
        except Exception as e:
            print(f"Error creating virtual serial port: {e}")
            sys.exit(1)
    
    def _listen_for_commands(self):
        """Listen for incoming commands on the virtual serial port"""
        try:
            while self.running:
                # Read from master side of pty
                if os.read(self.master_fd, 1):
                    # Read available data
                    data = os.read(self.master_fd, 1024)
                    if data:
                        command = data.decode('utf-8', errors='ignore').strip()
                        if command:
                            print(f"Received command: '{command}'")
                            
                            # Process the command
                            response = self._process_command(command)
                            if response:
                                print(f"Sending response: '{response}'")
                                # Send response back through the virtual port
                                os.write(self.master_fd, (response + '\n').encode('utf-8'))
                
                time.sleep(0.01)  # Small delay to prevent busy waiting
                
        except Exception as e:
            if self.running:
                print(f"Error in command listener: {e}")
    
    def _process_command(self, command: str) -> str:
        """Process incoming commands and return appropriate responses"""
        command = command.strip()
        
        if command == "?":
            return "<Idle|MPos:433.000,23.000,231.000|FS:0,0>"
        elif command.upper() == "HELP":
            return "Available commands: ? (status), HELP (this message)"
        elif command.upper() == "QUIT" or command.upper() == "EXIT":
            self.running = False
            return "Goodbye!"
        else:
            return f"Unknown command: '{command}'. Send '?' for status or 'HELP' for help."
    
    def stop(self):
        """Stop the virtual serial port server"""
        self.running = False
        try:
            if hasattr(self, 'master_fd'):
                os.close(self.master_fd)
            if hasattr(self, 'slave_fd'):
                os.close(self.slave_fd)
        except:
            pass
        print("Virtual serial port stopped.")


def main():
    parser = argparse.ArgumentParser(description='Virtual Serial Port Utility')
    parser.add_argument('--port', '-p', default='vserial', 
                       help='Virtual port name (default: vserial)')
    parser.add_argument('--baudrate', '-b', type=int, default=115200,
                       help='Baud rate (default: 115200)')
    
    args = parser.parse_args()
    
    print("Virtual Serial Port Utility")
    print("=" * 40)
    
    # Create and start the virtual serial port
    virtual_port = VirtualSerialPort(args.port, args.baudrate)
    
    try:
        virtual_port.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        virtual_port.stop()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


