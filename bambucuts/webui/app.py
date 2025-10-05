#!/usr/bin/env python3
"""
Bambu Cuts - Cutter and Plotter API

A Flask API backend for controlling Bambu Lab printers as CNC cutters/plotters.
Provides RESTful endpoints for printer control, jogging, and G-code execution.

Author: AI Assistant
"""

from flask import Flask, render_template, jsonify, request, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
import time
import sys
import os
import tempfile
from pathlib import Path
import threading
import base64
from io import BytesIO

try:
    import bambulabs_api as bl
    from bambucuts import config
    from bambucuts.compress_3mf import process_3mf
    from bambucuts.gcodetools import GCodeTools, CuttingParameters
    from bambucuts.dxf2svg import convert_dxf_to_svg
except ImportError as e:
    print(f"Error importing required modules: {e}")
    print("Make sure bambulabs_api is installed and bambucuts is available")
    import traceback
    traceback.print_exc()
    sys.exit(1)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a1plotter-secret-key'
CORS(app)  # Enable CORS for all routes
socketio = SocketIO(app, cors_allowed_origins="*")

# Global state
state = {
    'position': {'x': 0.0, 'y': 0.0, 'z': 0.0, 'e': 0.0},
    'step_size': 1.0,
    'feed_rate': 1000.0,
    'printer_connected': False,
    'connection_error': None,
    'gcode_history': [],
    'camera_streaming': False
}

# Printer instance
printer = None

# Camera streaming control
camera_thread = None
camera_stop_event = threading.Event()


def connect_printer():
    """Connect to the BambuLab printer."""
    global printer, state

    try:
        # Get current config values
        cfg = config._config_data
        print(f"Connecting to printer at {cfg['ip']}...")
        printer = bl.Printer(cfg['ip'], cfg['access_code'], cfg['serial'])
        printer.connect()
        time.sleep(2)  # Allow time for connection to establish
        state['printer_connected'] = True
        state['connection_error'] = None
        print("Successfully connected to printer")

        # Set relative mode for jogging
        set_relative_mode()

        return True
    except Exception as e:
        state['printer_connected'] = False
        state['connection_error'] = str(e)
        print(f"Failed to connect to printer: {e}")
        return False


def disconnect_printer():
    """Disconnect from the BambuLab printer."""
    global printer, state

    try:
        if printer and state['printer_connected']:
            # Stop camera if running
            stop_camera_stream()

            # Restore absolute mode before disconnecting
            set_absolute_mode()
            printer.disconnect()
            print("Disconnected from printer")
    except Exception as e:
        print(f"Error disconnecting from printer: {e}")
    finally:
        state['printer_connected'] = False
        printer = None


def camera_stream_worker():
    """Background worker thread for streaming camera frames."""
    global printer, camera_stop_event

    print("Camera stream worker started")

    try:
        # Start the camera
        if printer.camera_start():
            print("Camera started successfully")
            time.sleep(1)  # Give camera time to initialize

            while not camera_stop_event.is_set():
                try:
                    # Get camera frame (base64 encoded)
                    frame_base64 = printer.get_camera_frame()

                    if frame_base64:
                        # Process frame (placeholder for future CV work)
                        processed_frame = process_camera_frame(frame_base64)

                        # Emit frame to all connected clients
                        socketio.emit('camera_frame', {'frame': processed_frame}, namespace='/')

                    # Limit frame rate to ~10 FPS
                    time.sleep(0.1)

                except Exception as e:
                    print(f"Error streaming frame: {e}")
                    time.sleep(0.5)

            # Stop camera when done
            printer.camera_stop()
            print("Camera stopped")

        else:
            print("Failed to start camera")

    except Exception as e:
        print(f"Camera stream worker error: {e}")
    finally:
        state['camera_streaming'] = False
        print("Camera stream worker stopped")


