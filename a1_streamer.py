#!/usr/bin/env python3
"""
Bambu Lab A1 Camera Streamer with WebSocket Server

Clean WebSocket-based streaming server that:
- Streams camera frames via WebSockets for better performance
- Serves a modern web interface for viewing the stream
- Maintains the original TLS connection to the printer

Usage: python a1_streamer.py --address <IP> --access-code <CODE> [--port 8080]
"""

import argparse
import asyncio
import base64
import json
import os
import signal
import ssl
import struct
import sys
import websockets
from typing import Set, Optional
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

JPEG_START = bytes([0xFF, 0xD8, 0xFF, 0xE0])
JPEG_END   = bytes([0xFF, 0xD9])
READ_CHUNK = 4096
PORT       = 6000

# Global WebSocket clients
clients: Set[websockets.WebSocketServerProtocol] = set()

# HTML page for viewing the stream
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bambu A1 Camera Stream</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            text-align: center;
            padding: 20px;
            background: rgba(0,0,0,0.3);
            backdrop-filter: blur(10px);
        }
        h1 {
            color: #00ff88;
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 0 0 20px rgba(0,255,136,0.5);
        }
        .status {
            padding: 10px 20px;
            border-radius: 25px;
            display: inline-block;
            font-weight: 500;
            transition: all 0.3s ease;
        }
        .status.connected { background: rgba(0,255,136,0.2); color: #00ff88; }
        .status.connecting { background: rgba(255,193,7,0.2); color: #ffc107; }
        .status.error { background: rgba(255,68,68,0.2); color: #ff4444; }
        
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 20px;
        }
        .stream-container {
            background: rgba(0,0,0,0.4);
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            max-width: 100%;
            width: 100%;
            max-width: 1200px;
        }
        #stream {
            width: 100%;
            height: auto;
            border-radius: 15px;
            box-shadow: 0 4px 20px rgba(0,255,136,0.3);
            transition: all 0.3s ease;
        }
        #stream:hover {
            transform: scale(1.02);
            box-shadow: 0 6px 30px rgba(0,255,136,0.5);
        }
        .controls {
            margin-top: 30px;
            display: flex;
            gap: 15px;
            justify-content: center;
            flex-wrap: wrap;
        }
        button {
            background: linear-gradient(45deg, #00ff88, #00cc6a);
            color: #1a1a1a;
            border: none;
            padding: 12px 24px;
            border-radius: 25px;
            cursor: pointer;
            font-weight: 600;
            font-size: 16px;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(0,255,136,0.3);
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0,255,136,0.4);
        }
        button:active {
            transform: translateY(0);
        }
        button:disabled {
            background: #666;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }
        .error-message {
            background: rgba(255,68,68,0.1);
            border: 1px solid rgba(255,68,68,0.3);
            color: #ff4444;
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
            text-align: center;
            display: none;
        }
        .loading {
            text-align: center;
            padding: 40px;
        }
        .spinner {
            border: 3px solid rgba(0,255,136,0.3);
            border-top: 3px solid #00ff88;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🖨️ Bambu A1 Camera Stream</h1>
        <div class="status connecting" id="status">Connecting...</div>
    </div>
    
    <div class="main">
        <div class="stream-container">
            <div class="loading" id="loading">
                <div class="spinner"></div>
                <div>Connecting to camera stream...</div>
            </div>
            <img id="stream" style="display: none;" alt="Camera Stream">
            <div class="error-message" id="error"></div>
        </div>
        
        <div class="controls">
            <button id="reconnect" onclick="reconnect()">Reconnect</button>
            <button id="fullscreen" onclick="toggleFullscreen()">Fullscreen</button>
        </div>
    </div>

    <script>
        let ws = null;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 5;
        let reconnectTimeout = null;

        function updateStatus(message, type = 'connecting') {
            const status = document.getElementById('status');
            const loading = document.getElementById('loading');
            const stream = document.getElementById('stream');
            const error = document.getElementById('error');
            
            status.textContent = message;
            status.className = `status ${type}`;
            
            if (type === 'connected') {
                loading.style.display = 'none';
                stream.style.display = 'block';
                error.style.display = 'none';
            } else if (type === 'error') {
                loading.style.display = 'none';
                stream.style.display = 'none';
                error.textContent = message;
                error.style.display = 'block';
            } else {
                loading.style.display = 'block';
                stream.style.display = 'none';
                error.style.display = 'none';
            }
        }

        function connect() {
            if (ws) {
                ws.close();
                ws = null;
            }

            if (reconnectTimeout) {
                clearTimeout(reconnectTimeout);
                reconnectTimeout = null;
            }

            updateStatus('Connecting to stream...', 'connecting');
            
            try {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const port = window.location.port ? parseInt(window.location.port) + 1 : 8081;
                const wsUrl = `${protocol}//${window.location.hostname}:${port}/ws`;
                ws = new WebSocket(wsUrl);
                
                ws.onopen = function() {
                    updateStatus('Connected - Streaming live video', 'connected');
                    reconnectAttempts = 0;
                };
                
                ws.onmessage = function(event) {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'frame' && data.data) {
                            const img = document.getElementById('stream');
                            img.src = `data:image/jpeg;base64,${data.data}`;
                        }
                    } catch (e) {
                        console.error('Error parsing WebSocket message:', e);
                    }
                };
                
                ws.onclose = function(event) {
                    ws = null;
                    if (!event.wasClean) {
                        handleReconnect();
                    }
                };
                
                ws.onerror = function(error) {
                    console.error('WebSocket error:', error);
                    updateStatus('Connection error', 'error');
                };
                
            } catch (error) {
                console.error('Failed to create WebSocket:', error);
                updateStatus('Failed to connect', 'error');
            }
        }

        function handleReconnect() {
            if (reconnectAttempts < maxReconnectAttempts) {
                reconnectAttempts++;
                updateStatus(`Connection lost. Reconnecting... (${reconnectAttempts}/${maxReconnectAttempts})`, 'error');
                reconnectTimeout = setTimeout(connect, 2000 * reconnectAttempts);
            } else {
                updateStatus('Failed to reconnect. Please refresh the page.', 'error');
            }
        }

        function reconnect() {
            reconnectAttempts = 0;
            connect();
        }

        function toggleFullscreen() {
            const img = document.getElementById('stream');
            if (!document.fullscreenElement) {
                if (img.requestFullscreen) {
                    img.requestFullscreen();
                } else if (img.webkitRequestFullscreen) {
                    img.webkitRequestFullscreen();
                } else if (img.msRequestFullscreen) {
                    img.msRequestFullscreen();
                }
            } else {
                document.exitFullscreen();
            }
        }

        // Handle page visibility changes
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                if (ws) {
                    ws.close();
                    ws = null;
                }
                updateStatus('Connection paused (tab hidden)', 'error');
            } else if (!ws) {
                reconnect();
            }
        });

        // Start connection when page loads
        window.onload = connect;
    </script>
