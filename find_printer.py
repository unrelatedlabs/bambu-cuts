#!/usr/bin/env python3
"""
Network discovery script to find the A1 Mini printer on the local network
"""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import requests

def scan_port(host, port, timeout=1):
    """Scan a single port on a host"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return port if result == 0 else None
    except:
        return None

def scan_host(host, ports, timeout=1):
    """Scan multiple ports on a host"""
    open_ports = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(scan_port, host, port, timeout) for port in ports]
        for future in futures:
            result = future.result()
            if result:
                open_ports.append(result)
    return open_ports

def test_http_services(host, ports):
    """Test HTTP services on open ports"""
    http_services = []
    for port in ports:
        for protocol in ['http', 'https']:
            try:
                url = f"{protocol}://{host}:{port}"
                response = requests.get(url, timeout=3)
                http_services.append({
                    'url': url,
                    'status': response.status_code,
                    'headers': dict(response.headers)
                })
                print(f"✓ {url} - Status: {response.status_code}")
            except:
                pass
    return http_services

def find_printer_on_network():
    """Find the A1 Mini printer on the local network"""
    print("Searching for A1 Mini printer on local network...")
    print("This may take a few minutes...")
    
    # Get local network range
    import subprocess
    try:
        # Get local IP and network
        result = subprocess.run(['ifconfig'], capture_output=True, text=True)
        print("Local network interfaces:")
        print(result.stdout)
    except:
        pass
    
    # Common ports to check
    ports_to_scan = [80, 443, 554, 322, 8080, 9999, 21, 990, 22, 23]
    
    # Get local network range (simplified - assumes 192.168.1.x)
    base_ip = "192.168.1"
    
    print(f"\nScanning {base_ip}.1-254 for open ports...")
    
    found_devices = []
    
    # Scan each IP in the range
    for i in range(1, 255):
        host = f"{base_ip}.{i}"
        print(f"Scanning {host}...", end="\r")
        
        open_ports = scan_host(host, ports_to_scan, timeout=0.5)
        
        if open_ports:
            print(f"\n✓ Found device at {host} with open ports: {open_ports}")
            found_devices.append({
                'ip': host,
                'ports': open_ports
            })
            
            # Test HTTP services
            http_services = test_http_services(host, open_ports)
            if http_services:
                print(f"  HTTP services found:")
                for service in http_services:
                    print(f"    {service['url']} - Status: {service['status']}")
    
    print(f"\nScan complete. Found {len(found_devices)} devices with open ports.")
    
    if found_devices:
        print("\nPotential printer candidates:")
        for device in found_devices:
            print(f"  {device['ip']} - Ports: {device['ports']}")
    else:
        print("\nNo devices found with open ports.")
        print("Make sure:")
        print("1. Printer is powered on")
        print("2. Printer is connected to the same network")
        print("3. Printer is in LAN mode")
    
    return found_devices

if __name__ == "__main__":
    find_printer_on_network()


