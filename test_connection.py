#!/usr/bin/env python3
"""
Test connection to A1 Mini printer and provide troubleshooting steps
"""

import socket
import requests
import subprocess
import sys
from config import PRINTER_IP, ACCESS_CODE

def test_basic_connectivity():
    """Test basic network connectivity to the printer"""
    print(f"Testing connectivity to {PRINTER_IP}...")
    
    # Test ping
    try:
        result = subprocess.run(['ping', '-c', '3', PRINTER_IP], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("✓ Ping successful - printer is reachable")
            return True
        else:
            print("✗ Ping failed - printer not reachable")
            return False
    except Exception as e:
        print(f"✗ Ping test failed: {e}")
        return False

def test_ports():
    """Test common ports on the printer"""
    print(f"\nTesting common ports on {PRINTER_IP}...")
    
    ports_to_test = {
        80: "HTTP",
        443: "HTTPS", 
        554: "RTSP",
        322: "RTSP Alternative",
        8080: "HTTP Alternative",
        9999: "Custom HTTP",
        21: "FTP",
        990: "FTPS",
        22: "SSH",
        23: "Telnet"
    }
    
    open_ports = []
    
    for port, description in ports_to_test.items():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((PRINTER_IP, port))
            sock.close()
            
            if result == 0:
                print(f"✓ Port {port} ({description}) - OPEN")
                open_ports.append(port)
            else:
                print(f"✗ Port {port} ({description}) - CLOSED")
        except Exception as e:
            print(f"✗ Port {port} ({description}) - ERROR: {e}")
    
    return open_ports

def test_http_services(open_ports):
    """Test HTTP services on open ports"""
    if not open_ports:
        print("\nNo open ports found to test HTTP services.")
        return
    
    print(f"\nTesting HTTP services on open ports...")
    
    for port in open_ports:
        for protocol in ['http', 'https']:
            try:
                url = f"{protocol}://{PRINTER_IP}:{port}"
                print(f"Testing {url}...")
                response = requests.get(url, timeout=5)
                print(f"✓ {url} - Status: {response.status_code}")
                
                # Check if it looks like a printer interface
                if 'bambu' in response.text.lower() or 'printer' in response.text.lower():
                    print(f"  → This looks like a printer interface!")
                
            except requests.exceptions.RequestException as e:
                print(f"✗ {url} - Error: {e}")

def provide_troubleshooting():
    """Provide troubleshooting steps"""
    print("\n" + "="*60)
    print("TROUBLESHOOTING STEPS")
    print("="*60)
    
    print("\n1. Check Printer Status:")
    print("   - Is the printer powered on?")
    print("   - Is the printer connected to WiFi?")
    print("   - Check the printer's display for network status")
    
    print("\n2. Enable LAN Mode:")
    print("   - On the printer's touchscreen, go to Settings")
    print("   - Navigate to Network settings")
    print("   - Enable 'LAN Mode' or 'Local Network Mode'")
    print("   - Note the IP address shown on screen")
    
    print("\n3. Verify Network Connection:")
    print("   - Ensure your computer and printer are on the same network")
    print("   - Check if you can ping the printer IP")
    print("   - Try accessing the printer's web interface in a browser")
    
    print("\n4. Check Firewall:")
    print("   - Ensure your firewall allows connections to the printer")
    print("   - Try temporarily disabling firewall for testing")
    
    print("\n5. Update Configuration:")
    print("   - Verify the IP address in config.py is correct")
    print("   - Check if the access code is correct")
    print("   - The access code is shown on the printer's LAN mode screen")
    
    print("\n6. Alternative Methods:")
    print("   - Try using the printer's mobile app to find the IP")
    print("   - Check your router's admin panel for connected devices")
    print("   - Look for 'Bambu' or 'A1' in the device list")

def main():
    print("A1 Mini Printer Connection Test")
    print("="*40)
    print(f"Target IP: {PRINTER_IP}")
    print(f"Access Code: {ACCESS_CODE}")
    print()
    
    # Test basic connectivity
    if not test_basic_connectivity():
        print("\n❌ Basic connectivity failed!")
        provide_troubleshooting()
        return False
    
    # Test ports
    open_ports = test_ports()
    
    # Test HTTP services
    test_http_services(open_ports)
    
    if not open_ports:
        print("\n❌ No open ports found!")
        print("The printer may not be in LAN mode or services are not running.")
        provide_troubleshooting()
        return False
    
    print(f"\n✓ Found {len(open_ports)} open ports: {open_ports}")
    print("You can now try the camera frame extraction tools.")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)


