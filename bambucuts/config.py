"""
Configuration for Bambu printer
Reads and writes to ~/.bambucuts.conf
"""

import os
import json
from pathlib import Path

# Config file location
CONFIG_FILE = Path.home() / '.bambucuts.conf'

# FTPS settings for file operations (constants)
FTPS_USERNAME = "bblp"  # Default Bambu FTPS username
FTPS_PORT = 990  # Default FTPS port (implicit TLS)

# Mutable config container - this can be updated and changes are visible everywhere
_config_data = {}


def load_config():
    """Load configuration from file or prompt user."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                return config
        except Exception as e:
            print(f"Error reading config file: {e}")
            print("Please re-enter configuration...")

    # Config file doesn't exist or couldn't be read, prompt user
    print("\n=== Bambu Printer Configuration ===")
    print("Configuration file not found. Please enter printer details:\n")

    ip = input("Printer IP Address (e.g., 192.168.1.150): ").strip()
    serial = input("Printer Serial Number (find on printer): ").strip()
    access_code = input("Access Code (from printer screen): ").strip()

    config = {
        'ip': ip,
        'serial': serial,
        'access_code': access_code
    }

    # Save the configuration
    save_config(config)
    print(f"\nConfiguration saved to {CONFIG_FILE}\n")

    return config


def save_config(config):
    """Save configuration to file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving config file: {e}")
        return False


def update_config(ip=None, serial=None, access_code=None):
    """Update configuration with new values."""
    try:
        # Update with new values (only if provided)
        if ip is not None:
            _config_data['ip'] = ip
        if serial is not None:
            _config_data['serial'] = serial
        if access_code is not None:
            _config_data['access_code'] = access_code

        # Save to file
        if save_config(_config_data):
            print(f"Configuration updated: IP={_config_data['ip']}, Serial={_config_data['serial']}")
            return True, 'Configuration saved successfully'
        else:
            return False, 'Failed to save configuration file'

    except Exception as e:
        return False, str(e)


def get_config():
    """Get current configuration."""
    return _config_data.copy()


# Load configuration on module import
_config_data.update(load_config())

