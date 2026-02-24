const socket = io();
const deviceGrid = document.getElementById('device-grid');
const deviceCountEl = document.getElementById('device-count');
let devices = [];

// Initialize Lucide icons
lucide.createIcons();

// Socket Events
socket.on('connect', () => {
    showToast('Connected to server');
    fetchDevices();
});

socket.on('device_event', ({ event, device }) => {
    console.log('Device event:', event, device);
    fetchDevices(); // Refresh list on change
});

// fetch devices
async function fetchDevices() {
    try {
        const response = await fetch('/api/devices');
        devices = await response.json();
        renderDevices();
    } catch (err) {
        console.error('Error fetching devices:', err);
    }
}

function renderDevices() {
    deviceCountEl.textContent = devices.length;
    
    if (devices.length === 0) {
        deviceGrid.innerHTML = `
            <div class="empty-state">
                <i data-lucide="search"></i>
                <p>Searching for devices via ADB...</p>
            </div>
        `;
        lucide.createIcons();
        return;
    }

    deviceGrid.innerHTML = devices.map(device => `
        <div class="device-card" id="device-${device.id}">
            <div class="screenshot">
                <img src="/api/screenshot/${device.id}?t=${Date.now()}" id="img-${device.id}" alt="Screen">
            </div>
            <div class="info">
                <h3>${device.id}</h3>
                <p>Status: ${device.type}</p>
            </div>
            <div class="card-actions">
                <button onclick="runCommand('${device.id}', 'input keyevent 3')" class="btn btn-primary btn-sm">
                    <i data-lucide="home"></i> Home
                </button>
                <button onclick="runCommand('${device.id}', 'input keyevent 4')" class="btn btn-secondary btn-sm">
                    <i data-lucide="arrow-left"></i> Back
                </button>
                <button onclick="refreshScreenshot('${device.id}')" class="btn btn-accent btn-sm">
                    <i data-lucide="refresh-cw"></i> Refresh
                </button>
                <button onclick="runCommand('${device.id}', 'input keyevent 26')" class="btn btn-warning btn-sm">
                    <i data-lucide="power"></i> Power
                </button>
            </div>
        </div>
    `).join('');
    
    lucide.createIcons();
}

async function runCommand(serial, command) {
    try {
        const response = await fetch('/api/shell', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serial, command })
        });
        const result = await response.json();
        showToast(`Command sent to ${serial}`);
        // Auto refresh screenshot after command
        setTimeout(() => refreshScreenshot(serial), 500);
    } catch (err) {
        showToast(`Error: ${err.message}`);
    }
}

function refreshScreenshot(serial) {
    const img = document.getElementById(`img-${serial}`);
    if (img) {
        img.src = `/api/screenshot/${serial}?t=${Date.now()}`;
    }
}

// Bulk Actions
document.getElementById('btn-run-bulk').addEventListener('click', () => {
    const command = document.getElementById('bulk-command').value;
    if (!command) return;
    devices.forEach(d => runCommand(d.id, command));
    document.getElementById('bulk-command').value = '';
});

document.getElementById('btn-home').addEventListener('click', () => {
    devices.forEach(d => runCommand(d.id, 'input keyevent 3'));
});

document.getElementById('btn-unlock').addEventListener('click', () => {
    devices.forEach(d => {
        runCommand(d.id, 'input keyevent 82'); // Menu/Unlock
        runCommand(d.id, 'input keyevent 66'); // Enter
    });
});

document.getElementById('btn-reboot').addEventListener('click', () => {
    if (confirm('Reboot status for ALL devices?')) {
        devices.forEach(d => runCommand(d.id, 'reboot'));
    }
});

function showToast(message) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
