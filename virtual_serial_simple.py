#!/usr/bin/env python3
"""
Simple Virtual Serial Port Utility

Creates a virtual serial port using a TCP socket that can be connected to
using socat or similar tools. Listens for commands and responds appropriately.
When receiving "?" command, returns "<Idle|MPos:433.000,23.000,231.000|FS:0,0>"
"""

import socket
import threading
import time
import sys
import argparse
import signal
from typing import Optional


class SimpleVirtualSerialPort:
    def __init__(self, host: str = 'localhost', port: int = 9999):
        self.host = host
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.running = False
        self.lock = threading.Lock()
        
    def start(self):
        """Start the virtual serial port server"""
        try:
            # Create server socket
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(1)
            
            print(f"Virtual serial port server started on {self.host}:{self.port}")
            print("To connect to this virtual serial port, use one of these methods:")
            print(f"  socat - TCP:{self.host}:{self.port}")
            print(f"  telnet {self.host} {self.port}")
            print(f"  nc {self.host} {self.port}")
            print()
            print("Waiting for connection...")
            print("Press Ctrl+C to stop.")
            
            self.running = True
            
            # Set up signal handler for graceful shutdown
            signal.signal(signal.SIGINT, self._signal_handler)
            
            # Accept connections and handle them
            while self.running:
                try:
                    self.client_socket, client_address = self.server_socket.accept()
                    print(f"Client connected from {client_address}")
                    
                    # Handle the client connection
                    self._handle_client()
                    
                except socket.error as e:
                    if self.running:
                        print(f"Socket error: {e}")
                    break
                except KeyboardInterrupt:
                    break
                    
        except Exception as e:
            print(f"Error starting virtual serial port server: {e}")
            sys.exit(1)
        finally:
            self.stop()
    
    def _signal_handler(self, signum, frame):
        """Handle interrupt signal for graceful shutdown"""
        print("\nReceived interrupt signal. Shutting down...")
        self.running = False
        if self.client_socket:
            self.client_socket.close()
        if self.server_socket:
            self.server_socket.close()
        sys.exit(0)
    
    def _handle_client(self):
        """Handle communication with a connected client"""
        try:
            while self.running and self.client_socket:
                # Receive data from client
                data = self.client_socket.recv(1024)
                if not data:
                    print("Client disconnected")
                    break
                
                # Decode and process the command
                command = data.decode('utf-8', errors='ignore').strip()
                if command:
                    print(f"Received command: '{command}'")
                    
                    # Process the command
                    response = self._process_command(command)
                    if response:
                        print(f"Sending response: '{response}'")
                        # Send response back to client
                        self.client_socket.send((response + '\n').encode('utf-8'))
                        
        except socket.error as e:
            print(f"Client connection error: {e}")
        except Exception as e:
            print(f"Error handling client: {e}")
        finally:
            if self.client_socket:
                self.client_socket.close()
                self.client_socket = None
    
    def _process_command(self, command: str) -> str:
        """Process incoming commands and return appropriate responses"""
        command = command.strip()
        
        if command == "?":
            return "<Idle|MPos:433.000,23.000,231.000|FS:0,0>"
        elif command.upper() == "HELP":
            return "Available commands: ? (status), HELP (this message), QUIT (disconnect)"
        elif command.upper() == "QUIT" or command.upper() == "EXIT":
            return "Goodbye!"
        elif command.upper() == "STATUS":
            return "<Idle|MPos:433.000,23.000,231.000|FS:0,0>"
        else:
            return f"Unknown command: '{command}'. Send '?' for status or 'HELP' for help."
    
    def stop(self):
        """Stop the virtual serial port server"""
        self.running = False
        try:
            if self.client_socket:
                self.client_socket.close()
            if self.server_socket:
                self.server_socket.close()
        except:
            pass
        print("Virtual serial port server stopped.")


def main():
    parser = argparse.ArgumentParser(description='Simple Virtual Serial Port Utility')
    parser.add_argument('--host', default='localhost', 
                       help='Host to bind to (default: localhost)')
    parser.add_argument('--port', '-p', type=int, default=9999,
                       help='Port to listen on (default: 9999)')
    
    args = parser.parse_args()
    
    print("Simple Virtual Serial Port Utility")
    print("=" * 40)
    
    # Create and start the virtual serial port
    virtual_port = SimpleVirtualSerialPort(args.host, args.port)
    
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