def process_camera_frame(frame_base64: str) -> str:
    """
    Process camera frame for computer vision tasks.

    This is a placeholder function that can be extended with:
    - Object detection
    - Print monitoring
    - Quality control
    - Defect detection

    Args:
        frame_base64: Base64 encoded image from camera

    Returns:
        Processed frame as base64 string
    """
    # For now, just return the original frame
    # In the future, add CV processing here:
    # - OpenCV operations
    # - ML model inference
    # - Overlay graphics
    # - Measurements

    try:
        # Decode base64 to image
        # image_data = base64.b64decode(frame_base64)
        # img = Image.open(BytesIO(image_data))

        # TODO: Add CV processing here
        # Example: img = apply_filters(img)
        # Example: img = detect_objects(img)
        # Example: img = overlay_measurements(img)

        # Return processed frame
        # processed_buffer = BytesIO()
        # img.save(processed_buffer, format='JPEG')
        # return base64.b64encode(processed_buffer.getvalue()).decode('utf-8')

        return frame_base64  # Return original for now

    except Exception as e:
        print(f"Error processing camera frame: {e}")
        return frame_base64


def start_camera_stream():
    """Start camera streaming in background thread."""
    global camera_thread, camera_stop_event, state

    if state['camera_streaming']:
        print("Camera already streaming")
        return True

    if not printer or not state['printer_connected']:
        print("Printer not connected")
        return False

    # Reset stop event
    camera_stop_event.clear()

    # Start camera thread
    camera_thread = threading.Thread(target=camera_stream_worker, daemon=True)
    camera_thread.start()

    state['camera_streaming'] = True
    return True


def stop_camera_stream():
    """Stop camera streaming."""
    global camera_thread, camera_stop_event, state

    if not state['camera_streaming']:
        return

    print("Stopping camera stream...")
    camera_stop_event.set()

    if camera_thread:
        camera_thread.join(timeout=5)

    state['camera_streaming'] = False


def set_relative_mode():
    """Set printer to relative positioning mode for jogging."""
    if not state['printer_connected'] or not printer:
        return False

    try:
        printer.gcode("G91")
        print("Set printer to relative mode (G91)")
        return True
    except Exception as e:
        print(f"Failed to set relative mode: {e}")
        return False


def set_absolute_mode():
    """Set printer to absolute positioning mode."""
    if not state['printer_connected'] or not printer:
        return False

    try:
        printer.gcode("G90")
        print("Set printer to absolute mode (G90)")
        return True
    except Exception as e:
        print(f"Failed to set absolute mode: {e}")
        return False


def send_gcode_to_printer(gcode: str) -> bool:
    """Send G-code command to the printer."""
    if not state['printer_connected'] or not printer:
        print(f"Cannot send G-code - printer not connected: {gcode}")
        return False

    try:
        printer.gcode(gcode)
        print(f"G-code sent to printer: {gcode}")
        return True
    except Exception as e:
        print(f"Failed to send G-code to printer: {e}")
        state['connection_error'] = str(e)
        return False


def add_to_history(gcode: str):
    """Add G-code command to history."""
    state['gcode_history'].append(gcode)
    # Keep only last 20 commands
    if len(state['gcode_history']) > 20:
        state['gcode_history'].pop(0)


# Routes

