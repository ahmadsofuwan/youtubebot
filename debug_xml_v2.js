const adb = require("adbkit");
const fs = require('fs');
const client = adb.createClient();

async function debugXml() {
    const serial = '127.0.0.1:21503';
    try {
        console.log("Dumping XML...");
        // Try multiple times if it fails with idle state
        let xml = "";
        for(let i=0; i<3; i++) {
            try {
                const stream = await client.shell(serial, 'uiautomator dump /sdcard/debug.xml && cat /sdcard/debug.xml');
                const buffer = await adb.util.readAll(stream);
                xml = buffer.toString();
                if (xml.includes('hierarchy')) break;
            } catch (e) {
                console.log(`Attempt ${i+1} failed: ${e.message}`);
                await new Promise(r => setTimeout(r, 1000));
            }
        }
        
        fs.writeFileSync('last_dump.xml', xml);
        console.log(`Saved XML dump to last_dump.xml (${xml.length} bytes)`);
        
        const keywords = ["Skip", "Lewati", "ad_skip", "skip_ad", "Close ad"];
        keywords.forEach(kw => {
            const index = xml.toLowerCase().indexOf(kw.toLowerCase());
            if (index !== -1) {
                console.log(`Found keyword "${kw}" at index ${index}`);
                console.log("Surrounding context:", xml.substring(Math.max(0, index - 100), Math.min(xml.length, index + 200)));
            }
        });

    } catch (err) {
        console.error("Error:", err.message);
    }
}

debugXml();
