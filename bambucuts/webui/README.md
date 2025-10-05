# Web-Based CNC/3D Printer Jogger

A modern web-based interface for controlling CNC machines and 3D printers. This is a web version of `jogger_simple.py` with the same functionality but accessible through a browser.

## Features

- **Real-time Position Tracking**: Monitor X, Y, Z, and E axis positions
- **Interactive Controls**:
  - Visual D-pad for XY movement
  - Z axis control buttons
  - E axis (extruder) control buttons
  - Keyboard shortcuts (Arrow keys, U/J, E/D)
- **Variable Step Sizes**: 0.1mm (fine), 1.0mm (normal), 10.0mm (coarse)
- **Special Functions**:
  - Home XY axes (G28)
  - Save Z zero position (G92 Z0)
  - Reset E to zero (G92 E0)
  - Move Z to absolute positions (0mm, 10mm)
- **Manual G-code Input**: Send custom G-code commands
- **Command History**: View the last 20 G-code commands sent
- **Auto-connect**: Automatically connects to printer on startup
- **Modern UI**: Responsive design with dark theme

## Architecture

### Backend (Flask API)
- `app.py` - Flask server providing RESTful API
- Connects to BambuLab printer via `bambulabs_api`
- Manages position tracking and G-code generation
- Handles printer communication and error handling

### Frontend (HTML/CSS/JavaScript)
- `templates/index.html` - Main UI structure
- `static/style.css` - Modern styling with dark theme
- `static/app.js` - Client-side logic and API communication

## Installation

1. Install Python dependencies:
```bash
pip install flask flask-cors
```

2. Make sure `bambulabs_api` is installed (already in parent directory):
```bash
pip install bambulabs_api
```

3. Configure your printer settings in `../config.py`:
```python
PRINTER_IP = "192.168.1.150"
SERIAL = "your_serial_number"
ACCESS_CODE = "your_access_code"
```

## Usage

1. Start the Flask server:
```bash
python app.py
```

2. Open your browser and navigate to:
```
http://localhost:5425
```

3. The app will automatically attempt to connect to the printer on startup.

4. Use the controls:
   - **Mouse**: Click buttons and D-pad
   - **Keyboard**: Arrow keys (X/Y), U/J (Z), E/D (E axis)
   - **Step Size**: Change precision using the dropdown
   - **G-code Input**: Type custom commands and press Enter or click Send

## API Endpoints

- `GET /api/status` - Get current position and connection status
- `GET /api/history` - Get G-code command history
- `POST /api/connect` - Toggle printer connection
- `POST /api/move` - Move axis by distance
- `POST /api/home` - Home XY axes
- `POST /api/save-z-zero` - Save current Z as zero
- `POST /api/reset-e-zero` - Reset E to zero
- `POST /api/move-z-absolute` - Move Z to absolute position
- `POST /api/gcode` - Execute custom G-code
- `POST /api/step-size` - Set step size

## Differences from jogger_simple.py

### Improvements
- **Web-based**: Accessible from any device on the network
- **Modern UI**: Better visual feedback and responsive design
- **No Terminal Required**: Works in any browser
- **Mobile-friendly**: Responsive design works on tablets/phones

### Maintained Features
- All original functionality preserved
- Same G-code generation logic
- Same printer communication flow
- Same position tracking
- Same special functions

## Configuration

The server runs on `0.0.0.0:5425` by default, making it accessible from other devices on your network. To change:

```python
app.run(host='localhost', port=8080)  # Local only, different port
```

## Security Note

This app is designed for local network use. If exposing to the internet, add authentication and use HTTPS.

## Troubleshooting

### Printer won't connect
- Check `config.py` has correct IP, serial, and access code
- Verify printer is on the same network
- Check printer is powered on and network-enabled

### Commands not executing
- Verify connection status shows "CONNECTED"
- Check G-code history for errors
- Look at server console for error messages

### Browser issues
- Try Chrome/Firefox/Safari (latest versions)
- Clear browser cache
- Check browser console for errors (F12)

## Development

To run in debug mode:
```bash
python app.py
```

The server will auto-reload on code changes.

## License

Same as parent project.