@app.route('/')
def index():
    """Serve the main page."""
    return render_template('index.html')


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get current status including position and connection state."""
    printer_state = None
    camera_alive = False
    if state['printer_connected'] and printer:
        try:
            printer_state = str(printer.get_state())
            camera_alive = printer.camera_client_alive()
        except Exception as e:
            print(f"Error getting printer state: {e}")

    return jsonify({
        'position': state['position'],
        'step_size': state['step_size'],
        'printer_connected': state['printer_connected'],
        'connection_error': state['connection_error'],
        'printer_ip': config._config_data.get('ip', ''),
        'printer_state': printer_state,
        'camera_streaming': state['camera_streaming'],
        'camera_alive': camera_alive
    })


@app.route('/api/history', methods=['GET'])
def get_history():
    """Get G-code command history."""
    return jsonify({
        'history': state['gcode_history']
    })


@app.route('/api/connect', methods=['POST'])
def toggle_connection():
    """Connect or disconnect from printer."""
    if state['printer_connected']:
        disconnect_printer()
        return jsonify({
            'success': True,
            'connected': False,
            'message': 'Disconnected from printer'
        })
    else:
        success = connect_printer()
        return jsonify({
            'success': success,
            'connected': success,
            'message': 'Connected to printer' if success else f'Failed to connect: {state["connection_error"]}'
        })


@app.route('/api/move', methods=['POST'])
def move_axis():
    """Move specified axis by distance."""
    data = request.json
    axis = data.get('axis', '').lower()
    distance = float(data.get('distance', 0))

    if axis not in ['x', 'y', 'z', 'e']:
        return jsonify({'success': False, 'error': 'Invalid axis'}), 400

    # Update position
    state['position'][axis] += distance

    # Generate G-code
    gcode_relative = "G91"
    gcode_move = f"G1 {axis.upper()}{distance:.3f} F{state['feed_rate']:.0f}"

    # Add to history
    add_to_history(gcode_relative)
    add_to_history(gcode_move)

    # Send to printer if connected
    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode_relative) and send_gcode_to_printer(gcode_move)

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': [gcode_relative, gcode_move]
    })


@app.route('/api/home', methods=['POST'])
def home_xy():
    """Home X and Y axes."""
    # Reset position
    state['position']['x'] = 0.0
    state['position']['y'] = 0.0

    # For homing, temporarily switch to absolute mode
    if state['printer_connected']:
        set_absolute_mode()
        time.sleep(0.1)

    gcode = "G28 X Y"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)
        time.sleep(0.1)
        set_relative_mode()

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/save-z-zero', methods=['POST'])
def save_z_zero():
    """Save current Z position as zero."""
    state['position']['z'] = 0.0

    gcode = "G92 Z0"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/reset-e-zero', methods=['POST'])
def reset_e_zero():
    """Reset E position to zero."""
    state['position']['e'] = 0.0

    gcode = "G92 E0"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/move-z-absolute', methods=['POST'])
def move_z_absolute():
    """Move Z to absolute position."""
    data = request.json
    z_position = float(data.get('position', 0))

    state['position']['z'] = z_position

    # Switch to absolute mode
    if state['printer_connected']:
        set_absolute_mode()
        time.sleep(0.1)

    gcode = f"G1 Z{z_position:.1f} F600"
    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)
        time.sleep(0.1)
        set_relative_mode()

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/gcode', methods=['POST'])
def execute_gcode():
    """Execute custom G-code command."""
    data = request.json
    gcode = data.get('gcode', '').strip()

    if not gcode:
        return jsonify({'success': False, 'error': 'Empty G-code'}), 400

    add_to_history(gcode)

    success = True
    if state['printer_connected']:
        success = send_gcode_to_printer(gcode)

    # Try to parse position updates from G-code
    gcode_upper = gcode.upper()
    if gcode_upper.startswith('G1') or gcode_upper.startswith('G0'):
        parts = gcode_upper.split()
        for part in parts[1:]:
            if part.startswith('X'):
                try:
                    state['position']['x'] = float(part[1:])
                except ValueError:
                    pass
            elif part.startswith('Y'):
                try:
                    state['position']['y'] = float(part[1:])
                except ValueError:
                    pass
            elif part.startswith('Z'):
                try:
                    state['position']['z'] = float(part[1:])
                except ValueError:
                    pass
            elif part.startswith('E'):
                try:
                    state['position']['e'] = float(part[1:])
                except ValueError:
                    pass

    return jsonify({
        'success': success,
        'position': state['position'],
        'gcode': gcode
    })


@app.route('/api/step-size', methods=['POST'])
def set_step_size():
    """Set step size."""
    data = request.json
    step_size = float(data.get('step_size', 1.0))
    state['step_size'] = step_size

    return jsonify({
        'success': True,
        'step_size': state['step_size']
    })


@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration."""
    return jsonify({
        'ip': config._config_data.get('ip', ''),
        'serial': config._config_data.get('serial', ''),
        'access_code': config._config_data.get('access_code', '')
    })


