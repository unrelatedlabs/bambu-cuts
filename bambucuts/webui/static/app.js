// Web Jogger JavaScript - API Client

const API_BASE = window.location.origin;

// State
let state = {
    position: { x: 0, y: 0, z: 0, e: 0 },
    stepSize: 1.0,
    printerConnected: false,
    printerIp: '',
    updateInterval: null,
    currentFileName: 'Untitled.gcode',
    lastKissTime: 0,
    lastActionWasKiss: false
};

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('Initializing Web Jogger...');

    // Attach event listeners
    attachEventListeners();

    // Start status polling
    startStatusPolling();

    // Initial status fetch
    updateStatus();
    updateHistory();
    loadConfig();

    // Setup editor drag and drop
    setupEditorDragDrop();

    // Setup converter drag and drop
    setupConverterDragDrop();

    // Initialize keyboard highlighting
    updateKeyboardHighlights(false);
});

// Attach all event listeners
function attachEventListeners() {
    // Connection button
    document.getElementById('connectBtn').addEventListener('click', toggleConnection);

    // D-pad buttons (old style)
    document.querySelectorAll('.dpad-btn').forEach(btn => {
        btn.addEventListener('click', handleDpadClick);
    });

    // Ring D-pad buttons (new style)
    document.querySelectorAll('.dpad-ring-btn').forEach(btn => {
        btn.addEventListener('click', handleRingDpadClick);
    });

    // Axis buttons (Z and E)
    document.querySelectorAll('.axis-btn-compact').forEach(btn => {
        btn.addEventListener('click', handleAxisClick);
    });

    // Ring-style axis buttons (Z and E with step sizes)
    document.querySelectorAll('.axis-btn-ring').forEach(btn => {
        btn.addEventListener('click', handleAxisRingClick);
    });

    // Unified axis buttons (compact vertical stack)
    document.querySelectorAll('.axis-btn-unified').forEach(btn => {
        btn.addEventListener('click', handleAxisRingClick);
    });

    // Special function buttons
    document.getElementById('homeXY').addEventListener('click', homeXY);
    document.getElementById('kissZ').addEventListener('click', kissZ);
    document.getElementById('microKissZ').addEventListener('click', microKissZ);
    document.getElementById('saveZ').addEventListener('click', saveZZero);
    document.getElementById('resetE').addEventListener('click', resetEZero);
    document.getElementById('moveZ0').addEventListener('click', () => moveZAbsolute(0));
    document.getElementById('moveZ2').addEventListener('click', () => moveZAbsolute(2));
    document.getElementById('moveZ10').addEventListener('click', () => moveZAbsolute(10));
    document.getElementById('moveZ50').addEventListener('click', () => moveZAbsolute(50));

    // G-code input
    document.getElementById('sendGcode').addEventListener('click', sendCustomGcode);
    document.getElementById('gcodeInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendCustomGcode();
        }
    });

    // Laser controls
    document.getElementById('laserOnBtn').addEventListener('click', laserOn);
    document.getElementById('laserOffBtn').addEventListener('click', laserOff);
    document.getElementById('setLaserPowerBtn').addEventListener('click', setLaserPower);
    const laserPowerSlider = document.getElementById('laserPowerSlider');
    const laserPowerValue = document.getElementById('laserPowerValue');
    laserPowerSlider.addEventListener('input', (e) => {
        laserPowerValue.textContent = e.target.value;
    });

    // Step size pill buttons
    document.querySelectorAll('.step-pill').forEach(pill => {
        pill.addEventListener('click', (e) => {
            const newStepSize = parseFloat(e.currentTarget.getAttribute('data-step'));

            // Remove active class from all pills
            document.querySelectorAll('.step-pill').forEach(p => p.classList.remove('active'));

            // Add active class to clicked pill
            e.currentTarget.classList.add('active');

            // Update state and backend
            state.stepSize = newStepSize;
            setStepSize(state.stepSize);
        });
    });

    // Keyboard controls
    document.addEventListener('keydown', handleKeyboard);

    // Configuration
    document.getElementById('saveConfigBtn').addEventListener('click', saveConfig);

    // Editor buttons
    document.getElementById('loadFileBtn').addEventListener('click', loadFile);
    document.getElementById('saveFileBtn').addEventListener('click', saveFile);
    document.getElementById('sendAllBtn').addEventListener('click', sendAllGcode);
    document.getElementById('sendDirectBtn').addEventListener('click', sendAllGcodeDirect);
    document.getElementById('download3mfBtn').addEventListener('click', download3mf);

    // Filename changes
    document.getElementById('fileName').addEventListener('change', updateFileName);

    // Editor line numbers
    const editor = document.getElementById('gcodeEditor');
    editor.addEventListener('input', updateLineNumbers);
    editor.addEventListener('scroll', syncLineNumbersScroll);

    // Initial line numbers
    updateLineNumbers();
}

