const adb = require('adbkit');
const client = adb.createClient();

client.listDevices()
    .then(devices => {
        console.log('Devices found:', devices);
        process.exit(0);
    })
    .catch(err => {
        console.error('Error connecting to ADB:', err.message);
        process.exit(1);
    });
