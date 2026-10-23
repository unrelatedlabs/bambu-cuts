"""
Configuration for Bambu printer

Each printer gets its own profile, and each profile its own file:

    default profile   ->  ~/.bambucuts.conf
    profile "mini"    ->  ~/.bambucuts.mini.conf

Pick one with --profile on the command line or BAMBUCUTS_PROFILE in the
environment. To drive several printers, run one server instance per profile.
Each profile gets its own default port so they do not collide: the default
profile takes DEFAULT_PORT and every other profile the next port up, in the
order list_profiles() returns them.

    bambucuts server              # default profile  ->  port 5425
    bambucuts server --profile mini   # second profile  ->  port 5426

A 'port' in the profile's config file, or --port, still wins.
"""

import os
import json
import re
from pathlib import Path

# Where profile files live, and the name of the default one.
CONFIG_DIR = Path.home()
DEFAULT_CONFIG_FILE = CONFIG_DIR / '.bambucuts.conf'

# Kept for backwards compatibility; the active profile may point elsewhere.
CONFIG_FILE = DEFAULT_CONFIG_FILE

# Port the default profile's server binds to; other profiles count up from here.
DEFAULT_PORT = 5425

# FTPS settings for file operations (constants)
FTPS_USERNAME = "bblp"  # Default Bambu FTPS username
FTPS_PORT = 990  # Default FTPS port (implicit TLS)

# Profile names become part of a filename, so keep them boring.
_PROFILE_RE = re.compile(r'^[A-Za-z0-9._-]+$')

# Active profile, None meaning the default one.
_profile = None

# Mutable config container - this can be updated and changes are visible
# everywhere. Filled lazily on first access so that --profile can be applied
# before anything reads (or prompts for) a printer's details.
_config_data = {}
_loaded = False

# Default MQTT settings, merged into every profile that lacks them.
DEFAULTS = {
    'mqtt_enabled': False,
    'mqtt_broker': 'localhost',
    'mqtt_port': 1883,
    'mqtt_topic': 'bambucuts/estop',
    # Completion markers read back from the laser board over USB serial, which
    # confirm a direct job far sooner than the printer's MQTT progress report.
    # See bambucuts/marker_link.py. An empty device is auto-detected.
    'marker_enabled': True,
    'marker_device': '',
}


def normalize_profile(name):
    """Return a validated profile name, or None for the default profile."""
    if name is None:
        return None
    name = str(name).strip()
    if name == '' or name == 'default':
        return None
    if not _PROFILE_RE.match(name):
        raise ValueError(
            f"Invalid profile name {name!r}: use letters, digits, '.', '_' or '-'")
    return name


def config_path(profile=None):
    """Path of the config file backing a profile (the active one by default)."""
    if profile is None:
        profile = _profile
    else:
        profile = normalize_profile(profile)
    if profile is None:
        return DEFAULT_CONFIG_FILE
    return CONFIG_DIR / f'.bambucuts.{profile}.conf'


def get_profile():
    """Active profile name, or None when running the default profile."""
    return _profile


def profile_label():
    """Human readable name of the active profile."""
    return _profile or 'default'


def set_profile(name):
    """Switch to a profile. The new config is loaded on next access."""
    global _profile, _loaded
    name = normalize_profile(name)
    if name == _profile and _loaded:
        return
    _profile = name
    _loaded = False
    _config_data.clear()


def list_profiles():
    """Names of the profiles that have a config file, default one first."""
    profiles = []
    if DEFAULT_CONFIG_FILE.exists():
        profiles.append('default')
    for path in sorted(CONFIG_DIR.glob('.bambucuts.*.conf')):
        name = path.name[len('.bambucuts.'):-len('.conf')]
        if name:
            profiles.append(name)
    return profiles


def _pinned_port(name):
    """The 'port' a profile's config file names, or None."""
    try:
        with open(config_path(None if name == 'default' else name)) as f:
            port = json.load(f).get('port')
    except Exception:
        return None
    return int(port) if port else None