</body>
</html>
"""

class HTTPHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for serving the HTML page"""
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'404 Not Found')
    
    def log_message(self, format, *args):
        # Suppress HTTP server logs
        pass

async def handle_websocket(websocket):
    """Handle WebSocket connections only"""
    clients.add(websocket)
    print(f"WebSocket client connected. Total clients: {len(clients)}")
    
    try:
        # Send welcome message
        await websocket.send(json.dumps({"type": "status", "message": "Connected to camera stream"}))
        
        # Keep connection alive
        async for message in websocket:
            # Handle any client messages if needed
            pass
            
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        clients.discard(websocket)
        print(f"WebSocket client disconnected. Total clients: {len(clients)}")

def build_auth_data(access_code: str) -> bytes:
    """
    Mirrors src/auth_data.rs:
      (0x40_u32, 0x3000_u32, 0_u32, 0_u32, padded_username[32], padded_access_code[32])
    with little-endian, fixed-width encoding.
    """
    username = b"bblp"
    padded_user = username.ljust(32, b'\x00')
    ac_bytes = access_code.encode("utf-8")
    if len(ac_bytes) > 32:
        raise ValueError("access code must be <= 32 bytes")
    padded_ac = ac_bytes.ljust(32, b'\x00')

    # 4 * u32 little-endian, then raw 32B username and 32B access code
    return struct.pack("<IIII", 0x40, 0x3000, 0, 0) + padded_user + padded_ac

def make_insecure_ssl_context() -> ssl.SSLContext:
    """
    Mirrors tokio-native-tls with .danger_accept_invalid_certs(true)
    and .danger_accept_invalid_hostnames(true).
    """
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

