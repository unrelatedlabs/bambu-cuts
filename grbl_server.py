#!/usr/bin/env python3
"""
GRBL Server - A simple GRBL-compatible server for CNC control simulation.

This module provides a GRBL server that:
- Listens on port 2217 for G-code commands
- Maintains coordinate state and responds to queries
- Logs all communication to console
- Can be integrated into larger CNC control systems

GRBL Protocol Features:
- Responds to "?" with current position and status
- Processes G-code commands and updates coordinates
- Maintains machine state (idle, run, hold, etc.)
- Provides GRBL-compatible responses
"""

import socket
import threading
import time
import re
import logging
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from enum import Enum


class MachineState(Enum):
    """GRBL machine states."""
    IDLE = "Idle"
    RUN = "Run"
    HOLD = "Hold"
    JOG = "Jog"
    ALARM = "Alarm"
    DOOR = "Door"
    CHECK = "Check"
    HOME = "Home"
    SLEEP = "Sleep"


@dataclass
class MachinePosition:
    """Current machine position in all axes."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    a: float = 0.0  # A axis (4th axis)
    b: float = 0.0  # B axis (5th axis)
    c: float = 0.0  # C axis (6th axis)
    
    def to_grbl_string(self) -> str:
        """Convert to GRBL position string format."""
        return f"<{self.x:.3f},{self.y:.3f},{self.z:.3f}>"


@dataclass
class MachineSettings:
    """Machine settings and configuration."""
    feed_rate: float = 1000.0  # mm/min
    spindle_speed: int = 0  # RPM
    coordinate_system: int = 0  # G54, G55, etc.
    units: str = "mm"  # mm or inches
    absolute_positioning: bool = True  # G90 vs G91
    plane_selection: str = "XY"  # XY, XZ, YZ
    tool_length_offset: float = 0.0
    work_coordinate_offset: MachinePosition = None
    
    def __post_init__(self):
        if self.work_coordinate_offset is None:
            self.work_coordinate_offset = MachinePosition()


class GCodeParser:
    """Parse and process G-code commands."""
    
    def __init__(self):
        self.coordinate_pattern = re.compile(r'([XYZABC])([+-]?\d*\.?\d+)')
        self.feed_pattern = re.compile(r'F(\d*\.?\d+)')
        self.spindle_pattern = re.compile(r'S(\d+)')
        self.gcode_pattern = re.compile(r'G(\d+)')
        self.mcode_pattern = re.compile(r'M(\d+)')
    
    def parse_line(self, line: str) -> Dict[str, Any]:
        """Parse a G-code line and extract commands and parameters."""
        line = line.strip().upper()
        
        result = {
            'g_codes': [],
            'm_codes': [],
            'coordinates': {},
            'feed_rate': None,
            'spindle_speed': None,
            'raw_line': line
        }
        
        # Extract G codes
        g_matches = self.gcode_pattern.findall(line)
        result['g_codes'] = [int(g) for g in g_matches]
        
        # Extract M codes
        m_matches = self.mcode_pattern.findall(line)
        result['m_codes'] = [int(m) for m in m_matches]
        
        # Extract coordinates
        coord_matches = self.coordinate_pattern.findall(line)
        for axis, value in coord_matches:
            result['coordinates'][axis] = float(value)
        
        # Extract feed rate
        f_match = self.feed_pattern.search(line)
        if f_match:
            result['feed_rate'] = float(f_match.group(1))
        
        # Extract spindle speed
        s_match = self.spindle_pattern.search(line)
        if s_match:
            result['spindle_speed'] = int(s_match.group(1))
        
        return result


class GRBLServer:
    """GRBL-compatible server for CNC control simulation."""
    
    def __init__(self, host: str = 'localhost', port: int = 2217):
        self.host = host
        self.port = port
        self.socket = None
        self.running = False
        self.clients = []
        
        # Machine state
        self.state = MachineState.IDLE
        self.position = MachinePosition()
        self.settings = MachineSettings()
        self.parser = GCodeParser()
        
        # Statistics
        self.commands_processed = 0
        self.bytes_received = 0
        self.start_time = time.time()
        
        # Setup logging
        self.logger = self._setup_logging()
        
        # Command handlers
        self.command_handlers = {
            '?': self._handle_status_query,
            'G0': self._handle_rapid_move,
            'G1': self._handle_linear_move,
            'G2': self._handle_arc_move_cw,
            'G3': self._handle_arc_move_ccw,
            'G4': self._handle_dwell,
            'G10': self._handle_set_coordinate_offset,
            'G17': self._handle_plane_selection_xy,
            'G18': self._handle_plane_selection_xz,
            'G19': self._handle_plane_selection_yz,
            'G20': self._handle_units_inches,
            'G21': self._handle_units_mm,
            'G28': self._handle_home,
            'G90': self._handle_absolute_positioning,
            'G91': self._handle_relative_positioning,
            'G92': self._handle_set_position,
            'M0': self._handle_program_stop,
            'M2': self._handle_program_end,
            'M3': self._handle_spindle_cw,
            'M4': self._handle_spindle_ccw,
            'M5': self._handle_spindle_stop,
            'M30': self._handle_program_end_rewind,
        }
    
    def _setup_logging(self) -> logging.Logger:
        """Setup console logging for the server."""
        logger = logging.getLogger('grbl_server')
        logger.setLevel(logging.INFO)
        
        # Create console handler
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        
        # Add handler to logger
        logger.addHandler(handler)
        
        return logger
    
    def start(self):
        """Start the GRBL server."""
        try:
            # Check if port is already in use
            if self.is_port_in_use(self.host, self.port):
                self.logger.error(f"Port {self.port} is already in use. Please stop any existing server or use a different port.")
                self.logger.error("You can kill existing processes with: pkill -f grbl_server.py")
                return
            
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)
            
            self.running = True
            self.logger.info(f"GRBL Server started on {self.host}:{self.port}")
            self.logger.info("Waiting for connections...")
            
            while self.running:
                try:
                    client_socket, address = self.socket.accept()
                    self.logger.info(f"Client connected from {address}")
                    
                    # Handle client in separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, address)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                    
                except socket.error as e:
                    if self.running:
                        self.logger.error(f"Socket error: {e}")
                    break
                    
        except Exception as e:
            self.logger.error(f"Failed to start server: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the GRBL server."""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.logger.info("GRBL Server stopped")
    
    def is_port_in_use(self, host: str, port: int) -> bool:
        """Check if a port is already in use."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex((host, port))
                return result == 0
        except:
            return False
    
    def _handle_client(self, client_socket: socket.socket, address: Tuple[str, int]):
        """Handle communication with a client."""
        try:
            # Set socket options for better compatibility
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client_socket.settimeout(30.0)  # 30 second timeout
            
            # Handle initial protocol negotiation
            self._handle_initial_negotiation(client_socket)
            
            # Send welcome message
            self._send_response(client_socket, "Grbl 1.1h ['$' for help]")
            
            while self.running:
                try:
                    # Receive data
                    data = client_socket.recv(1024)
                    if not data:
                        break
                    
                    # Handle different data types
                    if data.startswith(b'\xff\xfd'):  # Telnet IAC (Interpret As Command)
                        # Handle telnet commands
                        self._handle_telnet_command(client_socket, data)
                        continue
                    elif data.startswith(b'\x00'):  # Null byte - ignore
                        continue
                    elif len(data) == 1 and data[0] in [0x00, 0xFF]:  # Single null or IAC
                        continue
                    
                    # Handle potential encoding issues
                    try:
                        data_str = data.decode('utf-8')
                    except UnicodeDecodeError:
                        # Try to decode with error handling
                        data_str = data.decode('utf-8', errors='ignore')
                        self.logger.warning(f"Received data with encoding issues from {address}: {data.hex()}")
                    
                    # Skip empty or whitespace-only data
                    if not data_str.strip():
                        continue
                    
                    self.bytes_received += len(data)
                    self.logger.info(f"Received from {address}: {data_str.strip()}")
                    
                    # Process each line
                    for line in data_str.strip().split('\n'):
                        line = line.strip()
                        if line and not line.startswith('\x00'):
                            response = self._process_command(line)
                            if response:
                                self._send_response(client_socket, response)
                                
                except socket.timeout:
                    # Timeout is normal - continue listening
                    continue
                except socket.error as e:
                    self.logger.error(f"Socket error with {address}: {e}")
                    break
                    
        except Exception as e:
            self.logger.error(f"Error handling client {address}: {e}")
        finally:
            client_socket.close()
            self.logger.info(f"Client {address} disconnected")
    
    def _handle_telnet_command(self, client_socket: socket.socket, data: bytes):
        """Handle telnet commands (RFC2217 protocol)."""
        try:
            if len(data) >= 3:
                # IAC (0xFF) + command + option
                iac = data[0]
                command = data[1]
                option = data[2]
                
                if iac == 0xFF:  # IAC
                    if command == 0xFD:  # WILL
                        # Client wants to enable an option
                        if option == 0x00:  # Binary transmission
                            # Agree to binary transmission
                            response = bytes([0xFF, 0xFB, 0x00])  # IAC DO BINARY
                            client_socket.send(response)
                            self.logger.info("Enabled binary transmission mode")
                        elif option == 0x18:  # Terminal type
                            # Agree to terminal type
                            response = bytes([0xFF, 0xFB, 0x18])  # IAC DO TERMINAL-TYPE
                            client_socket.send(response)
                            self.logger.info("Enabled terminal type negotiation")
                        elif option == 0x1F:  # Window size
                            # Agree to window size
                            response = bytes([0xFF, 0xFB, 0x1F])  # IAC DO NAWS
                            client_socket.send(response)
                            self.logger.info("Enabled window size negotiation")
                        else:
                            # Reject other options
                            response = bytes([0xFF, 0xFE, option])  # IAC WONT
                            client_socket.send(response)
                    elif command == 0xFC:  # WON'T
                        # Client refuses an option - that's fine
                        pass
                    elif command == 0xFB:  # DO
                        # Client wants us to enable an option
                        if option == 0x00:  # Binary transmission
                            # Agree to binary transmission
                            response = bytes([0xFF, 0xFC, 0x00])  # IAC WILL BINARY
                            client_socket.send(response)
                            self.logger.info("Enabled binary transmission mode")
                        else:
                            # Reject other options
                            response = bytes([0xFF, 0xFE, option])  # IAC WONT
                            client_socket.send(response)
                    elif command == 0xFE:  # DON'T
                        # Client doesn't want us to enable an option - that's fine
                        pass
                    elif command == 0xFA:  # SB (Subnegotiation Begin)
                        # Handle subnegotiation
                        if option == 0x18:  # Terminal type
                            # Send terminal type
                            response = bytes([0xFF, 0xFA, 0x18, 0x00, 0x47, 0x72, 0x62, 0x6C, 0xFF, 0xF0])  # "Grbl"
                            client_socket.send(response)
                            self.logger.info("Sent terminal type: Grbl")
                        elif option == 0x1F:  # Window size
                            # Send window size (80x24)
                            response = bytes([0xFF, 0xFA, 0x1F, 0x00, 0x50, 0x00, 0x18, 0xFF, 0xF0])  # 80x24
                            client_socket.send(response)
                            self.logger.info("Sent window size: 80x24")
        except Exception as e:
            self.logger.warning(f"Error handling telnet command: {e}")
    
    def _handle_initial_negotiation(self, client_socket: socket.socket):
        """Handle initial protocol negotiation for RFC2217 compatibility."""
        try:
            # Wait for initial data from client
            client_socket.settimeout(1.0)  # Short timeout for negotiation
            
            # Try to receive initial data
            data = client_socket.recv(1024)
            if data:
                # Check if this is telnet negotiation
                if data.startswith(b'\xff\xfd'):  # IAC WILL
                    self.logger.info("Client requesting telnet negotiation")
                    self._handle_telnet_command(client_socket, data)
                elif data.startswith(b'\xff\xfb'):  # IAC DO
                    self.logger.info("Client offering telnet options")
                    self._handle_telnet_command(client_socket, data)
                else:
                    # Regular data - store it for processing in main loop
                    # We'll handle this in the main receive loop
                    pass
            
            # Reset timeout for normal operation
            client_socket.settimeout(30.0)
            
        except socket.timeout:
            # No initial data - that's fine, continue with normal operation
            pass
        except Exception as e:
            self.logger.warning(f"Error in initial negotiation: {e}")
    
    def _send_response(self, client_socket: socket.socket, message: str):
        """Send response to client."""
        try:
            response = message + '\n'
            client_socket.send(response.encode('utf-8'))
            self.logger.info(f"Sent: {message}")
        except socket.error as e:
            self.logger.error(f"Failed to send response: {e}")
    
    def _process_command(self, command: str) -> Optional[str]:
        """Process a G-code command and return response."""
        self.commands_processed += 1
        
        # Handle special commands
        if command == '?':
            return self._handle_status_query()
        elif command.startswith('$'):
            return self._handle_dollar_commands(command)
        elif command == '!':
            return self._handle_feed_hold()
        elif command == '~':
            return self._handle_cycle_start()
        elif command == '^X':
            return self._handle_soft_reset()
        
        # Parse G-code command
        parsed = self.parser.parse_line(command)
        
        # Handle G codes
        for g_code in parsed['g_codes']:
            handler_key = f"G{g_code}"
            if handler_key in self.command_handlers:
                response = self.command_handlers[handler_key](parsed)
                if response:
                    return response
        
        # Handle M codes
        for m_code in parsed['m_codes']:
            handler_key = f"M{m_code}"
            if handler_key in self.command_handlers:
                response = self.command_handlers[handler_key](parsed)
                if response:
                    return response
        
        # Default response for unknown commands
        return "ok"
    
    def _handle_status_query(self) -> str:
        """Handle status query (?) command."""
        # Format: <State|MPos:x,y,z|WPos:x,y,z|Buf:15|FS:1000,0>
        wpos = self._get_work_position()
        return f"<{self.state.value}|MPos:{self.position.x:.3f},{self.position.y:.3f},{self.position.z:.3f}|WPos:{wpos.x:.3f},{wpos.y:.3f},{wpos.z:.3f}|Buf:15|FS:{self.settings.feed_rate:.0f},{self.settings.spindle_speed}>"
    
    def _handle_dollar_commands(self, command: str) -> str:
        """Handle $ configuration commands."""
        if command == '$':
            return self._get_help_text()
        elif command == '$#':
            return self._get_parameters()
        elif command == '$G':
            return self._get_parser_state()
        elif command == '$I':
            return self._get_build_info()
        elif command == '$N':
            return self._get_startup_blocks()
        elif command == '$X':
            return self._handle_alarm_lock()
        else:
            return "ok"
    
    def _handle_feed_hold(self) -> str:
        """Handle feed hold (!) command."""
        if self.state == MachineState.RUN:
            self.state = MachineState.HOLD
            self.logger.info("Feed hold activated")
        return "ok"
    
    def _handle_cycle_start(self) -> str:
        """Handle cycle start (~) command."""
        if self.state == MachineState.HOLD:
            self.state = MachineState.RUN
            self.logger.info("Cycle start - resuming")
        return "ok"
    
    def _handle_soft_reset(self) -> str:
        """Handle soft reset (^X) command."""
        self.state = MachineState.IDLE
        self.logger.info("Soft reset - machine idle")
        return "ok"
    
    def _handle_alarm_lock(self) -> str:
        """Handle alarm lock ($X) command."""
        if self.state == MachineState.ALARM:
            self.state = MachineState.IDLE
            self.logger.info("Alarm lock cleared")
        return "ok"
    
    def _handle_rapid_move(self, parsed: Dict[str, Any]) -> str:
        """Handle G0 rapid move command."""
        self._update_position(parsed['coordinates'])
        self.logger.info(f"Rapid move to {self.position}")
        return "ok"
    
    def _handle_linear_move(self, parsed: Dict[str, Any]) -> str:
        """Handle G1 linear move command."""
        if parsed['feed_rate']:
            self.settings.feed_rate = parsed['feed_rate']
        
        self._update_position(parsed['coordinates'])
        self.logger.info(f"Linear move to {self.position} at {self.settings.feed_rate} mm/min")
        return "ok"
    
    def _handle_arc_move_cw(self, parsed: Dict[str, Any]) -> str:
        """Handle G2 clockwise arc move command."""
        self._update_position(parsed['coordinates'])
        self.logger.info(f"CW arc move to {self.position}")
        return "ok"
    
    def _handle_arc_move_ccw(self, parsed: Dict[str, Any]) -> str:
        """Handle G3 counter-clockwise arc move command."""
        self._update_position(parsed['coordinates'])
        self.logger.info(f"CCW arc move to {self.position}")
        return "ok"
    
    def _handle_dwell(self, parsed: Dict[str, Any]) -> str:
        """Handle G4 dwell command."""
        dwell_time = parsed['coordinates'].get('P', 0)  # P parameter for dwell time
        self.logger.info(f"Dwell for {dwell_time} seconds")
        time.sleep(dwell_time)
        return "ok"
    
    def _handle_set_coordinate_offset(self, parsed: Dict[str, Any]) -> str:
        """Handle G10 set coordinate offset command."""
        # G10 L2 P1 X0 Y0 Z0 - set coordinate system offset
        self.logger.info("Coordinate offset set")
        return "ok"
    
    def _handle_plane_selection_xy(self, parsed: Dict[str, Any]) -> str:
        """Handle G17 XY plane selection."""
        self.settings.plane_selection = "XY"
        self.logger.info("Plane selection: XY")
        return "ok"
    
    def _handle_plane_selection_xz(self, parsed: Dict[str, Any]) -> str:
        """Handle G18 XZ plane selection."""
        self.settings.plane_selection = "XZ"
        self.logger.info("Plane selection: XZ")
        return "ok"
    
    def _handle_plane_selection_yz(self, parsed: Dict[str, Any]) -> str:
        """Handle G19 YZ plane selection."""
        self.settings.plane_selection = "YZ"
        self.logger.info("Plane selection: YZ")
        return "ok"
    
    def _handle_units_inches(self, parsed: Dict[str, Any]) -> str:
        """Handle G20 inches units."""
        self.settings.units = "inches"
        self.logger.info("Units set to inches")
        return "ok"
    
    def _handle_units_mm(self, parsed: Dict[str, Any]) -> str:
        """Handle G21 mm units."""
        self.settings.units = "mm"
        self.logger.info("Units set to mm")
        return "ok"
    
    def _handle_home(self, parsed: Dict[str, Any]) -> str:
        """Handle G28 home command."""
        self.position = MachinePosition()  # Reset to origin
        self.state = MachineState.HOME
        self.logger.info("Homing - position reset to origin")
        return "ok"
    
    def _handle_absolute_positioning(self, parsed: Dict[str, Any]) -> str:
        """Handle G90 absolute positioning."""
        self.settings.absolute_positioning = True
        self.logger.info("Absolute positioning mode")
        return "ok"
    
    def _handle_relative_positioning(self, parsed: Dict[str, Any]) -> str:
        """Handle G91 relative positioning."""
        self.settings.absolute_positioning = False
        self.logger.info("Relative positioning mode")
        return "ok"
    
    def _handle_set_position(self, parsed: Dict[str, Any]) -> str:
        """Handle G92 set position command."""
        coords = parsed['coordinates']
        if 'X' in coords:
            self.position.x = coords['X']
        if 'Y' in coords:
            self.position.y = coords['Y']
        if 'Z' in coords:
            self.position.z = coords['Z']
        if 'A' in coords:
            self.position.a = coords['A']
        if 'B' in coords:
            self.position.b = coords['B']
        if 'C' in coords:
            self.position.c = coords['C']
        
        self.logger.info(f"Position set to: {self.position}")
        return "ok"
    
    def _handle_program_stop(self, parsed: Dict[str, Any]) -> str:
        """Handle M0 program stop."""
        self.state = MachineState.HOLD
        self.logger.info("Program stop (M0)")
        return "ok"
    
    def _handle_program_end(self, parsed: Dict[str, Any]) -> str:
        """Handle M2 program end."""
        self.state = MachineState.IDLE
        self.logger.info("Program end (M2)")
        return "ok"
    
    def _handle_spindle_cw(self, parsed: Dict[str, Any]) -> str:
        """Handle M3 spindle clockwise."""
        if parsed['spindle_speed']:
            self.settings.spindle_speed = parsed['spindle_speed']
        self.logger.info(f"Spindle CW at {self.settings.spindle_speed} RPM")
        return "ok"
    
    def _handle_spindle_ccw(self, parsed: Dict[str, Any]) -> str:
        """Handle M4 spindle counter-clockwise."""
        if parsed['spindle_speed']:
            self.settings.spindle_speed = parsed['spindle_speed']
        self.logger.info(f"Spindle CCW at {self.settings.spindle_speed} RPM")
        return "ok"
    
    def _handle_spindle_stop(self, parsed: Dict[str, Any]) -> str:
        """Handle M5 spindle stop."""
        self.settings.spindle_speed = 0
        self.logger.info("Spindle stopped")
        return "ok"
    
    def _handle_program_end_rewind(self, parsed: Dict[str, Any]) -> str:
        """Handle M30 program end and rewind."""
        self.state = MachineState.IDLE
        self.logger.info("Program end and rewind (M30)")
        return "ok"
    
    def _update_position(self, coordinates: Dict[str, float]):
        """Update machine position based on coordinates."""
        for axis, value in coordinates.items():
            if axis == 'X':
                if self.settings.absolute_positioning:
                    self.position.x = value
                else:
                    self.position.x += value
            elif axis == 'Y':
                if self.settings.absolute_positioning:
                    self.position.y = value
                else:
                    self.position.y += value
            elif axis == 'Z':
                if self.settings.absolute_positioning:
                    self.position.z = value
                else:
                    self.position.z += value
            elif axis == 'A':
                if self.settings.absolute_positioning:
                    self.position.a = value
                else:
                    self.position.a += value
            elif axis == 'B':
                if self.settings.absolute_positioning:
                    self.position.b = value
                else:
                    self.position.b += value
            elif axis == 'C':
                if self.settings.absolute_positioning:
                    self.position.c = value
                else:
                    self.position.c += value
    
    def _get_work_position(self) -> MachinePosition:
        """Calculate work position (machine position - work coordinate offset)."""
        return MachinePosition(
            x=self.position.x - self.settings.work_coordinate_offset.x,
            y=self.position.y - self.settings.work_coordinate_offset.y,
            z=self.position.z - self.settings.work_coordinate_offset.z,
            a=self.position.a - self.settings.work_coordinate_offset.a,
            b=self.position.b - self.settings.work_coordinate_offset.b,
            c=self.position.c - self.settings.work_coordinate_offset.c
        )
    
    def _get_help_text(self) -> str:
        """Get help text for $ commands."""
        return """$ - View Grbl settings