// Handle keyboard input
function handleKeyboard(e) {
    // Don't trigger if typing in input field or textarea
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        return;
    }

    // Update highlights on Shift key change
    if (e.key === 'Shift') {
        updateKeyboardHighlights(true);
    }

    // Apply 10x finer if Shift is held
    const stepSize = e.shiftKey ? state.stepSize / 10 : state.stepSize;

    switch(e.key) {
        case 'ArrowUp':
            e.preventDefault();
            moveAxis('y', stepSize);
            break;
        case 'ArrowDown':
            e.preventDefault();
            moveAxis('y', -stepSize);
            break;
        case 'ArrowLeft':
            e.preventDefault();
            moveAxis('x', -stepSize);
            break;
        case 'ArrowRight':
            e.preventDefault();
            moveAxis('x', stepSize);
            break;
        case 'u':
        case 'U':
            e.preventDefault();
            state.lastActionWasKiss = false; // Reset kiss flag on manual Z move
            moveAxis('z', stepSize);
            break;
        case 'j':
        case 'J':
            e.preventDefault();
            state.lastActionWasKiss = false; // Reset kiss flag on manual Z move
            moveAxis('z', -stepSize);
            break;
        case 'e':
        case 'E':
            e.preventDefault();
            moveAxis('e', stepSize);
            break;
        case 'd':
        case 'D':
            e.preventDefault();
            moveAxis('e', -stepSize);
            break;
        case 'k':
        case 'K':
            e.preventDefault();
            if (e.shiftKey) {
                microKissZ();
            } else {
                kissZ();
            }
            break;
    }
}

// Handle key up to update highlights when Shift is released
document.addEventListener('keyup', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        return;
    }

    if (e.key === 'Shift') {
        updateKeyboardHighlights(false);
    }
});

// Update keyboard highlights based on current step size and Shift state
function updateKeyboardHighlights(shiftPressed) {
    const effectiveStepSize = shiftPressed ? state.stepSize / 10 : state.stepSize;

    // Remove all highlights
    document.querySelectorAll('.dpad-ring-btn').forEach(btn => btn.classList.remove('keyboard-active'));
    document.querySelectorAll('.axis-btn-unified').forEach(btn => btn.classList.remove('keyboard-active'));

    // Highlight XY buttons matching the effective step size
    document.querySelectorAll('.dpad-ring-btn').forEach(btn => {
        const step = parseFloat(btn.getAttribute('data-step'));
        if (Math.abs(step - effectiveStepSize) < 0.001) {
            btn.classList.add('keyboard-active');
        }
    });

    // Highlight Z buttons matching the effective step size
    document.querySelectorAll('.axis-btn-unified[data-axis="z"]').forEach(btn => {
        const step = parseFloat(btn.getAttribute('data-step'));
        if (Math.abs(step - effectiveStepSize) < 0.001) {
            btn.classList.add('keyboard-active');
        }
    });

    // Highlight E buttons matching the effective step size
    document.querySelectorAll('.axis-btn-unified[data-axis="e"]').forEach(btn => {
        const step = parseFloat(btn.getAttribute('data-step'));
        if (Math.abs(step - effectiveStepSize) < 0.001) {
            btn.classList.add('keyboard-active');
        }
    });
}

