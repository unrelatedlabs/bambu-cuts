#!/usr/bin/env python3
"""
Virtual TTY Utility

Creates a virtual TTY device using socat and handles commands.
When receiving "?" command, returns "<Idle|MPos:433.000,23.000,231.000|FS:0,0>"
"""

import subprocess
import threading
import time
import sys
import os
import signal
import tempfile
from typing import Optional


class VirtualTTY:
    def __init__(self, tty_path: str = None):
        self.tty_path = tty_path or "/tmp/virtual_tty"
        self.socat_process: Optional[subprocess.Popen] = None
        self.server_process: Optional[subprocess.Popen] = None
        self.running = False
        
    def start(self):
        """Start the virtual TTY"""
        try:
            # Check if socat is available
            try:
                subprocess.run(['socat', '-V'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("Error: socat is not installed. Please install it first:")
                print("  macOS: brew install socat")
                print("  Ubuntu/Debian: sudo apt-get install socat")
                print("  CentOS/RHEL: sudo yum install socat")
                sys.exit(1)
            
            # Create a named pipe for communication
            self.pipe_path = "/tmp/virtual_tty_pipe"
            os.mkfifo(self.pipe_path)
            
            # Start socat to create the TTY
            socat_cmd = [
                'socat',
                f'PTY,link={self.tty_path},raw,echo=0',
                f'PIPE:{self.pipe_path}'
            ]
            
            print(f"Starting socat to create TTY at: {self.tty_path}")
            self.socat_process = subprocess.Popen(socat_cmd)
            
            # Wait a moment for socat to create the TTY
            time.sleep(1)
            
            # Check if TTY was created
            if not os.path.exists(self.tty_path):
                print(f"Error: TTY device not created at {self.tty_path}")
                self.stop()
                sys.exit(1)
            
            print(f"Virtual TTY created successfully: {self.tty_path}")
            print("You can now connect using:")
            print(f"  screen {self.tty_path} 115200")
            print(f"  minicom -D {self.tty_path} -b 115200")
            print("Press Ctrl+C to stop.")
            
            self.running = True
            
            # Set up signal handler
            signal.signal(signal.SIGINT, self._signal_handler)
            
            # Start the command handler
            self._handle_commands()
            
        except Exception as e:
            print(f"Error creating virtual TTY: {e}")
            self.stop()
            sys.exit(1)
    
    def _signal_handler(self, signum, frame):
        """Handle interrupt signal"""
        print("\nShutting down...")
        self.running = False
        self.stop()
        sys.exit(0)
    
    def _handle_commands(self):
        """Handle commands from the TTY"""
        try:
            while self.running:
                # Read from the pipe
                with open(self.pipe_path, 'r') as pipe:
                    while self.running:
                        try:
                            # Read data from the pipe
                            data = pipe.read(1024)
                            if data:
                                command = data.strip()
                                if command:
                                    print(f"Received command: '{command}'")
                                    
                                    # Process the command
                                    response = self._process_command(command)
                                    if response:
                                        print(f"Sending response: '{response}'")
                                        # Write response back to the pipe
                                        with open(self.pipe_path, 'w') as out_pipe:
                                            out_pipe.write(response + '\n')
                                            out_pipe.flush()
                            
                            time.sleep(0.01)
                        except Exception as e:
                            if self.running:
                                print(f"Error reading from pipe: {e}")
                            break
                            
        except Exception as e:
            print(f"Error in command handler: {e}")
    
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
        """Stop the virtual TTY"""
        self.running = False
        
        if self.socat_process:
            self.socat_process.terminate()
            self.socat_process.wait()
        
        # Clean up files
        try:
            if os.path.exists(self.pipe_path):
                os.unlink(self.pipe_path)
            if os.path.exists(self.tty_path):
                os.unlink(self.tty_path)
        except:
            pass
        
        print("Virtual TTY stopped.")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Virtual TTY Utility')
    parser.add_argument('--tty', '-t', default='/tmp/virtual_tty',
                       help='TTY device path (default: /tmp/virtual_tty)')
    
    args = parser.parse_args()
    
    print("Virtual TTY Utility")
    print("=" * 20)
    
    virtual_tty = VirtualTTY(args.tty)
    
    try:
        virtual_tty.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        virtual_tty.stop()
    except Exception as e:
        print(f"Error: {e}")
        virtual_tty.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()