@app.route('/api/config', methods=['POST'])
def save_config_endpoint():
    """Update printer configuration and reconnect."""
    global printer

    data = request.json

    print(f"Updating config with IP={data.get('ip')}, Serial={data.get('serial')}")

    success, message = config.update_config(
        ip=data.get('ip'),
        serial=data.get('serial'),
        access_code=data.get('access_code')
    )

    if success:
        print(f"Config saved. Current config: {config._config_data}")

        # Clear printer object and state completely
        printer = None
        state['printer_connected'] = False
        state['connection_error'] = None

        # Disconnect from old printer if still connected
        try:
            disconnect_printer()
        except Exception as e:
            print(f"Error disconnecting (may already be disconnected): {e}")

        # Wait a moment
        time.sleep(1)

        # Reconnect with new settings
        print("Attempting to reconnect with new settings...")
        reconnect_success = connect_printer()

        if reconnect_success:
            print("Reconnection successful!")
            return jsonify({
                'success': True,
                'message': f'{message}. Reconnected to printer successfully.'
            })
        else:
            print(f"Reconnection failed: {state.get('connection_error', 'Unknown error')}")
            return jsonify({
                'success': False,
                'error': f"Config saved but reconnection failed: {state.get('connection_error', 'Could not connect to printer')}",
                'connection_failed': True
            }), 400
    else:
        return jsonify({
            'success': False,
            'error': message
        }), 500


@app.route('/api/gcode/validate', methods=['POST'])
def validate_gcode():
    """Validate G-code syntax."""
    data = request.json
    gcode_text = data.get('gcode', '')

    errors = []
    warnings = []
    line_num = 0

    for line in gcode_text.split('\n'):
        line_num += 1
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith(';'):
            continue

        # Remove inline comments
        if ';' in line:
            line = line.split(';')[0].strip()

        # Basic G-code validation
        if not line[0].upper() in ['G', 'M', 'T', 'N']:
            errors.append(f"Line {line_num}: Invalid command start '{line[0]}'")
            continue

        # Check for common issues
        if line.upper().startswith('G') or line.upper().startswith('M'):
            # Check if there's a number after G/M
            if len(line) < 2 or not line[1].isdigit():
                errors.append(f"Line {line_num}: Missing command number")

    return jsonify({
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'line_count': line_num
    })


@app.route('/api/gcode/format', methods=['POST'])
def format_gcode():
    """Format G-code with proper spacing and comments."""
    data = request.json
    gcode_text = data.get('gcode', '')

    formatted_lines = []

    for line in gcode_text.split('\n'):
        line = line.strip()

        # Keep empty lines and comments as-is
        if not line or line.startswith(';'):
            formatted_lines.append(line)
            continue

        # Split command and comment
        if ';' in line:
            code_part, comment_part = line.split(';', 1)
            code_part = code_part.strip()
            comment_part = comment_part.strip()
            formatted_lines.append(f"{code_part:<20} ; {comment_part}")
        else:
            formatted_lines.append(line)

    return jsonify({
        'formatted': '\n'.join(formatted_lines)
    })


@app.route('/api/gcode/send-all', methods=['POST'])
def send_all_gcode():
    """Send all G-code lines from editor."""
    data = request.json
    gcode_text = data.get('gcode', '')

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to send'}), 400

    sent_count = 0
    errors = []

    for line_num, line in enumerate(gcode_text.split('\n'), 1):
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith(';'):
            continue

        # Remove inline comments
        if ';' in line:
            line = line.split(';')[0].strip()

        # Add to history
        add_to_history(line)

        # Send to printer if connected
        if state['printer_connected']:
            success = send_gcode_to_printer(line)
            if not success:
                errors.append(f"Line {line_num}: Failed to send")

        sent_count += 1
        time.sleep(0.05)  # Small delay between commands

    return jsonify({
        'success': len(errors) == 0,
        'sent_count': sent_count,
        'errors': errors
    })