// Handle D-pad button clicks
function handleDpadClick(e) {
    const btn = e.currentTarget;
    const axis = btn.getAttribute('data-axis');
    const dir = parseFloat(btn.getAttribute('data-dir'));
    const distance = state.stepSize * dir;

    // Reset kiss flag if Z is moved manually
    if (axis === 'z') {
        state.lastActionWasKiss = false;
    }

    moveAxis(axis, distance);
}

// Handle ring D-pad button clicks
function handleRingDpadClick(e) {
    const btn = e.currentTarget;
    const axis = btn.getAttribute('data-axis');
    const dir = parseFloat(btn.getAttribute('data-dir'));
    const step = parseFloat(btn.getAttribute('data-step'));
    const distance = step * dir;

    // Reset kiss flag if Z is moved manually
    if (axis === 'z') {
        state.lastActionWasKiss = false;
    }

    moveAxis(axis, distance);
}

// Handle axis button clicks
function handleAxisClick(e) {
    const btn = e.currentTarget;
    const axis = btn.getAttribute('data-axis');
    const dir = parseFloat(btn.getAttribute('data-dir'));
    const distance = state.stepSize * dir;

    // Reset kiss flag if Z is moved manually
    if (axis === 'z') {
        state.lastActionWasKiss = false;
    }

    moveAxis(axis, distance);
}

// Handle ring-style axis button clicks (with step attribute)
function handleAxisRingClick(e) {
    const btn = e.currentTarget;
    const axis = btn.getAttribute('data-axis');
    const dir = parseFloat(btn.getAttribute('data-dir'));
    const step = parseFloat(btn.getAttribute('data-step'));
    const distance = step * dir;

    // Reset kiss flag if Z is moved manually
    if (axis === 'z') {
        state.lastActionWasKiss = false;
    }

    moveAxis(axis, distance);
}

// API Calls

async function toggleConnection() {
    try {
        const response = await fetch(`${API_BASE}/api/connect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        console.log('Connection toggle:', data);

        updateStatus();

        if (data.success && data.connected) {
            showNotification('Connected to printer', 'success');
        } else if (data.success && !data.connected) {
            showNotification('Disconnected from printer', 'info');
        } else {
            showNotification(`Connection failed: ${data.message}`, 'error');
        }
    } catch (error) {
        console.error('Connection error:', error);
        showNotification('Failed to toggle connection', 'error');
    }
}

async function moveAxis(axis, distance) {
    try {
        const response = await fetch(`${API_BASE}/api/move`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ axis, distance })
        });

        const data = await response.json();
        console.log('Move result:', data);

        if (data.success) {
            updatePositionDisplay(data.position);
            updateHistory();
        } else {
            showNotification('Movement failed', 'error');
        }
    } catch (error) {
        console.error('Move error:', error);
        showNotification('Failed to move axis', 'error');
    }
}

async function homeXY() {
    try {
        const response = await fetch(`${API_BASE}/api/home`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        console.log('Home result:', data);

        if (data.success) {
            updatePositionDisplay(data.position);
            updateHistory();
            showNotification('Homing XY axes', 'success');
        } else {
            showNotification('Homing failed', 'error');
        }
    } catch (error) {
        console.error('Home error:', error);
        showNotification('Failed to home axes', 'error');
    }
}

async function kissZ() {
    if (!state.printerConnected) {
        showNotification('Printer not connected', 'warning');
        return;
    }

    try {
        showNotification('Running Kiss Z sequence...', 'info');

        // Send kiss sequence: Z down 0.1mm, draw 5mm diameter circle, set Z=0
        const gcodeSequence = `G91
G1 Z-0.1 F500
G2 I0 J-2.5 F500
G92 Z0`;

        const response = await fetch(`${API_BASE}/api/gcode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: gcodeSequence })
        });

        const data = await response.json();

        if (data.success) {
            showNotification('Kiss Z sequence complete!', 'success');
            updateHistory();
            state.lastActionWasKiss = true;
        } else {
            showNotification('Kiss Z sequence failed', 'error');
        }
    } catch (error) {
        console.error('Kiss Z error:', error);
        showNotification('Kiss Z sequence failed', 'error');
    }
}

