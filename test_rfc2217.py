#!/usr/bin/env python3
"""
Test RFC2217 protocol support for GRBL server.

This script tests the telnet/RFC2217 protocol negotiation that some
G-code senders expect when connecting to a GRBL server.
"""

import socket
import time
import struct


def test_rfc2217_negotiation():
    """Test RFC2217 protocol negotiation."""
    print("Testing RFC2217 Protocol Support")
    print("=" * 40)
    
    try:
        # Connect to server
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', 2217))
        print("✓ Connected to GRBL server")
        
        # Test 1: Send telnet WILL BINARY command
        print("\n1. Testing WILL BINARY command...")
        will_binary = bytes([0xFF, 0xFD, 0x00])  # IAC WILL BINARY
        sock.send(will_binary)
        
        # Read response
        response = sock.recv(1024)
        print(f"   Response: {response.hex()}")
        
        if response.startswith(bytes([0xFF, 0xFB, 0x00])):  # IAC DO BINARY
            print("   ✓ Server agreed to binary transmission")
        else:
            print("   ✗ Server did not agree to binary transmission")
        
        # Test 2: Send telnet DO BINARY command
        print("\n2. Testing DO BINARY command...")
        do_binary = bytes([0xFF, 0xFB, 0x00])  # IAC DO BINARY
        sock.send(do_binary)
        
        # Read response
        response = sock.recv(1024)
        print(f"   Response: {response.hex()}")
        
        if response.startswith(bytes([0xFF, 0xFC, 0x00])):  # IAC WILL BINARY
            print("   ✓ Server enabled binary transmission")
        else:
            print("   ✗ Server did not enable binary transmission")
        
        # Test 3: Send terminal type negotiation
        print("\n3. Testing terminal type negotiation...")
        will_termtype = bytes([0xFF, 0xFD, 0x18])  # IAC WILL TERMINAL-TYPE
        sock.send(will_termtype)
        
        # Read response
        response = sock.recv(1024)
        print(f"   Response: {response.hex()}")
        
        if response.startswith(bytes([0xFF, 0xFB, 0x18])):  # IAC DO TERMINAL-TYPE
            print("   ✓ Server agreed to terminal type negotiation")
            
            # Send terminal type subnegotiation
            termtype_sb = bytes([0xFF, 0xFA, 0x18, 0x00, 0xFF, 0xF0])  # IAC SB TERMINAL-TYPE SEND IAC SE
            sock.send(termtype_sb)
            
            # Read terminal type response
            response = sock.recv(1024)
            print(f"   Terminal type response: {response.hex()}")
            
            if b'Grbl' in response:
                print("   ✓ Server sent terminal type: Grbl")
            else:
                print("   ✗ Server did not send expected terminal type")
        else:
            print("   ✗ Server did not agree to terminal type negotiation")
        
        # Test 4: Send window size negotiation
        print("\n4. Testing window size negotiation...")
        will_naws = bytes([0xFF, 0xFD, 0x1F])  # IAC WILL NAWS
        sock.send(will_naws)
        
        # Read response
        response = sock.recv(1024)
        print(f"   Response: {response.hex()}")
        
        if response.startswith(bytes([0xFF, 0xFB, 0x1F])):  # IAC DO NAWS
            print("   ✓ Server agreed to window size negotiation")
        else:
            print("   ✗ Server did not agree to window size negotiation")
        
        # Test 5: Send regular G-code command
        print("\n5. Testing regular G-code after negotiation...")
        sock.send(b'?\n')  # Status query
        
        # Read response
        response = sock.recv(1024)
        print(f"   Response: {response.decode('utf-8', errors='ignore').strip()}")
        
        if response.startswith(b'<'):
            print("   ✓ Server responded with status")
        else:
            print("   ✗ Server did not respond with expected status")
        
        print("\n" + "=" * 40)
        print("RFC2217 Protocol Test Complete")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        try:
            sock.close()
        except:
            pass


def test_binary_mode():
    """Test binary mode communication."""
    print("\nTesting Binary Mode Communication")
    print("=" * 40)
    
    try:
        # Connect to server
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', 2217))
        print("✓ Connected to GRBL server")
        
        # Enable binary mode
        will_binary = bytes([0xFF, 0xFD, 0x00])  # IAC WILL BINARY
        sock.send(will_binary)
        sock.recv(1024)  # Read response
        
        do_binary = bytes([0xFF, 0xFB, 0x00])  # IAC DO BINARY
        sock.send(do_binary)
        sock.recv(1024)  # Read response
        
        print("✓ Binary mode enabled")
        
        # Test sending binary data
        print("\nTesting binary data transmission...")
        
        # Send a simple command in binary
        command = b'G90\n'
        sock.send(command)
        
        response = sock.recv(1024)
        print(f"   Command: {command}")
        print(f"   Response: {response.decode('utf-8', errors='ignore').strip()}")
        
        if b'ok' in response:
            print("   ✓ Binary mode communication working")
        else:
            print("   ✗ Binary mode communication failed")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        try:
            sock.close()
        except:
            pass


def main():
    """Main test function."""
    print("GRBL Server RFC2217 Protocol Test")
    print("=" * 50)
    
    # Test RFC2217 negotiation
    test_rfc2217_negotiation()
    
    # Test binary mode
    test_binary_mode()
    
    print("\nTest completed!")


if __name__ == '__main__':
    main()

