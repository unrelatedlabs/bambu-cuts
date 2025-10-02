#!/usr/bin/env python3
"""
Test script for the WebSocket streamer
Uses config.py values for easy testing
"""

import subprocess
import sys
import time
from config import PRINTER_IP, ACCESS_CODE

def test_websocket_streamer():
    """Test the WebSocket streamer with config values"""
    print("Testing WebSocket Streamer with config values...")
    print(f"Printer IP: {PRINTER_IP}")
    print(f"Access Code: {ACCESS_CODE}")
    print("-" * 50)
    
    # Build command
    cmd = [
        sys.executable, 
        "a1_streamer.py",
        "-a", PRINTER_IP,
        "-c", ACCESS_CODE,
        "-p", "8080"
    ]
    
    print(f"Running: {' '.join(cmd)}")
    print("Open http://localhost:8080 in your browser to view the stream")
    print("WebSocket endpoint: ws://localhost:8080/ws")
    print("Press Ctrl+C to stop")
    print("-" * 50)
    
    try:
        # Run the streamer
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except subprocess.CalledProcessError as e:
        print(f"Error running streamer: {e}")
        return False
    except FileNotFoundError:
        print("Error: a1_streamer.py not found")
        return False
    
    return True

if __name__ == "__main__":
    test_websocket_streamer()