async function microKissZ() {
    if (!state.printerConnected) {
        showNotification('Printer not connected', 'warning');
        return;
    }

    try {
        showNotification('Running Micro Kiss Z sequence...', 'info');

        // Send micro kiss sequence: Z down 0.02mm, draw 5mm diameter circle, set Z=0
        const gcodeSequence = `G91
G1 Z-0.02 F100
G2 I0 J-2.5 F500
G92 Z0`;

        const response = await fetch(`${API_BASE}/api/gcode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: gcodeSequence })
        });

        const data = await response.json();

        if (data.success) {
            showNotification('Micro Kiss Z sequence complete!', 'success');
            updateHistory();
            state.lastActionWasKiss = true;
        } else {
            showNotification('Micro Kiss Z sequence failed', 'error');
        }
    } catch (error) {
        console.error('Micro Kiss Z error:', error);
        showNotification('Micro Kiss Z sequence failed', 'error');
    }
}

async function saveZZero() {
    try {
        const response = await fetch(`${API_BASE}/api/save-z-zero`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        console.log('Save Z zero result:', data);

        if (data.success) {
            updatePositionDisplay(data.position);
            updateHistory();
            showNotification('Z zero saved', 'success');
        } else {
            showNotification('Save Z zero failed', 'error');
        }
    } catch (error) {
        console.error('Save Z zero error:', error);
        showNotification('Failed to save Z zero', 'error');
    }
}

async function resetEZero() {
    try {
        const response = await fetch(`${API_BASE}/api/reset-e-zero`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        console.log('Reset E zero result:', data);

        if (data.success) {
            updatePositionDisplay(data.position);
            updateHistory();
            showNotification('E zero reset', 'success');
        } else {
            showNotification('Reset E zero failed', 'error');
        }
    } catch (error) {
        console.error('Reset E zero error:', error);
        showNotification('Failed to reset E zero', 'error');
    }
}

async function moveZAbsolute(position) {
    try {
        const response = await fetch(`${API_BASE}/api/move-z-absolute`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ position })
        });

        const data = await response.json();
        console.log('Move Z absolute result:', data);

        if (data.success) {
            updatePositionDisplay(data.position);
            updateHistory();
            showNotification(`Moving Z to ${position}mm`, 'success');
        } else {
            showNotification('Z movement failed', 'error');
        }
    } catch (error) {
        console.error('Move Z absolute error:', error);
        showNotification('Failed to move Z', 'error');
    }
}

async function sendCustomGcode() {
    const input = document.getElementById('gcodeInput');
    const gcode = input.value.trim();

    if (!gcode) {
        showNotification('Please enter a G-code command', 'warning');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/gcode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode })
        });

        const data = await response.json();
        console.log('G-code result:', data);

        if (data.success) {
            updatePositionDisplay(data.position);
            updateHistory();
            showNotification('G-code sent', 'success');
        } else {
            showNotification(`G-code failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('G-code error:', error);
        showNotification('Failed to send G-code', 'error');
    }
}

async function laserOn() {
    if (!state.printerConnected) {
        showNotification('Printer not connected', 'warning');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/gcode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: 'M620 P1 M621 P1 M400' })
        });

        const data = await response.json();

        if (data.success) {
            showNotification('Laser ON', 'success');
            updateHistory();
        } else {
            showNotification('Failed to turn laser on', 'error');
        }
    } catch (error) {
        console.error('Laser ON error:', error);
        showNotification('Failed to turn laser on', 'error');
    }
}

async function laserOff() {
    if (!state.printerConnected) {
        showNotification('Printer not connected', 'warning');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/gcode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: 'M620 P0 M621 P0 M400' })
        });

        const data = await response.json();

        if (data.success) {
            showNotification('Laser OFF', 'success');
            updateHistory();
        } else {
            showNotification('Failed to turn laser off', 'error');
        }
    } catch (error) {
        console.error('Laser OFF error:', error);
        showNotification('Failed to turn laser off', 'error');
    }
}