def _port_assignment(extra=None):
    """Port per known profile: pinned ones as written, the rest filled in.

    The default profile starts at DEFAULT_PORT and the others one port up,
    each taking the lowest port no other profile has already taken, so two
    printers never end up on the same one. `extra` is a profile with no
    config file yet; it goes last so it cannot displace an existing one.
    """
    names = list_profiles()
    if extra and extra not in names:
        names.append(extra)

    assigned = {name: port for name in names
                if (port := _pinned_port(name)) is not None}
    used = set(assigned.values())
    for name in names:
        if name in assigned:
            continue
        port = DEFAULT_PORT if name == 'default' else DEFAULT_PORT + 1
        while port in used:
            port += 1
        assigned[name] = port
        used.add(port)
    return assigned


def default_port(profile=None):
    """Port a profile serves on when neither --port nor its config names one.

    The default profile gets DEFAULT_PORT and a second printer's profile the
    next port up, so both can run at once without any extra flags. New
    profiles have this written into their config file when created, which
    pins it for good.
    """
    profile = _profile if profile is None else normalize_profile(profile)
    name = profile or 'default'
    return _port_assignment(extra=name)[name]


def load_config(profile=None):
    """Load configuration for a profile from file, or prompt for it."""
    path = config_path(profile)
    label = normalize_profile(profile) if profile is not None else _profile

    if path.exists():
        try:
            with open(path, 'r') as f:
                config = json.load(f)
                # Add MQTT defaults if not present
                for key, value in DEFAULTS.items():
                    if key not in config:
                        config[key] = value
                return config
        except Exception as e:
            print(f"Error reading config file {path}: {e}")
            print("Please re-enter configuration...")

    # Config file doesn't exist or couldn't be read, prompt user
    print("\n=== Bambu Printer Configuration ===")
    print(f"Profile: {label or 'default'}  ({path})")
    print("Configuration file not found. Please enter printer details:\n")

    ip = input("Printer IP Address (e.g., 192.168.1.150): ").strip()
    serial = input("Printer Serial Number (find on printer): ").strip()
    access_code = input("Access Code (from printer screen): ").strip()

    config = {
        'ip': ip,
        'serial': serial,
        'access_code': access_code,
        # Shown in the UI so several instances stay tellable apart.
        'name': label or '',
    }

    # Pin a free port for a second (third, ...) printer's profile, so several
    # instances can run at once and this one's port stays put as more are added.
    if label is not None:
        config['port'] = default_port(label)

    # Add MQTT defaults
    config.update(DEFAULTS)

    # Save the configuration
    save_config(config, profile=profile)
    print(f"\nConfiguration saved to {path}\n")

    return config


def ensure_loaded():
    """Load the active profile once, on first use."""
    global _loaded
    if not _loaded:
        _config_data.update(load_config())
        _loaded = True
    return _config_data


def save_config(config, profile=None):
    """Save configuration to the profile's file."""
    path = config_path(profile)
    try:
        with open(path, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving config file {path}: {e}")
        return False


def update_config(ip=None, serial=None, access_code=None, name=None,
                  mqtt_enabled=None, mqtt_broker=None, mqtt_port=None, mqtt_topic=None):
    """Update configuration with new values."""
    try:
        ensure_loaded()

        # Update with new values (only if provided)
        if ip is not None:
            _config_data['ip'] = ip
        if serial is not None:
            _config_data['serial'] = serial
        if access_code is not None:
            _config_data['access_code'] = access_code
        if name is not None:
            _config_data['name'] = name
        if mqtt_enabled is not None:
            _config_data['mqtt_enabled'] = mqtt_enabled
        if mqtt_broker is not None:
            _config_data['mqtt_broker'] = mqtt_broker
        if mqtt_port is not None:
            _config_data['mqtt_port'] = mqtt_port
        if mqtt_topic is not None:
            _config_data['mqtt_topic'] = mqtt_topic

        # Save to file
        if save_config(_config_data):
            print(f"Configuration updated [{profile_label()}]: "
                  f"IP={_config_data['ip']}, Serial={_config_data['serial']}")
            return True, 'Configuration saved successfully'
        else:
            return False, 'Failed to save configuration file'

    except Exception as e:
        return False, str(e)


def get_config():
    """Get current configuration."""
    return ensure_loaded().copy()


def display_name():
    """Label for this instance: the configured name, else the profile name."""
    return (get_config().get('name') or '').strip() or profile_label()


# Env var lets `BAMBUCUTS_PROFILE=x1 python -m bambucuts.webui.app` work without
# any command line plumbing. A later set_profile() call still wins.
set_profile(os.environ.get('BAMBUCUTS_PROFILE'))
