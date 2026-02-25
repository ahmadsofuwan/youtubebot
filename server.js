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