async function setLaserPower() {
    if (!state.printerConnected) {
        showNotification('Printer not connected', 'warning');
        return;
    }

    const power = parseInt(document.getElementById('laserPowerSlider').value);
    const powerValue = power * 2;

    try {
        const response = await fetch(`${API_BASE}/api/gcode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: `M620 P${powerValue} M621 P${powerValue} M400` })
        });

        const data = await response.json();

        if (data.success) {
            showNotification(`Laser power set to ${power}%`, 'success');
            updateHistory();
        } else {
            showNotification('Failed to set laser power', 'error');
        }
    } catch (error) {
        console.error('Set laser power error:', error);
        showNotification('Failed to set laser power', 'error');
    }
}

async function setStepSize(stepSize) {
    try {
        const response = await fetch(`${API_BASE}/api/step-size`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ step_size: stepSize })
        });

        const data = await response.json();
        console.log('Step size set:', data);
    } catch (error) {
        console.error('Step size error:', error);
    }
}

// Status updates

async function updateStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/status`);
        const data = await response.json();

        console.log('Status update:', data);

        // Update state
        state.position = data.position;
        state.printerConnected = data.printer_connected;
        state.printerIp = data.printer_ip;

        // Update UI
        updatePositionDisplay(data.position);
        updateConnectionStatus(data.printer_connected, data.printer_ip, data.connection_error, data.printer_state);
    } catch (error) {
        console.error('Status update error:', error);
    }
}

async function updateHistory() {
    try {
        const response = await fetch(`${API_BASE}/api/history`);
        const data = await response.json();

        const historyContainer = document.getElementById('gcodeHistory');

        if (data.history.length === 0) {
            historyContainer.innerHTML = '<div class="history-empty">No commands sent yet</div>';
        } else {
            historyContainer.innerHTML = data.history
                .map(cmd => `<div class="history-item">${escapeHtml(cmd)}</div>`)
                .join('');

            // Auto-scroll to bottom
            historyContainer.scrollTop = historyContainer.scrollHeight;
        }
    } catch (error) {
        console.error('History update error:', error);
    }
}

// UI Updates

function updatePositionDisplay(position) {
    // Position display removed from UI - no longer needed for relative positioning
    // Keep function for compatibility but do nothing
}

function updateConnectionStatus(connected, printerIp, error, printerState) {
    const statusBox = document.getElementById('connectionStatus');
    const statusText = document.getElementById('statusText');
    const connectBtn = document.getElementById('connectBtn');

    console.log('Updating connection status:', { connected, printerIp, error, printerState });

    if (connected) {
        statusBox.className = 'status-box connected';
        let statusMessage = `Printer: CONNECTED (${printerIp})`;
        if (printerState) {
            statusMessage += ` - ${printerState}`;
        } else {
            statusMessage += ' - RELATIVE MODE';
        }
        statusText.textContent = statusMessage;
        connectBtn.textContent = 'Disconnect';
        connectBtn.className = 'btn btn-primary';
    } else {
        statusBox.className = 'status-box disconnected';
        if (error) {
            statusText.textContent = `Printer: DISCONNECTED - ${error.substring(0, 50)}...`;
        } else {
            statusText.textContent = 'Printer: DISCONNECTED';
        }
        connectBtn.textContent = 'Connect';
        connectBtn.className = 'btn btn-primary';
    }
}

// Notifications
function showNotification(message, type = 'info') {
    console.log(`[${type.toUpperCase()}] ${message}`);
    // TODO: Add visual notification system (toast/banner)
}

// Polling
function startStatusPolling() {
    // Poll status every 2 seconds
    state.updateInterval = setInterval(() => {
        updateStatus();
    }, 2000);
}

function stopStatusPolling() {
    if (state.updateInterval) {
        clearInterval(state.updateInterval);
        state.updateInterval = null;
    }
}

// Utility functions
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

// Configuration Functions

