#!/usr/bin/env python3
"""
Simple Jogger Module - Curses Only (No External Permissions Required)

A simplified jogger interface using only curses for input handling.
No system permissions required - works out of the box on macOS.

Controls:
- Arrow keys: X/Y movement (1mm default, 0.1mm with Shift)
- U/J keys: Z movement (1mm default, 0.1mm with Shift)  
- E/D keys: E (extruder) movement (1mm default, 0.1mm with Shift)
- Mouse: Click the on‑screen D‑pad (X/Y) and Z± / E± buttons; wheel is ignored
- Special buttons: Home XY, Save Z to zero, E to zero

Author: AI Assistant
"""

import curses
import time
import sys
from typing import Dict, Any


def debug_print(message):
    """Print debug message to stderr so it's not captured by curses."""
    print(f"DEBUG: {message}", file=sys.stderr)
    sys.stderr.flush()


class SimpleJoggerController:
    """Simple jogger controller using only curses (no external permissions)."""
    
    def __init__(self):
        debug_print("Initializing jogger controller...")
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}
        self.step_size = 1.0  # Default step size
        self.fine_step_size = 0.1  # Fine step size
        self.feed_rate = 1000.0  # Default feed rate
        self.is_running = False
        self.debug_mode = False  # Enable debug mode to test wheel behavior
        self.screen = None
        self.stdscr = None

        # Clickable regions for mouse support (populated in draw)
        self.dpad_regions = {}
        self.input_regions = {}  # For input field and X button
        self.button_regions = {}  # For special function buttons
        
        # G-code command history
        self.gcode_history = []
        
        # Manual G-code input mode
        self.input_mode = False
        self.input_buffer = ""
        self.input_cursor_pos = 0
        
        # Modifier key tracking
        self.shift_pressed = False
        
        # UI colors and styling
        self.colors = {
            'title': 1,
            'position': 2,
            'controls': 3,
            'buttons': 4,
            'status': 5,
            'gcode': 6,
            'input': 7
        }
        debug_print("Jogger controller initialization completed")
    
    def init_curses(self, stdscr):
        """Initialize curses screen and colors."""
        self.stdscr = stdscr
        self.screen = stdscr
        
        try:
            # Enable colors
            curses.start_color()
            curses.use_default_colors()
            
            # Define color pairs
            curses.init_pair(1, curses.COLOR_CYAN, -1)      # Title
            curses.init_pair(2, curses.COLOR_GREEN, -1)     # Position
            curses.init_pair(3, curses.COLOR_YELLOW, -1)    # Controls
            curses.init_pair(4, curses.COLOR_MAGENTA, -1)   # Buttons
            curses.init_pair(5, curses.COLOR_RED, -1)       # Status
            curses.init_pair(6, curses.COLOR_WHITE, -1)     # G-code
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Input field
            
            # Hide cursor
            curses.curs_set(0)
            
            # Enable keypad
            stdscr.keypad(True)
            stdscr.nodelay(True)
            
            # Try to enable mouse events (may fail on some terminals)
            try:
                curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
                curses.mouseinterval(0)
                debug_print("Mouse events enabled")
            except curses.error:
                debug_print("Mouse events not supported in this terminal")
            
            debug_print("Curses initialized successfully")
            
        except curses.error as e:
            debug_print(f"Curses initialization error: {e}")
            raise

    def draw_dpad(self, top_y: int, left_x: int):
        """Draw a simple D‑pad (clickable) and record its regions."""
        # Clear any previous regions
        self.dpad_regions = {}

        # D‑pad layout sizing
        center_y = top_y + 2
        center_x = left_x + 6

        # Up arrow
        self.screen.addstr(top_y, left_x + 6, "▲")
        self.dpad_regions['up'] = (top_y, left_x + 6, top_y, left_x + 6)

        # Left / Right
        self.screen.addstr(top_y + 2, left_x, "◀")
        self.dpad_regions['left'] = (top_y + 2, left_x, top_y + 2, left_x)

        self.screen.addstr(top_y + 2, left_x + 12, "▶")
        self.dpad_regions['right'] = (top_y + 2, left_x + 12, top_y + 2, left_x + 12)

        # Down arrow
        self.screen.addstr(top_y + 4, left_x + 6, "▼")
        self.dpad_regions['down'] = (top_y + 4, left_x + 6, top_y + 4, left_x + 6)

        # Labels
        self.screen.addstr(top_y + 2, left_x + 6, "●")  # center dot

        # Z and E buttons to the right of D‑pad
        btn_x = left_x + 16
        self.screen.addstr(top_y, btn_x,   "Z+")
        self.screen.addstr(top_y + 2, btn_x, "Z-")
        self.screen.addstr(top_y + 4, btn_x, "E+")
        self.screen.addstr(top_y + 6, btn_x, "E-")
        self.dpad_regions['z_up']   = (top_y, btn_x,   top_y, btn_x + 1)
        self.dpad_regions['z_down'] = (top_y + 2, btn_x, top_y + 2, btn_x + 1)
        self.dpad_regions['e_up']   = (top_y + 4, btn_x, top_y + 4, btn_x + 1)
        self.dpad_regions['e_down'] = (top_y + 6, btn_x, top_y + 6, btn_x + 1)

    def point_in_region(self, y: int, x: int, region: tuple) -> bool:
        y1, x1, y2, x2 = region
        return y1 <= y <= y2 and x1 <= x <= x2
    
    def safe_addstr(self, y: int, x: int, text: str, attr=0):
        """Safely add string to screen, checking boundaries."""
        if not self.screen:
            return
        height, width = self.screen.getmaxyx()
        if 0 <= y < height and 0 <= x < width and x + len(text) < width:
            try:
                self.screen.addstr(y, x, text, attr)
            except curses.error:
                pass  # Ignore curses errors

    def draw_ui(self):
        """Draw the main UI interface."""
        if not self.screen:
            debug_print("No screen available for drawing")
            return
            
        try:
            self.screen.clear()
            height, width = self.screen.getmaxyx()
            debug_print(f"Drawing UI, screen size: {width}x{height}")
            
            # Check if terminal is too small
            if height < 15 or width < 60:
                debug_print(f"Terminal too small: {width}x{height}")
                self.safe_addstr(0, 0, f"Terminal too small: {width}x{height}", 
                                curses.color_pair(self.colors['status']))
                self.safe_addstr(1, 0, "Need at least 60x15. Resize and restart.", 
                                curses.color_pair(self.colors['status']))
                self.screen.refresh()
                return
        except Exception as e:
            debug_print(f"Error in draw_ui: {e}")
            return
        
        # Title
        title = "🎮 Simple CNC/3D Printer Jogger 🎮"
        title_x = max(0, (width - len(title)) // 2)
        if title_x + len(title) < width:
            self.screen.addstr(1, title_x, title, 
                              curses.color_pair(self.colors['title']) | curses.A_BOLD)
        
        # Position display
        pos_y = 4
        self.safe_addstr(pos_y, 2, "📍 Current Position:", 
                        curses.color_pair(self.colors['position']) | curses.A_BOLD)
        
        pos_text = f"X: {self.position['x']:8.3f}mm  Y: {self.position['y']:8.3f}mm"
        self.safe_addstr(pos_y + 1, 4, pos_text, curses.color_pair(self.colors['position']))
        
        pos_text = f"Z: {self.position['z']:8.3f}mm  E: {self.position['e']:8.3f}mm"
        self.safe_addstr(pos_y + 2, 4, pos_text, curses.color_pair(self.colors['position']))
        
        # Step size indicator
        step_text = f"Step Size: {self.step_size:.2f}mm"
        if self.step_size == self.fine_step_size:
            step_text += " (FINE)"
        self.safe_addstr(pos_y + 4, 2, step_text, curses.color_pair(self.colors['status']))
        
        # Debug mode indicator
        debug_text = f"Debug Mode: {'ON' if self.debug_mode else 'OFF'}"
        self.screen.addstr(pos_y + 5, 2, debug_text, curses.color_pair(self.colors['status']))
        
        # Controls section
        controls_y = pos_y + 7
        self.screen.addstr(controls_y, 2, "🎯 Controls:", 
                          curses.color_pair(self.colors['controls']) | curses.A_BOLD)

        # On‑screen D‑pad with mouse support
        self.screen.addstr(controls_y, width - 28, "🕹 D‑pad (clickable)", 
                          curses.color_pair(self.colors['controls']) | curses.A_BOLD)
        self.draw_dpad(controls_y + 1, width - 28)
        
        controls = [
            "Arrow Keys: Move X/Y axes",
            "U/J Keys:   Move Z axis (Up/Down)",
            "E/D Keys:   Move E axis (Extrude/Retract)",
            "Shift+Arrows: 10x finer X/Y movement",
            "Shift+U/J:  10x finer Z movement",
            "Shift+E/D:  10x finer E movement",
            "Space:      Toggle step size (1.0mm ↔ 0.1mm)",
            "B:          Toggle debug mode",
            "Mouse:      Click D‑pad arrows / Z± / E±; wheel is ignored"
        ]
        
        for i, control in enumerate(controls):
            self.screen.addstr(controls_y + 2 + i, 4, control, 
                              curses.color_pair(self.colors['controls']))
        
        # Special buttons
        buttons_y = controls_y + 8
        self.screen.addstr(buttons_y, 2, "🔧 Special Functions:", 
                          curses.color_pair(self.colors['buttons']) | curses.A_BOLD)
        
        buttons = [
            ("H", "Home XY axes"),
            ("Z", "Save current Z as zero"),
            ("R", "Reset E to zero"),
            ("I", "Toggle manual G-code input mode"),
            ("Q", "Quit jogger")
        ]
        
        # Clear button regions
        self.button_regions = {}
        
        for i, (key, description) in enumerate(buttons):
            button_text = f"{key} - {description}"
            self.screen.addstr(buttons_y + 2 + i, 4, button_text, 
                              curses.color_pair(self.colors['buttons']))
            
            # Define clickable region for this button
            start_x = 4
            end_x = start_x + len(button_text) - 1
            self.button_regions[key.lower()] = (buttons_y + 2 + i, start_x, buttons_y + 2 + i, end_x)
        
        # Manual G-code input section
        input_y = buttons_y + 7
        self.screen.addstr(input_y, 2, "⌨️  Manual G-code Input:", 
                          curses.color_pair(self.colors['gcode']) | curses.A_BOLD)
        
        # Input field
        input_field_y = input_y + 2
        input_field_width = min(60, width - 6)
        
        # Clear input regions
        self.input_regions = {}
        
        if self.input_mode:
            # Show input field with cursor and X button
            input_display = self.input_buffer[:input_field_width-2]
            input_text = "> " + input_display
            self.screen.addstr(input_field_y, 4, input_text, 
                              curses.color_pair(self.colors['input']))
            
            # Add X button to exit input mode
            x_button_x = 4 + input_field_width + 2
            if x_button_x < width - 2:
                self.screen.addstr(input_field_y, x_button_x, " [X]", 
                                  curses.color_pair(self.colors['buttons']) | curses.A_BOLD)
                # Define clickable region for X button (including the brackets)
                self.input_regions['exit'] = (input_field_y, x_button_x, input_field_y, x_button_x + 3)
                print(f"DEBUG: X button region defined at ({input_field_y}, {x_button_x}) to ({input_field_y}, {x_button_x + 3})")
            
            # Define clickable region for input field
            self.input_regions['input'] = (input_field_y, 4, input_field_y, 4 + input_field_width - 1)
            
            # Position cursor more carefully
            cursor_x = 6 + min(self.input_cursor_pos, input_field_width-3)
            if cursor_x < width - 1:  # Ensure cursor is within screen bounds
                try:
                    self.screen.move(input_field_y, cursor_x)
                except curses.error:
                    pass  # Ignore cursor positioning errors
        else:
            # Show prompt to enter input mode (clickable)
            prompt_text = "Press 'I' or click here to enter manual G-code input mode"
            self.screen.addstr(input_field_y, 4, prompt_text, 
                              curses.color_pair(self.colors['controls']))
            # Define clickable region for prompt
            self.input_regions['activate'] = (input_field_y, 4, input_field_y, 4 + len(prompt_text) - 1)
        
        # G-code output section
        gcode_y = input_y + 5
        self.screen.addstr(gcode_y, 2, "📝 G-code Commands:", 
                          curses.color_pair(self.colors['gcode']) | curses.A_BOLD)
        
        # Show last few G-code commands
        max_gcode_lines = min(6, height - gcode_y - 3)
        start_idx = max(0, len(self.gcode_history) - max_gcode_lines)
        
        for i, gcode in enumerate(self.gcode_history[start_idx:]):
            if gcode_y + 2 + i < height - 1:
                self.screen.addstr(gcode_y + 2 + i, 4, f"{gcode}", 
                                  curses.color_pair(self.colors['gcode']))
        
        # Status line
        if self.input_mode:
            status_text = "INPUT MODE: Type G-code and press Enter to execute | Press Escape or click [X] to exit input mode"
        else:
            status_text = "Press 'q' to quit | U/J for Z | Shift+Arrows for 10x finer X/Y | Shift+U/J for 10x finer Z | Space toggles step size | Click any button or press key | Mouse: click D‑pad; wheel ignored"
        if height > 0 and width > len(status_text):
            self.screen.addstr(height - 1, 0, status_text, 
                              curses.color_pair(self.colors['status']))
        
        self.screen.refresh()
    
    def generate_gcode(self, axis: str, distance: float) -> str:
        """Generate G-code command for movement."""
        if axis.upper() == 'E':
            # E axis movement
            return f"G1 E{distance:+.3f} F{self.feed_rate:.0f}"
        else:
            # X, Y, Z axis movement
            return f"G1 {axis.upper()}{distance:+.3f} F{self.feed_rate:.0f}"
    
    def move_axis(self, axis: str, distance: float):
        """Move specified axis by distance and generate G-code."""
        self.position[axis.lower()] += distance
        
        gcode = self.generate_gcode(axis, distance)
        self.gcode_history.append(gcode)
        
        # Keep only last 20 commands
        if len(self.gcode_history) > 20:
            self.gcode_history.pop(0)
        
        print(f"Generated G-code: {gcode}")
        self.draw_ui()
    
    def home_xy(self):
        """Home X and Y axes."""
        self.position['x'] = 0.0
        self.position['y'] = 0.0
        
        gcode = "G28 X Y"
        self.gcode_history.append(gcode)
        print(f"Generated G-code: {gcode}")
        self.draw_ui()
    
    def save_z_zero(self):
        """Save current Z position as zero."""
        self.position['z'] = 0.0
        
        gcode = "G92 Z0"
        self.gcode_history.append(gcode)
        print(f"Generated G-code: {gcode}")
        self.draw_ui()
    
    def reset_e_zero(self):
        """Reset E position to zero."""
        self.position['e'] = 0.0
        
        gcode = "G92 E0"
        self.gcode_history.append(gcode)
        print(f"Generated G-code: {gcode}")
        self.draw_ui()
    
    def toggle_step_size(self):
        """Toggle between coarse and fine step sizes."""
        if self.step_size == 1.0:
            self.step_size = self.fine_step_size
        else:
            self.step_size = 1.0
        self.draw_ui()
    
    def toggle_debug_mode(self):
        """Toggle debug mode on/off."""
        self.debug_mode = not self.debug_mode
        print(f"DEBUG: Debug mode {'enabled' if self.debug_mode else 'disabled'}")
        self.draw_ui()
    
    def toggle_input_mode(self):
        """Toggle manual G-code input mode on/off."""
        self.input_mode = not self.input_mode
        if self.input_mode:
            self.input_buffer = ""
            self.input_cursor_pos = 0
            curses.curs_set(1)  # Show cursor for input
        else:
            curses.curs_set(0)  # Hide cursor
        print(f"Manual G-code input mode {'enabled' if self.input_mode else 'disabled'}")
        self.draw_ui()
    
    def execute_gcode(self, gcode: str):
        """Execute a G-code command and add to history."""
        gcode = gcode.strip()
        if not gcode:
            return
        
        # Add to history
        self.gcode_history.append(gcode)
        
        # Keep only last 20 commands
        if len(self.gcode_history) > 20:
            self.gcode_history.pop(0)
        
        # Basic G-code parsing for position updates
        gcode_upper = gcode.upper()
        if gcode_upper.startswith('G1') or gcode_upper.startswith('G0'):
            # Parse movement commands for position tracking
            parts = gcode_upper.split()
            for part in parts[1:]:  # Skip the G1/G0 part
                if part.startswith('X'):
                    try:
                        self.position['x'] = float(part[1:])
                    except ValueError:
                        pass
                elif part.startswith('Y'):
                    try:
                        self.position['y'] = float(part[1:])
                    except ValueError:
                        pass
                elif part.startswith('Z'):
                    try:
                        self.position['z'] = float(part[1:])
                    except ValueError:
                        pass
                elif part.startswith('E'):
                    try:
                        self.position['e'] = float(part[1:])
                    except ValueError:
                        pass
        
        print(f"Executed G-code: {gcode}")
        # Don't call draw_ui() here - let the caller handle it
    
    def handle_modifier_keys(self, key):
        """Handle modifier key detection."""
        # Check for Shift key combinations
        if key == curses.KEY_SR or key == curses.KEY_SF or key == curses.KEY_SLEFT or key == curses.KEY_SRIGHT:
            self.shift_pressed = True
            print(f"DEBUG: Shift arrow detected, key={key}")
            return True
        elif key == ord('U') or key == ord('J') or key == ord('E') or key == ord('D'):
            # These are uppercase keys, likely Shift+lowercase
            self.shift_pressed = True
            print(f"DEBUG: Uppercase key detected, key={key} ({chr(key)})")
            return True
        else:
            self.shift_pressed = False
            return False
    
    def handle_input_mode(self, key):
        """Handle key input when in input mode."""
        if key == ord('\n') or key == ord('\r'):  # Enter key
            # Clear input buffer first
            current_input = self.input_buffer
            self.input_buffer = ""
            self.input_cursor_pos = 0
            
            # Execute command if not empty
            if current_input.strip():
                self.execute_gcode(current_input)
            
            # Redraw UI to show cleared input
            self.draw_ui()
            return True
        elif key == 27:  # Escape key - exit input mode
            self.input_mode = False
            curses.curs_set(0)
            self.input_buffer = ""
            self.input_cursor_pos = 0
            self.draw_ui()
            return True
        elif key == curses.KEY_BACKSPACE or key == 127:  # Backspace
            if self.input_cursor_pos > 0:
                self.input_buffer = self.input_buffer[:self.input_cursor_pos-1] + self.input_buffer[self.input_cursor_pos:]
                self.input_cursor_pos -= 1
                self.draw_ui()
        elif key == curses.KEY_LEFT:  # Left arrow
            if self.input_cursor_pos > 0:
                self.input_cursor_pos -= 1
                self.draw_ui()
        elif key == curses.KEY_RIGHT:  # Right arrow
            if self.input_cursor_pos < len(self.input_buffer):
                self.input_cursor_pos += 1
                self.draw_ui()
        elif key == curses.KEY_HOME:  # Home key
            self.input_cursor_pos = 0
            self.draw_ui()
        elif key == curses.KEY_END:  # End key
            self.input_cursor_pos = len(self.input_buffer)
            self.draw_ui()
        elif key == curses.KEY_DC:  # Delete key
            if self.input_cursor_pos < len(self.input_buffer):
                self.input_buffer = self.input_buffer[:self.input_cursor_pos] + self.input_buffer[self.input_cursor_pos+1:]
                self.draw_ui()
        elif 32 <= key <= 126:  # Printable characters
            self.input_buffer = (self.input_buffer[:self.input_cursor_pos] + 
                               chr(key) + 
                               self.input_buffer[self.input_cursor_pos:])
            self.input_cursor_pos += 1
            self.draw_ui()
        
        return True
    
    def handle_mouse(self):
        try:
            _id, mx, my, _z, bstate = curses.getmouse()
        except curses.error:
            return

        # Ignore wheel to prevent accidental jogs, but consume the events so they don't become arrows
        if bstate & (getattr(curses, 'BUTTON4_PRESSED', 0) | getattr(curses, 'BUTTON5_PRESSED', 0)):
            return

        # Left click on regions
        if bstate & getattr(curses, 'BUTTON1_PRESSED', 0):
            # print(f"DEBUG: Mouse click at ({my}, {mx})")
            # print(f"DEBUG: Available input regions: {list(self.input_regions.keys())}")
            # print(f"DEBUG: Available button regions: {list(self.button_regions.keys())}")
            # for name, rect in self.input_regions.items():
            #     print(f"DEBUG: Checking input region '{name}': {rect}")
            # for name, rect in self.button_regions.items():
            #     print(f"DEBUG: Checking button region '{name}': {rect}")
            
            # Check D-pad regions first (highest priority for movement)
            for name, rect in self.dpad_regions.items():
                if self.point_in_region(my, mx, rect):
                    if name == 'up':
                        self.move_axis('y', self.step_size)
                    elif name == 'down':
                        self.move_axis('y', -self.step_size)
                    elif name == 'left':
                        self.move_axis('x', -self.step_size)
                    elif name == 'right':
                        self.move_axis('x', self.step_size)
                    elif name == 'z_up':
                        self.move_axis('z', self.step_size)
                    elif name == 'z_down':
                        self.move_axis('z', -self.step_size)
                    elif name == 'e_up':
                        self.move_axis('e', self.step_size)
                    elif name == 'e_down':
                        self.move_axis('e', -self.step_size)
                    return
            
            # Check input field regions
            for name, rect in self.input_regions.items():
                if self.point_in_region(my, mx, rect):
                    print(f"DEBUG: Clicked on input region '{name}' at ({my}, {mx})")
                    if name == 'activate':
                        self.toggle_input_mode()
                    elif name == 'exit':
                        print("DEBUG: Exiting input mode via X button")
                        self.input_mode = False
                        curses.curs_set(0)
                        self.input_buffer = ""
                        self.input_cursor_pos = 0
                        self.draw_ui()
                    elif name == 'input' and self.input_mode:
                        # Clicking in input field - already active, just focus
                        print("DEBUG: Clicked in input field")
                        pass
                    return
            
            # Check special function buttons
            for name, rect in self.button_regions.items():
                if self.point_in_region(my, mx, rect):
                    print(f"DEBUG: Clicked on button '{name}' at ({my}, {mx})")
                    if name == 'h':
                        self.home_xy()
                    elif name == 'z':
                        self.save_z_zero()
                    elif name == 'r':
                        self.reset_e_zero()
                    elif name == 'i':
                        self.toggle_input_mode()
                    elif name == 'q':
                        self.is_running = False
                    return
    
    def run(self, stdscr):
        """Main run loop."""
        debug_print("RUN METHOD CALLED - Starting jogger initialization...")
        try:
            debug_print("Starting jogger initialization...")
            self.init_curses(stdscr)
            self.is_running = True
            debug_print("Jogger initialized, drawing UI...")
            
            # Initial UI draw
            self.draw_ui()
            debug_print("UI drawn, entering main loop...")
            debug_print(f"is_running = {self.is_running}")
            
            # Main loop
            loop_count = 0
            debug_print("About to start main loop...")
            debug_print(f"is_running before loop: {self.is_running}")
            
            if not self.is_running:
                debug_print("ERROR: is_running is False before entering main loop!")
                return
            
            while self.is_running:
                debug_print(f"Main loop iteration {loop_count}, is_running = {self.is_running}")
                try:
                    loop_count += 1
                    if loop_count > 10:  # Limit debug output to first 10 iterations
                        debug_print("Main loop running (limiting debug output)...")
                        break
                    
                    key = stdscr.getch()
                    
                    # Debug: Print key codes to see what's being received
                    if key != -1:  # Only print non-empty key presses
                        debug_print(f"Received key code: {key} (type: {type(key)})")
                        if key == curses.KEY_UP:
                            debug_print("This is KEY_UP")
                        elif key == curses.KEY_DOWN:
                            debug_print("This is KEY_DOWN")
                    
                    # Handle input mode first
                    if self.input_mode:
                        if self.handle_input_mode(key):
                            continue
                    
                    # Handle mouse events (clickable D‑pad); wheel is ignored
                    if key == curses.KEY_MOUSE:
                        self.handle_mouse()
                        continue
                    
                    # Check for modifier keys
                    self.handle_modifier_keys(key)
                    
                    if key == ord('q') or key == ord('Q'):
                        break
                    elif key == ord(' '):
                        self.toggle_step_size()
                    elif key == ord('b') or key == ord('B'):
                        self.toggle_debug_mode()
                    elif key == ord('i') or key == ord('I'):
                        self.toggle_input_mode()
                    
                    # Arrow keys for X/Y movement (disabled in debug mode to test wheel behavior)
                    elif key == curses.KEY_UP:
                        if self.debug_mode:
                            print("DEBUG: Arrow UP detected - would move Y+ (disabled in debug mode)")
                        else:
                            self.move_axis('y', self.step_size)
                    elif key == curses.KEY_DOWN:
                        if self.debug_mode:
                            print("DEBUG: Arrow DOWN detected - would move Y- (disabled in debug mode)")
                        else:
                            self.move_axis('y', -self.step_size)
                    elif key == curses.KEY_LEFT:
                        if self.debug_mode:
                            print("DEBUG: Arrow LEFT detected - would move X- (disabled in debug mode)")
                        else:
                            self.move_axis('x', -self.step_size)
                    elif key == curses.KEY_RIGHT:
                        if self.debug_mode:
                            print("DEBUG: Arrow RIGHT detected - would move X+ (disabled in debug mode)")
                        else:
                            self.move_axis('x', self.step_size)
                    
                    # Shift + Arrow keys for 10x finer X/Y movement
                    elif key == curses.KEY_SR:  # Shift + Up
                        self.move_axis('y', self.step_size / 10)
                    elif key == curses.KEY_SF:  # Shift + Down
                        self.move_axis('y', -self.step_size / 10)
                    elif key == curses.KEY_SLEFT:  # Shift + Left
                        self.move_axis('x', -self.step_size / 10)
                    elif key == curses.KEY_SRIGHT:  # Shift + Right
                        self.move_axis('x', self.step_size / 10)
                    
                    # Z axis movement (U/J keys)
                    elif key == ord('u') or key == ord('U'):
                        if self.shift_pressed or key == ord('U'):
                            self.move_axis('z', self.step_size / 10)
                        else:
                            self.move_axis('z', self.step_size)
                    elif key == ord('j') or key == ord('J'):
                        if self.shift_pressed or key == ord('J'):
                            self.move_axis('z', -self.step_size / 10)
                        else:
                            self.move_axis('z', -self.step_size)
                    
                    # E axis movement
                    elif key == ord('e') or key == ord('E'):
                        if self.shift_pressed or key == ord('E'):
                            self.move_axis('e', self.step_size / 10)
                        else:
                            self.move_axis('e', self.step_size)
                    elif key == ord('d') or key == ord('D'):
                        if self.shift_pressed or key == ord('D'):
                            self.move_axis('e', -self.step_size / 10)
                        else:
                            self.move_axis('e', -self.step_size)
                    
                    # Special functions
                    elif key == ord('H'):
                        self.home_xy()
                    elif key == ord('Z'):
                        self.save_z_zero()
                    elif key == ord('R'):
                        self.reset_e_zero()
                    
                
                except curses.error:
                    pass
                
                # Adjust sleep time based on input mode
                if self.input_mode:
                    time.sleep(0.05)  # Slightly longer delay in input mode to reduce blinking
                else:
                    time.sleep(0.01)  # Normal delay for jogger mode
                
        except KeyboardInterrupt:
            debug_print("KeyboardInterrupt received")
            pass
        except curses.error as e:
            debug_print(f"Curses error in run method: {e}")
            print(f"Curses error: {e}")
            print("Try resizing your terminal window and restarting.")
        except Exception as e:
            debug_print(f"Unexpected error in run method: {e}")
            import traceback
            debug_print(f"Traceback: {traceback.format_exc()}")
            print(f"Unexpected error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            debug_print("Run method finally block - setting is_running to False")
            self.is_running = False


def test_curses():
    """Test if curses works at all in this terminal."""
    def test_run(stdscr):
        debug_print("Testing basic curses functionality...")
        try:
            stdscr.clear()
            stdscr.addstr(0, 0, "Curses test - press any key to continue")
            stdscr.refresh()
            debug_print("Test message displayed, waiting for key...")
            stdscr.getch()
            debug_print("Key received, test successful")
        except Exception as e:
            debug_print(f"Curses test failed: {e}")
            raise
    
    try:
        debug_print("Running basic curses test...")
        curses.wrapper(test_run)
        debug_print("Curses test completed successfully")
        return True
    except Exception as e:
        debug_print(f"Curses test failed: {e}")
        return False


def main():
    """Main entry point for the simple jogger."""
    print("Starting Simple CNC/3D Printer Jogger Controller...")
    print("No external permissions required - uses curses only!")
    print()
    
    # Check if we're in a proper terminal
    import os
    if not os.isatty(0):
        print("Error: Not running in a terminal. Please run from a terminal application.")
        return
    
    # Check TERM environment variable
    term = os.environ.get('TERM', '')
    print(f"DEBUG: TERM environment variable: '{term}'")
    
    if not term or term == 'dumb':
        print("Warning: TERM is not set or set to 'dumb'. This may cause issues.")
        print("Try running: export TERM=xterm-256color")
    
    # Test basic curses functionality first
    if not test_curses():
        print("Basic curses test failed. This terminal may not support curses.")
        return
    
    try:
        debug_print("Creating jogger controller...")
        jogger = SimpleJoggerController()
        debug_print("Jogger controller created successfully")
        print("DEBUG: About to call curses.wrapper...")
        
        # Create a wrapper function for curses.wrapper
        def run_wrapper(stdscr):
            debug_print("WRAPPER FUNCTION CALLED")
            return jogger.run(stdscr)
        
        curses.wrapper(run_wrapper)
        print("DEBUG: curses.wrapper completed")
    except curses.error as e:
        print(f"Curses error: {e}")
        print("This terminal may not support curses properly.")
        print("Try running in a different terminal or check your TERM environment variable.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
