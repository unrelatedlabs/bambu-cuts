#!/usr/bin/env python3
"""
GRBL Server Startup Script

This script provides an easy way to start the GRBL server with proper error handling
and cleanup. It also includes a simple web interface for monitoring the server status.
"""

import sys
import signal
import time
import threading
from grbl_server import GRBLServer


class GRBLServerManager:
    """Manager for the GRBL server with proper cleanup and monitoring."""
    
    def __init__(self, host='localhost', port=2217):
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        self.running = False
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print(f"\nReceived signal {signum}. Shutting down gracefully...")
        self.stop()
        sys.exit(0)
    
    def start(self):
        """Start the GRBL server."""
        print("GRBL Server Manager")
        print("=" * 50)
        
        # Check if port is available
        if self.server and self.server.is_port_in_use(self.host, self.port):
            print(f"❌ Port {self.port} is already in use!")
            print("Please stop any existing server or use a different port.")
            print("You can kill existing processes with: pkill -f grbl_server.py")
            return False
        
        # Create and start server
        self.server = GRBLServer(host=self.host, port=self.port)
        self.server_thread = threading.Thread(target=self.server.start)
        self.server_thread.daemon = True
        self.server_thread.start()
        
        # Wait for server to start
        time.sleep(1)
        
        if self.server.running:
            print(f"✅ GRBL Server started successfully on {self.host}:{self.port}")
            print("\nServer Information:")
            print(f"  Host: {self.host}")
            print(f"  Port: {self.port}")
            print(f"  Protocol: GRBL 1.1h compatible")
            print("\nConnection Examples:")
            print(f"  bCNC: Network connection to {self.host}:{self.port}")
            print(f"  Universal Gcode Sender: TCP connection to {self.host}:{self.port}")
            print(f"  Custom applications: Connect to {self.host}:{self.port} via TCP socket")
            print("\nCommands:")
            print("  Press Ctrl+C to stop the server")
            print("  Run 'python test_grbl_server.py' to test the server")
            print("\nServer is running... (Press Ctrl+C to stop)")
            
            self.running = True
            self._monitor_server()
            return True
        else:
            print("❌ Failed to start GRBL server")
            return False
    
    def stop(self):
        """Stop the GRBL server."""
        if self.server:
            self.server.stop()
            self.running = False
            print("\n✅ GRBL Server stopped")
    
    def _monitor_server(self):
        """Monitor the server and display statistics."""
        try:
            while self.running and self.server and self.server.running:
                time.sleep(10)  # Update every 10 seconds
                
                if self.server:
                    stats = self.server.get_statistics()
                    print(f"\n📊 Server Statistics:")
                    print(f"  Uptime: {stats['uptime_seconds']:.1f} seconds")
                    print(f"  Commands processed: {stats['commands_processed']}")
                    print(f"  Bytes received: {stats['bytes_received']}")
                    print(f"  Current position: X{stats['current_position'].x:.3f} Y{stats['current_position'].y:.3f} Z{stats['current_position'].z:.3f}")
                    print(f"  Machine state: {stats['machine_state']}")
                    print(f"  Active clients: {stats['active_clients']}")
                
        except KeyboardInterrupt:
            pass


def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='GRBL Server Manager')
    parser.add_argument('--host', default='localhost', help='Host to bind to (default: localhost)')
    parser.add_argument('--port', type=int, default=2217, help='Port to bind to (default: 2217)')
    parser.add_argument('--test', action='store_true', help='Run test suite after starting server')
    
    args = parser.parse_args()
    
    # Create server manager
    manager = GRBLServerManager(host=args.host, port=args.port)
    
    try:
        # Start server
        if manager.start():
            # Run test if requested
            if args.test:
                print("\n🧪 Running test suite...")
                import subprocess
                try:
                    result = subprocess.run([sys.executable, 'test_grbl_server.py'], 
                                          capture_output=True, text=True, timeout=30)
                    if result.returncode == 0:
                        print("✅ All tests passed!")
                    else:
                        print("❌ Some tests failed:")
                        print(result.stdout)
                        print(result.stderr)
                except subprocess.TimeoutExpired:
                    print("⏰ Test suite timed out")
                except Exception as e:
                    print(f"❌ Error running tests: {e}")
            
            # Keep running until interrupted
            while manager.running:
                time.sleep(1)
        else:
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        manager.stop()


if __name__ == '__main__':
    main()