@app.route('/api/gcode/send-all-3mf', methods=['POST'])
def send_all_gcode_3mf():
    """Send G-code by converting to 3MF and uploading to printer."""
    data = request.json
    gcode_text = data.get('gcode', '')
    filename = data.get('filename', 'plot.gcode')

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to send'}), 400

    if not state['printer_connected']:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 400

    # Create temporary directory for files
    temp_dir = tempfile.mkdtemp()
    temp_gcode_path = None
    temp_3mf_path = None

    try:
        # Save G-code to temporary file
        temp_gcode_path = os.path.join(temp_dir, 'temp_plot.gcode')
        with open(temp_gcode_path, 'w') as f:
            f.write(gcode_text)

        # Convert to 3MF using template
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     'test_files', 'template.3mf')

        if not os.path.exists(template_path):
            return jsonify({'success': False, 'error': f'Template file not found: {template_path}'}), 500

        # Generate output 3MF file
        output_3mf_name = filename.replace('.gcode', '.3mf') if filename.endswith('.gcode') else f"{filename}.3mf"
        temp_3mf_path = os.path.join(temp_dir, output_3mf_name)

        # Process 3MF
        process_3mf(template_path, temp_3mf_path, temp_gcode_path, verbose=False)

        # Upload to printer
        with open(temp_3mf_path, 'rb') as f:
            result = printer.upload_file(f, output_3mf_name)

        # Check if upload was successful (226 is FTP success code)
        if "226" not in result:
            return jsonify({'success': False, 'error': f'Upload failed: {result}'}), 500

        # Start the print
        printer.start_print(output_3mf_name, 1)

        return jsonify({
            'success': True,
            'message': f'Successfully uploaded and started printing {output_3mf_name}',
            'filename': output_3mf_name
        })

    except Exception as e:
        print(f"Error in send_all_gcode_3mf: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

    finally:
        # Clean up temporary files
        try:
            if temp_gcode_path and os.path.exists(temp_gcode_path):
                os.remove(temp_gcode_path)
            if temp_3mf_path and os.path.exists(temp_3mf_path):
                os.remove(temp_3mf_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
        except Exception as e:
            print(f"Error cleaning up temporary files: {e}")


@app.route('/api/gcode/create-3mf', methods=['POST'])
def create_3mf():
    """Create 3MF file from G-code and return for download."""
    data = request.json
    gcode_text = data.get('gcode', '')
    filename = data.get('filename', 'plot.gcode')

    if not gcode_text.strip():
        return jsonify({'success': False, 'error': 'No G-code to convert'}), 400

    # Create temporary directory for files
    temp_dir = tempfile.mkdtemp()
    temp_gcode_path = None
    temp_3mf_path = None

    try:
        # Save G-code to temporary file
        temp_gcode_path = os.path.join(temp_dir, 'temp_plot.gcode')
        with open(temp_gcode_path, 'w') as f:
            f.write(gcode_text)

        # Convert to 3MF using template
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     'test_files', 'template.3mf')

        if not os.path.exists(template_path):
            return jsonify({'success': False, 'error': f'Template file not found: {template_path}'}), 500

        # Generate output 3MF file
        output_3mf_name = filename.replace('.gcode', '.3mf') if filename.endswith('.gcode') else f"{filename}.3mf"
        temp_3mf_path = os.path.join(temp_dir, output_3mf_name)

        # Process 3MF
        process_3mf(template_path, temp_3mf_path, temp_gcode_path, verbose=False)

        # Return the file for download
        return send_file(
            temp_3mf_path,
            as_attachment=True,
            download_name=output_3mf_name,
            mimetype='application/vnd.ms-package.3dmanufacturing-3dmodel+xml'
        )

    except Exception as e:
        print(f"Error in create_3mf: {e}")
        # Clean up on error
        try:
            if temp_gcode_path and os.path.exists(temp_gcode_path):
                os.remove(temp_gcode_path)
            if temp_3mf_path and os.path.exists(temp_3mf_path):
                os.remove(temp_3mf_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
        except Exception as cleanup_error:
            print(f"Error cleaning up temporary files: {cleanup_error}")

        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/convert-to-gcode', methods=['POST'])
def convert_to_gcode():
    """Convert SVG or DXF file to G-code."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    file_type = request.form.get('file_type', '')

    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename'}), 400

    if file_type not in ['svg', 'dxf']:
        return jsonify({'success': False, 'error': 'Invalid file type. Only SVG and DXF are supported'}), 400

    # Create temporary directory for processing
    temp_dir = tempfile.mkdtemp()
    temp_input_path = None
    temp_svg_path = None
    temp_gcode_path = None

    try:
        # Save uploaded file
        filename = secure_filename(file.filename)
        temp_input_path = os.path.join(temp_dir, filename)
        file.save(temp_input_path)

        # Convert DXF to SVG if needed
        if file_type == 'dxf':
            temp_svg_path = os.path.join(temp_dir, filename.replace('.dxf', '.svg'))
            convert_dxf_to_svg(temp_input_path, temp_svg_path)
            svg_file_path = temp_svg_path
        else:
            svg_file_path = temp_input_path

        # Convert SVG to G-code
        params = CuttingParameters(
            material_thickness=0.0,  # For plotting, no Z depth
            cutting_speed=1000.0,
            movement_speed=3000.0,
            join_paths=True,
            knife_offset=0.0,  # No offset for pen plotting
            origin_top_left=True
        )

        gcode_tools = GCodeTools(params)
        temp_gcode_path = os.path.join(temp_dir, 'output.gcode')
        gcode = gcode_tools.svg_to_gcode(svg_file_path, temp_gcode_path)

        # Read the generated G-code
        with open(temp_gcode_path, 'r') as f:
            gcode_content = f.read()

        # Count lines
        line_count = len([line for line in gcode_content.split('\n') if line.strip() and not line.strip().startswith(';')])

        return jsonify({
            'success': True,
            'gcode': gcode_content,
            'line_count': line_count,
            'original_filename': filename
        })

    except Exception as e:
        print(f"Error in convert_to_gcode: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

    finally:
        # Clean up temporary files
        try:
            if temp_input_path and os.path.exists(temp_input_path):
                os.remove(temp_input_path)
            if temp_svg_path and os.path.exists(temp_svg_path):
                os.remove(temp_svg_path)
            if temp_gcode_path and os.path.exists(temp_gcode_path):
                os.remove(temp_gcode_path)
            if os.path.exists(temp_dir):
                # Remove any remaining files in temp dir
                for f in os.listdir(temp_dir):
                    try:
                        os.remove(os.path.join(temp_dir, f))
                    except:
                        pass
                os.rmdir(temp_dir)
        except Exception as e:
            print(f"Error cleaning up temporary files: {e}")


@app.route('/api/camera/start', methods=['POST'])
def start_camera():
    """Start camera streaming."""
    if not state['printer_connected']:
        return jsonify({'success': False, 'error': 'Printer not connected'}), 400

    success = start_camera_stream()

    return jsonify({
        'success': success,
        'streaming': state['camera_streaming'],
        'message': 'Camera started' if success else 'Failed to start camera'
    })


@app.route('/api/camera/stop', methods=['POST'])
def stop_camera():
    """Stop camera streaming."""
    stop_camera_stream()

    return jsonify({
        'success': True,
        'streaming': state['camera_streaming'],
        'message': 'Camera stopped'
    })


@app.route('/api/camera/status', methods=['GET'])
def camera_status():
    """Get camera status."""
    camera_alive = False
    if printer and state['printer_connected']:
        try:
            camera_alive = printer.camera_client_alive()
        except Exception as e:
            print(f"Error checking camera status: {e}")

    return jsonify({
        'streaming': state['camera_streaming'],
        'camera_alive': camera_alive,
        'connected': state['printer_connected']
    })


# WebSocket handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    print('Client connected')
    emit('connection_response', {'status': 'connected'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('Client disconnected')


@socketio.on('request_camera_frame')
def handle_frame_request():
    """Handle request for single camera frame."""
    if printer and state['printer_connected']:
        try:
            frame = printer.get_camera_frame()
            if frame:
                processed_frame = process_camera_frame(frame)
                emit('camera_frame', {'frame': processed_frame})
        except Exception as e:
            print(f"Error getting camera frame: {e}")


def start_server(host='0.0.0.0', port=5425, debug=False):
    """Start the Bambu Cuts web server."""
    print("Starting Bambu Cuts - Cutter and Plotter API...")
    print(f"Printer IP: {config._config_data.get('ip', 'Not configured')}")
    print(f"Server will run at: http://{host}:{port}")
    print()

    # Auto-connect on startup
    print("Attempting to connect to printer on startup...")
    connect_printer()

    try:
        socketio.run(app, host=host, port=port, debug=debug, use_reloader=False, allow_unsafe_werkzeug=True)
    finally:
        # Clean up
        stop_camera_stream()
        disconnect_printer()


if __name__ == '__main__':
    start_server(debug=True)