async def open_tls_connection(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """
    Mirrors src/printer_tls_client.rs: connect TCP then wrap with TLS.
    """
    ssl_ctx = make_insecure_ssl_context()
    return await asyncio.open_connection(host=host, port=port, ssl=ssl_ctx, server_hostname=host)

async def write_frame(data: bytes, output_path: str = None) -> None:
    """Write frame to file and broadcast to WebSocket clients"""
    # Convert frame to base64 for WebSocket transmission
    frame_b64 = base64.b64encode(data).decode('utf-8')
    
    # Broadcast to all connected WebSocket clients
    if clients:
        message = json.dumps({"type": "frame", "data": frame_b64})
        disconnected_clients = set()
        
        for client in clients:
            try:
                await client.send(message)
            except (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException):
                disconnected_clients.add(client)
        
        # Clean up disconnected clients
        clients.difference_update(disconnected_clients)
    
    # Save to file if specified
    if output_path:
        with open(output_path, "wb") as f:
            f.write(data)

async def stream(address: str, access_code: str, output_path: Optional[str]) -> None:
    reader, writer = await open_tls_connection(address, PORT)

    # Clean shutdown on Ctrl+C like the Rust task that calls .shutdown()
    stop = asyncio.Event()
    def _handle_sigint():
        # print once; next SIGINT will just let the loop exit promptly
        if not stop.is_set():
            print("Exiting...", file=sys.stderr)
        stop.set()
        try:
            writer.close()
        except Exception:
            pass

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _handle_sigint)
    except NotImplementedError:
        # Windows without Proactor sometimes doesn't support add_signal_handler for SIGINT
        signal.signal(signal.SIGINT, lambda *_: _handle_sigint())

    # Send auth payload (same as Rust's get_auth_data + write_all)
    auth = build_auth_data(access_code)
    writer.write(auth)
    await writer.drain()

    image: Optional[bytearray] = None
    payload_size: int = 0

    try:
        while not stop.is_set():
            buf = await reader.read(READ_CHUNK)

            if image is not None and buf:
                image.extend(buf)
                if len(image) > payload_size:
                    print(f"ERROR: Received more data than expected: {len(image)} > {payload_size}", file=sys.stderr)
                    image = None
                    continue
                if len(image) == payload_size:
                    # Validate JPEG markers
                    if not (len(image) >= 4 and image[:4] == JPEG_START):
                        print("ERROR: Invalid JPEG start marker", file=sys.stderr)
                    elif not (len(image) >= 2 and image[-2:] == JPEG_END):
                        print("ERROR: Invalid JPEG end marker", file=sys.stderr)
                    else:
                        await write_frame(bytes(image), output_path)
                    image = None

            elif buf and len(buf) == 16:
                # Like Rust: read 16-byte header; first 4 bytes encode payload size (LE u32)
                # Rust code expanded to 8 bytes and used bincode to decode a usize,
                # but effectively it's a little-endian 32-bit length.
                payload_size = struct.unpack_from("<I", buf, 0)[0]
                image = bytearray()
            elif not buf:
                # read() returned 0 bytes — mirror Rust's "Connection rejected..."
                print("Connection rejected by the server.\nCheck the IP address and access code.", file=sys.stderr)
                break
            # else: any other header sizes are ignored (behaves like the Rust loop)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream JPEG frames from a Bambu Lab printer via HTTP server with SSE."
    )
    parser.add_argument("-o", "--output", help="Output file (overwritten each frame). Optional.", default=None)
    parser.add_argument("-a", "--address", required=True, help="Printer IP / hostname")
    parser.add_argument("-c", "--access-code", required=True, help="Printer access code (<= 32 bytes)")
    parser.add_argument("-p", "--port", type=int, default=8080, help="HTTP server port (default: 8080)")
    return parser.parse_args()

def start_http_server(port: int):
    """Start HTTP server in a separate thread"""
    server = HTTPServer(('', port), HTTPHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server

async def start_websocket_server(port: int):
    """Start WebSocket server"""
    print(f"Starting WebSocket server on port {port + 1}")
    return await websockets.serve(handle_websocket, "localhost", port + 1)

async def main_async():
    """Main async function"""
    args = parse_args()
    
    
    print(f"Starting Bambu A1 Camera Streamer")
    print(f"Printer: {args.address}")
    print(f"HTTP Server: http://localhost:{args.port}")
    print(f"WebSocket Server: ws://localhost:{args.port + 1}")
    print(f"Web Interface: http://localhost:{args.port}")
    print("-" * 50)
    
    # Start HTTP server
    http_server = start_http_server(args.port)
    
    # Start WebSocket server
    ws_server = await start_websocket_server(args.port)
    
    try:
        # Start camera stream in background
        stream_task = asyncio.create_task(stream(args.address, args.access_code, args.output))
        
        # Wait for either stream to complete or interrupt
        await stream_task
        
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        http_server.shutdown()
        ws_server.close()
        await ws_server.wait_closed()
        print("Server stopped.")

def main() -> None:
    """Main entry point"""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nShutdown complete.")

if __name__ == "__main__":
    main()