$# - View # parameters
$G - View parser state
$I - View build info
$N - View startup blocks
$X - Kill alarm lock
$$ - View Grbl settings (verbose)
$H - Run homing cycle
$RST=* - Restore all Grbl settings
$RST=$ - Restore $$ settings
$RST=# - Restore # parameters
$RST=* - Restore all settings and parameters"""
    
    def _get_parameters(self) -> str:
        """Get # parameters."""
        return f"[G54:0.000,0.000,0.000]\n[G55:0.000,0.000,0.000]\n[G56:0.000,0.000,0.000]\n[G57:0.000,0.000,0.000]\n[G58:0.000,0.000,0.000]\n[G59:0.000,0.000,0.000]\n[G28:0.000,0.000,0.000]\n[G30:0.000,0.000,0.000]\n[G92:0.000,0.000,0.000]\n[TLO:0.000]\n[PRB:0.000,0.000,0.000:0]"
    
    def _get_parser_state(self) -> str:
        """Get parser state."""
        return f"[GC:G0 G54 G17 G21 G90 G94 M0 M5 T0 F0 S0]"
    
    def _get_build_info(self) -> str:
        """Get build information."""
        return "[VER:1.1h.20210325:GRBL Server Simulator]"
    
    def _get_startup_blocks(self) -> str:
        """Get startup blocks."""
        return "ok"
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get server statistics."""
        uptime = time.time() - self.start_time
        return {
            'uptime_seconds': uptime,
            'commands_processed': self.commands_processed,
            'bytes_received': self.bytes_received,
            'current_position': self.position,
            'machine_state': self.state.value,
            'active_clients': len(self.clients)
        }


def main():
    """Main function to run the GRBL server."""
    import argparse
    
    parser = argparse.ArgumentParser(description='GRBL Server - CNC Control Simulator')
    parser.add_argument('--host', default='localhost', help='Host to bind to (default: localhost)')
    parser.add_argument('--port', type=int, default=2217, help='Port to bind to (default: 2217)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Create and start server
    server = GRBLServer(host=args.host, port=args.port)
    
    if args.verbose:
        server.logger.setLevel(logging.DEBUG)
    
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down GRBL server...")
        server.stop()


if __name__ == '__main__':
    main()
