const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const path = require('path');
const adbManager = require('./adb-manager');

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static('public'));
app.use(express.json());

// API Endpoints
app.get('/api/devices', async (req, res) => {
    const devices = await adbManager.listDevices();
    res.json(devices);
});

app.post('/api/shell', async (req, res) => {
    const { serial, command } = req.body;
    try {
        const output = await adbManager.shell(serial, command);
        res.json({ output: output.toString() });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

app.get('/api/screenshot/:serial', async (req, res) => {
    try {
        const buffer = await adbManager.takeScreenshot(req.params.serial);
        res.set('Content-Type', 'image/png');
        res.send(buffer);
    } catch (err) {
        res.status(500).send(err.message);
    }
});

// Start tracking globally so automation works without browser open
adbManager.trackDevices((event, device) => {
    io.emit('device_event', { event, device });
});

// Socket.io for real-time updates
io.on('connection', (socket) => {
    console.log('Client connected');
    socket.on('disconnect', () => {
        console.log('Client disconnected');
    });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Server running on http://localhost:${PORT}`);
});
