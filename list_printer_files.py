#!/usr/bin/env python3
"""
Command line utility to list files on Bambu A1 Mini printer via FTP/FTPS.
Usage: python list_printer_files.py [options]
"""

import argparse
import sys
import os
import ssl
from ftplib import FTP, FTP_TLS
from pathlib import Path
from config import PRINTER_IP, ACCESS_CODE, FTPS_USERNAME, FTPS_PORT


def connect_to_printer():
    """Establish FTP/FTPS connection to the printer"""
    # Try different connection methods
    connection_methods = [
        ("FTPS (port 990)", lambda: connect_ftps(990)),
        ("FTP (port 21)", lambda: connect_ftp(21)),
        ("FTPS (port 21)", lambda: connect_ftps(21)),
    ]
    
    for method_name, connect_func in connection_methods:
        print(f"Trying {method_name}...")
        try:
            ftp = connect_func()
            if ftp:
                print(f"Connected successfully using {method_name}!")
                return ftp
        except Exception as e:
            print(f"Failed with {method_name}: {e}")
            continue
    
    print("Error: Failed to connect using any method.")
    print("Make sure your printer is on and connected to the network.")
    print("Also verify the credentials in config.py are correct.")
    return None


def connect_ftp(port):
    """Connect using regular FTP"""
    ftp = FTP()
    ftp.connect(PRINTER_IP, port, timeout=10)
    ftp.login(FTPS_USERNAME, ACCESS_CODE)
    return ftp


def connect_ftps(port):
    """Connect using FTPS (FTP over TLS)"""
    # Create SSL context with relaxed settings for embedded devices
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_ciphers('DEFAULT@SECLEVEL=0')  # Allow weaker ciphers for embedded devices
    
    ftps = FTP_TLS(context=context)
    ftps.set_debuglevel(1)  # Enable debug output to see what's happening
    print(f"  Attempting to connect to {PRINTER_IP}:{port}...")
    ftps.connect(PRINTER_IP, port, timeout=15)
    print(f"  Connected! Attempting login with user '{FTPS_USERNAME}'...")
    ftps.login(FTPS_USERNAME, ACCESS_CODE)
    print(f"  Logged in! Switching to secure data connection...")
    ftps.prot_p()  # Switch to secure data connection
    print(f"  Secure data connection established!")
    return ftps


def list_files(ftp, remote_path="/", recursive=False, show_details=False):
    """List files in the specified remote directory"""
    try:
        print(f"\nListing files in: {remote_path}")
        print("-" * 60)
        
        def list_directory(path, level=0):
            try:
                # Change to the directory
                if path != "/":
                    ftp.cwd(path)
                
                # Get detailed file listing
                files = []
                ftp.retrlines('LIST', files.append)
                
                # Parse the listing
                parsed_files = []
                for line in files:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 9:
                            # Parse typical FTP LIST output
                            permissions = parts[0]
                            size = parts[4] if parts[4].isdigit() else "0"
                            date_parts = parts[5:8]
                            filename = " ".join(parts[8:])
                            
                            is_dir = permissions.startswith('d')
                            
                            parsed_files.append({
                                'name': filename,
                                'is_dir': is_dir,
                                'size': int(size) if size.isdigit() else 0,
                                'permissions': permissions,
                                'date': " ".join(date_parts)
                            })
                
                # Sort files (directories first, then by name)
                parsed_files.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
                
                for file_info in parsed_files:
                    indent = "  " * level
                    filename = file_info['name']
                    is_dir = file_info['is_dir']
                    
                    if show_details:
                        # Show detailed file information
                        size = file_info['size']
                        permissions = file_info['permissions']
                        date = file_info['date']
                        
                        if is_dir:
                            file_type = "DIR"
                            size_str = ""
                        else:
                            file_type = "FILE"
                            size_str = f"{size:>8} bytes"
                        
                        print(f"{indent}{file_type:4} {date:>12} {size_str:>12} {permissions:>10} {filename}")
                    else:
                        # Simple listing
                        if is_dir:
                            print(f"{indent}{filename}/")
                        else:
                            print(f"{indent}{filename}")
                    
                    # Recursively list subdirectories if requested
                    if recursive and is_dir:
                        sub_path = f"{path}/{filename}" if path != "/" else f"/{filename}"
                        list_directory(sub_path, level + 1)
                        
            except Exception as e:
                print(f"{indent}[Error accessing {path}: {e}]")
        
        list_directory(remote_path)
        
    except Exception as e:
        print(f"Error listing files: {e}")


def download_file(ftp, remote_path, local_path):
    """Download a file from the printer"""
    try:
        print(f"Downloading {remote_path} to {local_path}...")
        
        # Create local directory if it doesn't exist
        local_dir = os.path.dirname(local_path)
        if local_dir and not os.path.exists(local_dir):
            os.makedirs(local_dir)
        
        # Download the file
        with open(local_path, 'wb') as local_file:
            ftp.retrbinary(f'RETR {remote_path}', local_file.write)
        
        print("Download completed!")
        return True
    except Exception as e:
        print(f"Error downloading file: {e}")
        return False


def get_file_list(ftp, path="/"):
    """Get a simple list of files in a directory"""
    try:
        if path != "/":
            ftp.cwd(path)
        
        files = []
        ftp.retrlines('NLST', files.append)
        return files
    except Exception as e:
        print(f"Error getting file list: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(
        description="List files on Bambu A1 Mini printer via FTP/FTPS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python list_printer_files.py                    # List files in root directory
  python list_printer_files.py -r                 # List files recursively
  python list_printer_files.py -l                 # Show detailed file information
  python list_printer_files.py -p /sdcard         # List files in specific directory
  python list_printer_files.py -d file.gcode      # Download a specific file
        """
    )
    
    parser.add_argument(
        "-p", "--path",
        default="/",
        help="Remote directory path to list (default: /)"
    )
    
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="List files recursively in subdirectories"
    )
    
    parser.add_argument(
        "-l", "--long",
        action="store_true",
        help="Show detailed file information (size, date, permissions)"
    )
    
    parser.add_argument(
        "-d", "--download",
        metavar="FILE",
        help="Download a specific file from the printer"
    )
    
    parser.add_argument(
        "-o", "--output",
        help="Output file path for download (default: same as remote filename)"
    )
    
    args = parser.parse_args()
    
    # Connect to printer
    ftp = connect_to_printer()
    if not ftp:
        sys.exit(1)
    
    try:
        if args.download:
            # Download mode
            remote_file = args.download
            local_file = args.output if args.output else Path(remote_file).name
            download_file(ftp, remote_file, local_file)
        else:
            # List mode
            list_files(ftp, args.path, args.recursive, args.long)
            
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Clean up connection
        try:
            ftp.quit()
        except:
            pass
        print("\nDisconnected from printer.")


if __name__ == "__main__":
    main()