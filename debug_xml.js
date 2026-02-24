const adb = require("adbkit");
const client = adb.createClient();

async function debugXml() {
    const serial = '127.0.0.1:21503';
    console.log(`Dumping XML for ${serial}...`);
    try {
        console.log("Trying compressed dump...");
        const stream = await client.shell(serial, 'uiautomator dump --compressed /sdcard/debug_ads_comp.xml && cat /sdcard/debug_ads_comp.xml');
        const content = await adb.util.readAll(stream);
        console.log("--- XML DUMP START ---");
        console.log(content.toString());
        console.log("--- XML DUMP END ---");

        console.log("Taking screenshot...");
        const screenStream = await client.screencap(serial);
        const buffer = await adb.util.readAll(screenStream);
        const fs = require('fs');
        fs.writeFileSync('debug_screenshot.png', buffer);
        console.log("Screenshot saved as debug_screenshot.png");
    } catch (err) {
        console.error("Error:", err.message);
    }
}

debugXml();