async function loadConfig() {
    try {
        const response = await fetch(`${API_BASE}/api/config`);
        const data = await response.json();

        document.getElementById('printerIP').value = data.ip;
        document.getElementById('printerSerial').value = data.serial;
        document.getElementById('printerAccessCode').value = data.access_code;
       
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

async function saveConfig() {
    const ip = document.getElementById('printerIP').value;
    const serial = document.getElementById('printerSerial').value;
    const accessCode = document.getElementById('printerAccessCode').value;

    try {
        const response = await fetch(`${API_BASE}/api/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ip: ip,
                serial: serial,
                access_code: accessCode
            })
        });

        const data = await response.json();

        if (data.success) {
            showNotification(data.message, 'success');
        } else {
            showNotification(`Failed to save config: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Save config error:', error);
        showNotification('Failed to save configuration', 'error');
    }
}

// Emergency Stop Functions

async function triggerEstop() {
    if (!confirm('⚠️ EMERGENCY STOP: This will immediately halt all printer movements. Continue?')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/estop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (data.success) {
            showNotification('🛑 EMERGENCY STOP ACTIVATED!', 'error');
        } else {
            showNotification(`E-stop failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('E-stop error:', error);
        showNotification('Failed to trigger emergency stop', 'error');
    }
}



// Editor Functions

function setupEditorDragDrop() {
    const editorWrapper = document.querySelector('.editor-wrapper');

    editorWrapper.addEventListener('dragover', (e) => {
        e.preventDefault();
        editorWrapper.classList.add('drag-over');
    });

    editorWrapper.addEventListener('dragleave', (e) => {
        e.preventDefault();
        editorWrapper.classList.remove('drag-over');
    });

    editorWrapper.addEventListener('drop', (e) => {
        e.preventDefault();
        editorWrapper.classList.remove('drag-over');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const file = files[0];
            if (file.name.endsWith('.gcode') || file.name.endsWith('.nc') || file.name.endsWith('.txt')) {
                readFileContent(file);
            } else {
                showNotification('Please drop a .gcode, .nc, or .txt file', 'warning');
            }
        }
    });
}

function readFileContent(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById('gcodeEditor').value = e.target.result;
        state.currentFileName = file.name;
        document.getElementById('fileName').value = file.name;
        updateLineNumbers();
        showNotification(`Loaded ${file.name}`, 'success');
    };
    reader.onerror = () => {
        showNotification('Failed to read file', 'error');
    };
    reader.readAsText(file);
}

function loadFile() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.gcode,.nc,.txt';
    input.style.display = 'none';

    input.onchange = (e) => {
        const file = e.target.files[0];
        if (file) {
            readFileContent(file);
        }
        // Clean up
        document.body.removeChild(input);
    };

    document.body.appendChild(input);
    input.click();
}

function saveFile() {
    const content = document.getElementById('gcodeEditor').value;
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = state.currentFileName;
    a.click();
    URL.revokeObjectURL(url);
    showNotification(`Saved ${state.currentFileName}`, 'success');
}

async function sendAllGcode() {
    const content = document.getElementById('gcodeEditor').value;
    const filename = document.getElementById('fileName').value;

    if (!content.trim()) {
        showNotification('Editor is empty', 'warning');
        return;
    }

    if (!state.printerConnected) {
        showNotification('Printer not connected', 'warning');
        return;
    }

    if (!confirm('Convert to 3MF and start print?')) {
        return;
    }

    try {
        showNotification('Converting to 3MF...', 'info');

        const response = await fetch(`${API_BASE}/api/gcode/send-all-3mf`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: content, filename: filename })
        });

        const data = await response.json();

        if (data.success) {
            showNotification(`Successfully uploaded and started ${data.filename}`, 'success');
            updateHistory();
        } else {
            showNotification(`Upload failed: ${data.error}`, 'error');
            console.error('Errors:', data.errors);
        }
    } catch (error) {
        console.error('Send all error:', error);
        showNotification('Failed to send G-code', 'error');
    }
}

async function sendAllGcodeDirect() {
    const content = document.getElementById('gcodeEditor').value;

    if (!content.trim()) {
        showNotification('Editor is empty', 'warning');
        return;
    }

    if (!state.printerConnected) {
        showNotification('Printer not connected', 'warning');
        return;
    }

    if (!confirm('Send G-code directly line-by-line to printer?')) {
        return;
    }

    try {
        showNotification('Sending G-code directly...', 'info');

        const response = await fetch(`${API_BASE}/api/gcode/send-all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: content })
        });

        const data = await response.json();

        if (data.success) {
            showNotification(`Successfully sent ${data.sent_count} G-code lines`, 'success');
            updateHistory();
        } else {
            showNotification(`Send failed: ${data.error || 'Unknown error'}`, 'error');
            if (data.errors && data.errors.length > 0) {
                console.error('Errors:', data.errors);
            }
        }
    } catch (error) {
        console.error('Send direct error:', error);
        showNotification('Failed to send G-code', 'error');
    }
}

async function download3mf() {
    const content = document.getElementById('gcodeEditor').value;
    const filename = document.getElementById('fileName').value;

    if (!content.trim()) {
        showNotification('Editor is empty', 'warning');
        return;
    }

    try {
        showNotification('Creating 3MF file...', 'info');

        const response = await fetch(`${API_BASE}/api/gcode/create-3mf`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gcode: content, filename: filename })
        });

        if (response.ok) {
            // Get the blob from the response
            const blob = await response.blob();

            // Create download link
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename.replace('.gcode', '.3mf');
            a.click();
            URL.revokeObjectURL(url);

            showNotification('3MF file downloaded', 'success');
        } else {
            const data = await response.json();
            showNotification(`Download failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Download 3MF error:', error);
        showNotification('Failed to create 3MF file', 'error');
    }
}

function updateFileName() {
    const newName = document.getElementById('fileName').value.trim();
    if (newName) {
        state.currentFileName = newName;
    } else {
        // Reset to previous name if empty
        document.getElementById('fileName').value = state.currentFileName;
    }
}

function updateLineNumbers() {
    const editor = document.getElementById('gcodeEditor');
    const lineNumbers = document.getElementById('lineNumbers');
    const lines = editor.value.split('\n');
    const lineCount = lines.length;

    // Generate line numbers
    let numbers = '';
    for (let i = 1; i <= lineCount; i++) {
        numbers += i + '\n';
    }

    lineNumbers.textContent = numbers;
}

function syncLineNumbersScroll() {
    const editor = document.getElementById('gcodeEditor');
    const lineNumbers = document.getElementById('lineNumbers');
    lineNumbers.scrollTop = editor.scrollTop;
}

// Converter Functions

function setupConverterDragDrop() {
    const dropzone = document.getElementById('converterDropzone');
    const fileInput = document.getElementById('converterFileInput');

    // Click to browse
    dropzone.addEventListener('click', () => {
        fileInput.click();
    });

    // File input change
    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            handleConverterFile(file);
        }
    });

    // Drag over
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('drag-over');
    });

    // Drag leave
    dropzone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');
    });

    // Drop
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const file = files[0];
            if (file.name.endsWith('.svg') || file.name.endsWith('.dxf')) {
                handleConverterFile(file);
            } else {
                showConverterStatus('Please drop a .svg or .dxf file', 'error');
            }
        }
    });
}

async function handleConverterFile(file) {
    const fileName = file.name;
    const fileExtension = fileName.split('.').pop().toLowerCase();

    showConverterStatus(`Processing ${fileName}...`, 'processing');

    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('file_type', fileExtension);

        const response = await fetch(`${API_BASE}/api/convert-to-gcode`, {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            // Load the G-code into the editor
            document.getElementById('gcodeEditor').value = data.gcode;
            updateLineNumbers();

            // Update filename
            const newFileName = fileName.replace(`.${fileExtension}`, '.gcode');
            state.currentFileName = newFileName;
            document.getElementById('fileName').value = newFileName;

            showConverterStatus(`✓ Converted ${fileName} to G-code (${data.line_count} lines)`, 'success');
            showNotification(`Converted ${fileName} successfully`, 'success');
        } else {
            showConverterStatus(`✗ Conversion failed: ${data.error}`, 'error');
            showNotification(`Conversion failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Converter error:', error);
        showConverterStatus(`✗ Conversion failed: ${error.message}`, 'error');
        showNotification('Conversion failed', 'error');
    }
}

function showConverterStatus(message, type) {
    const status = document.getElementById('converterStatus');
    status.textContent = message;
    status.className = 'converter-status ' + type;

    // Auto-hide success/error after 5 seconds
    if (type !== 'processing') {
        setTimeout(() => {
            status.style.display = 'none';
        }, 5000);
    }
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    stopStatusPolling();
});
