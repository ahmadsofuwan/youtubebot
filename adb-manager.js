require('dotenv').config();
const adb = require("adbkit");
const fs = require('fs');
const path = require('path');
const client = adb.createClient();
const axios = require('axios');
const { HttpsProxyAgent } = require('https-proxy-agent');
//baypas shell powershell -ExecutionPolicy Bypass


const PROCESSED_FILE = path.join(__dirname, 'processed_devices.json');

class ADBManager {
    constructor() {
        this.processedDevices = new Set();
        this.activeDevices = new Set(); // Track currently online devices
        this.resetProcessedDevices();
    }

    resetProcessedDevices() {
        try {
            this.processedDevices = new Set();
            this.saveProcessedDevices();
            console.log(`[ADB] Daftar perangkat terproses telah direset untuk sesi baru.`);
        } catch (err) {
            console.error('[ADB] Gagal mereset file processed_devices.json:', err.message);
        }
    }

    saveProcessedDevices() {
        try {
            fs.writeFileSync(PROCESSED_FILE, JSON.stringify([...this.processedDevices], null, 2));
        } catch (err) {
            console.error('[ADB] Gagal menyimpan file processed_devices.json:', err.message);
        }
    }

  async listDevices() {
    try {
      const devices = await client.listDevices();
      return devices;
    } catch (err) {
      console.error("Error listing devices:", err);
      return [];
    }
  }

    async stopYouTube(serial) {
        console.log(`[ADB] Menghentikan paksa (force-stop) YouTube pada ${serial}`);
        return this.shell(serial, 'am force-stop com.google.android.youtube');
    }

    async dismissYouTubeDismissibleDialogs(serial) {
        console.log(`[ADB] Mengecek dialog yang menghalangi pada ${serial}`);
        try {
            const xmlStream = await client.shell(serial, 'uiautomator dump /sdcard/dialog.xml && cat /sdcard/dialog.xml');
            const xml = (await adb.util.readAll(xmlStream)).toString();
            
            // 1. Tangani dialog Login/Error
            if (xml.includes('signing in') || xml.includes('Sign in') || xml.includes('Masuk')) {
                console.log(`[ADB] Dialog login terdeteksi pada ${serial}, mencoba menekan BACK...`);
                await this.keyevent(serial, 4);
                await new Promise(resolve => setTimeout(resolve, 1000));
            }

            // 2. Tangani dialog "Open with" (Pilihan Aplikasi)
            if (xml.includes('Open with') || xml.includes('Buka dengan') || xml.includes('android:id/resolver_list')) {
                console.log(`[ADB] Dialog "Open with" terdeteksi pada ${serial}`);
                
                // Cari koordinat teks "YouTube" di dalam picker
                const ytMatch = xml.match(/text="YouTube"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/);
                if (ytMatch) {
                    const x = Math.floor((parseInt(ytMatch[1]) + parseInt(ytMatch[3])) / 2);
                    const y = Math.floor((parseInt(ytMatch[2]) + parseInt(ytMatch[4])) / 2);
                    await this.tap(serial, x, y);
                    await new Promise(resolve => setTimeout(resolve, 500));
                    
                    // Cari tombol "Hanya sekali" atau "Just once" atau "ALWAYS"
                    const onceMatch = xml.match(/text="(JUST ONCE|HANYA SEKALI|ALWAYS|SELALU)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/i);
                    if (onceMatch) {
                        const ox = Math.floor((parseInt(onceMatch[2]) + parseInt(onceMatch[4])) / 2);
                        const oy = Math.floor((parseInt(onceMatch[3]) + parseInt(onceMatch[5])) / 2);
                        await this.tap(serial, ox, oy);
                    } else {
                        // Fallback jika tidak ketemu teks, coba klik area bawah picker
                        await this.tap(serial, 300, 1800); 
                    }
                }
                await new Promise(resolve => setTimeout(resolve, 2000));
            }
        } catch (err) {
            console.error('[ADB] Error dismiss dialog:', err.message);
        }
    }

    async openYouTube(serial) {
        console.log(`[Auto-Launch] Menjalankan YouTube pada ${serial}`);
        // Menggunakan monkey untuk cara yang paling stabil membuka launcher utama YouTube
        return this.shell(serial, 'monkey -p com.google.android.youtube -c android.intent.category.LAUNCHER 1');
    }

  async getDeviceProperties(serial) {
    try {
      const stream = await client.shell(serial, 'wm size');
      const output = (await adb.util.readAll(stream)).toString();
      const match = output.match(/Physical size: (\d+)x(\d+)/);
      if (match) {
        return { width: parseInt(match[1]), height: parseInt(match[2]) };
      }
    } catch (err) {
      console.error(`Error getting resolution for ${serial}:`, err);
    }
    return { width: 1080, height: 1920 };
  }

  async shell(serial, command) {
    try {
      const stream = await client.shell(serial, command);
      return adb.util.readAll(stream);
    } catch (err) {
      console.error(`Error executing shell command on ${serial}:`, err);
      throw err;
    }
  }

  async takeScreenshot(serial) {
    try {
      const stream = await client.screencap(serial);
      return adb.util.readAll(stream);
    } catch (err) {
      console.error(`Error taking screenshot on ${serial}:`, err);
      throw err;
    }
  }

  async tap(serial, x, y) {
    return this.shell(serial, `input tap ${x} ${y}`);
  }

  async swipe(serial, x1, y1, x2, y2, duration) {
    return this.shell(
      serial,
      `input swipe ${x1} ${y1} ${x2} ${y2} ${duration}`,
    );
  }

  async keyevent(serial, keycode) {
    return this.shell(serial, `input keyevent ${keycode}`);
  }

    async forcePortrait(serial) {
        console.log(`[ADB] Memaksa orientasi Portrait pada ${serial}`);
        try {
            await this.shell(serial, 'settings put system accelerometer_rotation 0');
            await this.shell(serial, 'settings put system user_rotation 0');
        } catch (err) {
            console.error(`[ADB] Gagal memaksa portrait pada ${serial}:`, err.message);
        }
    }

    async getFocusedWindow(serial) {
        try {
            const stream = await client.shell(serial, 'dumpsys window | grep mCurrentFocus');
            const output = (await adb.util.readAll(stream)).toString().trim();
            return output.toLowerCase();
        } catch (err) {
            return '';
        }
    }

    async isPlayStoreOpen(serial) {
        const focus = await this.getFocusedWindow(serial);
        // Typical focus for Play Store: mCurrentFocus=Window{... com.android.vending/com.google.android.finsky.activities.MainActivity}
        return focus.includes('vending') || focus.includes('playstore') || focus.includes('play.store');
    }

    async reboot(serial) {
        console.log(`[ADB] Proses selesai, merestart perangkat ${serial}...`);
        try {
            await this.clearProxy(serial); // Bersihkan proxy sebelum reboot agar bersih saat nyala lagi
            return this.shell(serial, 'reboot');
        } catch (err) {
            console.error(`[ADB] Gagal merestart perangkat ${serial}:`, err.message);
        }
    }

    async getRandomProxy() {
        const filePath = path.join(__dirname, 'proxy.txt');
        if (!fs.existsSync(filePath)) return null;
        const content = fs.readFileSync(filePath, 'utf8');
        const lines = content.split('\n').map(l => l.trim()).filter(l => l !== '');
        if (lines.length === 0) return null;
        return lines[Math.floor(Math.random() * lines.length)];
    }

    async setProxy(serial, proxy) {
        console.log(`[ADB] Memasang proxy ${proxy} pada ${serial}`);
        try {
            // Format proxy: host:port
            await this.shell(serial, `settings put global http_proxy ${proxy}`);
            return true;
        } catch (err) {
            console.error(`[ADB] Gagal memasang proxy:`, err.message);
            return false;
        }
    }

    async clearProxy(serial) {
        console.log(`[ADB] Membersihkan proxy pada ${serial}`);
        try {
            await this.shell(serial, 'settings put global http_proxy :0');
        } catch (err) {
            console.error(`[ADB] Gagal membersihkan proxy:`, err.message);
        }
    }

    async testProxyNode(proxy) {
        try {
            // Kita asumsikan formatnya host:port
            const agent = new HttpsProxyAgent(`http://${proxy}`);
            const response = await axios.get('https://www.google.com', {
                httpsAgent: agent,
                proxy: false, 
                timeout: 8000
            });
            return response.status === 200;
        } catch (err) {
            return false;
        }
    }

    async getAppliedProxy(serial) {
        try {
            const stream = await client.shell(serial, 'settings get global http_proxy');
            const output = (await adb.util.readAll(stream)).toString().trim();
            // Jika tidak ada proxy, output biasanya ":0" atau "null"
            if (output === ":0" || output === "null") return null;
            return output;
        } catch (err) {
            console.error(`[ADB] Gagal mengecek setting proxy:`, err.message);
            return null;
        }
    }

    async ping(serial, host = 'www.youtube.com') {
        console.log(`[ADB] Mencoba ping ke ${host} pada ${serial}...`);
        try {
            // -c 1: kirim 1 paket, -W 5: timeout 5 detik
            const stream = await client.shell(serial, `ping -c 1 -W 5 ${host}`);
            const output = (await adb.util.readAll(stream)).toString();
            return output.includes('1 packets transmitted, 1 received') || output.includes('1 received');
        } catch (err) {
            console.error(`[ADB] Gagal melakukan ping:`, err.message);
            return false;
        }
    }

    async getRandomVideo() {
        const filePath = path.join(__dirname, 'video_list.txt');
        if (!fs.existsSync(filePath)) return null;
        
        const content = fs.readFileSync(filePath, 'utf8');
        const lines = content.split('\n').filter(line => line.trim() !== '');
        if (lines.length === 0) return null;
        
        const randomLine = lines[Math.floor(Math.random() * lines.length)];
        const parts = randomLine.split(',').map(p => p.trim());
        
        let title = parts[0];
        let channel = parts.length > 1 ? parts[1] : null;
        let durationStr = parts.length > 2 ? parts[2] : null;

        const min = parseInt(process.env.WATCH_MIN_MINUTES) || 2;
        const max = parseInt(process.env.WATCH_MAX_MINUTES) || 5;
        const randomDuration = Math.floor(Math.random() * (max - min + 1)) + min;
        
        const duration = durationStr ? parseInt(durationStr) : randomDuration;
        
        return { 
            query: channel ? `${title} ${channel}` : title, 
            title: title,
            channel: channel,
            duration: duration 
        };
    }

    async searchYouTube(serial, query) {
        console.log(`[ADB] Mencari "${query}" di YouTube pada ${serial}`);
        // Memaksa menggunakan paket YouTube untuk menghindari dialog "Open with"
        const encodedQuery = encodeURIComponent(query);
        return this.shell(serial, `am start -a android.intent.action.VIEW -d "https://www.youtube.com/results?search_query=${encodedQuery}" com.google.android.youtube`);
    }

    async clickFirstVideo(serial, videoInfo) {
        const { title, channel } = videoInfo;
        console.log(`[ADB] Mencari video "${title}" pada ${serial}...`);
        
        const maxScrollAttempts = 5;
        const escapedTitle = title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

        await new Promise(resolve => setTimeout(resolve, 5000));
        for (let attempt = 0; attempt <= maxScrollAttempts; attempt++) {
            // Delay 5 detik sebelum dump sesuai permintaan
            
            if (attempt > 0) {
                console.log(`[ADB] Judul belum ditemukan, scroll down ke-${attempt} pada ${serial}...`);
                await this.swipe(serial, 540, 1600, 540, 600, 1000);
                await new Promise(resolve => setTimeout(resolve, 2000));
            }

            try {
                const xmlStream = await client.shell(serial, 'uiautomator dump /sdcard/view.xml && cat /sdcard/view.xml');
                const xml = (await adb.util.readAll(xmlStream)).toString();
                
                // 1. Kumpulkan posisi Iklan/Sponsored
                const adPositions = [];
                const adPatterns = [
                    /text="(Ad|Sponsored|Iklan|Promoted)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/gi,
                    /resource-id="[^"]*[:\/]ad_badge"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/gi
                ];

                for (const pattern of adPatterns) {
                    const matches = xml.matchAll(pattern);
                    for (const m of matches) {
                        adPositions.push({ y1: parseInt(m[2]), y2: parseInt(m[4]) });
                    }
                }

                // 2. Cari elemen Judul Video
                const titlePatterns = [
                    new RegExp(`text="[^"]*${escapedTitle}[^"]*"[^>]*bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"`, 'gi'),
                    /resource-id="[^"]*[:\/]title"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/gi
                ];

                let bestMatch = null;

                for (const pattern of titlePatterns) {
                    const matches = xml.matchAll(pattern);
                    for (const m of matches) {
                        const x1 = parseInt(m[1]), y1 = parseInt(m[2]), x2 = parseInt(m[3]), y2 = parseInt(m[4]);
                        const centerX = Math.floor((x1 + x2) / 2), centerY = Math.floor((y1 + y2) / 2);

                        if (centerY < 250 || centerY > 1900) continue;

                        const isAd = adPositions.some(adPos => Math.abs(adPos.y1 - y1) < 350);
                        if (isAd) {
                            console.log(`[ADB] Skip Y=${centerY} (Terdeteksi Iklan)`);
                            continue;
                        }

                        bestMatch = { x: centerX, y: centerY };
                        break;
                    }
                    if (bestMatch) break;
                }

                if (bestMatch) {
                    console.log(`[ADB] Video ditemukan di ${bestMatch.x}, ${bestMatch.y}`);
                    return this.tap(serial, bestMatch.x, bestMatch.y);
                }

            } catch (err) {
                console.error('[ADB] Error dump XML:', err.message);
            }
        }

        // Jika setelah 5x scroll tidak ketemu, lempar error agar cycle restart dari awal
        throw new Error(`Gagal menemukan video "${title}" setelah 5 kali scroll.`);
    }

    async findAndClickAdBanner(serial) {
        console.log(`[ADB] Mencari banner iklan di bawah video pada ${serial}`);
        
        let attempts = 0;
        const maxAttempts = 3; // Scroll up to 3 times to find the ad

        while (attempts < maxAttempts) {
            try {
                const xmlStream = await client.shell(serial, 'uiautomator dump /sdcard/banner.xml && cat /sdcard/banner.xml');
                const xml = (await adb.util.readAll(xmlStream)).toString();
                
                if (xml && xml.length > 100) {
                    const keywords = "Sponsored|Iklan|Visit site|Visit advertiser|Kunjungi situs|Kunjungi pengiklan|Install|Pasang|Buka|Open|Learn more|Pelajari selengkapnya";
                    const resIds = "ad_call_to_action_button|visit_advertiser_button|ad_cta_button|cta_button|promoted_ad_cta";
                    
                    const patterns = [
                        new RegExp(`text="[^"]*(${keywords})[^"]*"[^>]*bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"`, 'i'),
                        new RegExp(`content-desc="[^"]*(${keywords})[^"]*"[^>]*bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"`, 'i'),
                        new RegExp(`resource-id="[^"]*[:\\/](${resIds})[^"]*"[^>]*bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"`, 'i'),
                        new RegExp(`bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"[^>]*(text|content-desc|resource-id)="[^"]*(${keywords}|${resIds})[^"]*"`, 'i')
                    ];

                    let bannerMatch = null;
                    let centerX, centerY;

                    for (const pattern of patterns) {
                        bannerMatch = xml.match(pattern);
                        if (bannerMatch) {
                            if (pattern.source.startsWith('bounds')) {
                                centerX = Math.floor((parseInt(bannerMatch[1]) + parseInt(bannerMatch[3])) / 2);
                                centerY = Math.floor((parseInt(bannerMatch[2]) + parseInt(bannerMatch[4])) / 2);
                            } else {
                                centerX = Math.floor((parseInt(bannerMatch[2]) + parseInt(bannerMatch[4])) / 2);
                                centerY = Math.floor((parseInt(bannerMatch[3]) + parseInt(bannerMatch[5])) / 2);
                            }
                            break;
                        }
                    }
                    
                    if (bannerMatch) {
                        console.log(`[ADB] Banner iklan ditemukan! Mengunjungi pengiklan di ${centerX}, ${centerY}`);
                        await this.tap(serial, centerX, centerY);
                        return true;
                    }
                }
            } catch (err) {
                console.error('[ADB] Gagal mencari banner iklan:', err.message);
            }

            console.log(`[ADB] Banner tidak ditemukan, mencoba scroll via Key Event (DPAD_DOWN)... (${attempts + 1}/${maxAttempts})`);
            // Menggunakan Key Event arah bawah (KEYCODE_DPAD_DOWN = 20) 
            // Kita tekan 5 kali untuk mensimulasikan scroll ke bawah tanpa risiko salah klik player
            for (let i = 0; i < 5; i++) {
                await this.keyevent(serial, 20);
                await new Promise(resolve => setTimeout(resolve, 300));
            }
            
            await new Promise(resolve => setTimeout(resolve, 3000)); // Tunggu konten dirender
            attempts++;
        }
        
        return false;
    }

    async slowScroll(serial, durationSeconds) {
        console.log(`[ADB] Melakukan slow scroll di situs iklan selama ${durationSeconds} detik pada ${serial}`);
        const endTime = Date.now() + (durationSeconds * 1000);
        
        while (Date.now() < endTime) {
            const startX = Math.floor(Math.random() * 400) + 340;
            const startY = Math.floor(Math.random() * 400) + 1200;
            const endY = startY - (Math.floor(Math.random() * 300) + 200);
            const duration = Math.floor(Math.random() * 1000) + 1000;
            
            await this.swipe(serial, startX, startY, startX, endY, duration);
            await new Promise(resolve => setTimeout(resolve, Math.floor(Math.random() * 3000) + 2000));
            
            if (Math.random() > 0.85) {
                await this.swipe(serial, startX, endY, startX, startY, 1500);
                await new Promise(resolve => setTimeout(resolve, 2000));
            }
        }
    }

    async checkAndSkipAds(serial, useFallback = false) {
        try {
            const xmlStream = await client.shell(serial, 'uiautomator dump /sdcard/ads.xml && cat /sdcard/ads.xml');
            const xml = (await adb.util.readAll(xmlStream)).toString();
            
            const keywords = "Skip";
            const resIds = "skip_ad|skip_button|skip_ads|skip_ad_button|skip_button_container|modern_skip_ad_button|next_gen_skip_ad_button|common_skip_ad_button";

            const patterns = [
                new RegExp(`text="[^"]*(${keywords})[^"]*"[^>]*bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"`, 'i'),
                new RegExp(`content-desc="[^"]*(${keywords})[^"]*"[^>]*bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"`, 'i'),
                new RegExp(`resource-id="[^"]*(${resIds})[^"]*"[^>]*bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"`, 'i'),
                new RegExp(`bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"[^>]*(text|content-desc|resource-id)="[^"]*(${keywords}|${resIds})[^"]*"`, 'i')
            ];

            let adMatch = null;
            let centerX, centerY;

            for (const pattern of patterns) {
                adMatch = xml.match(pattern);
                if (adMatch) {
                    if (pattern.source.startsWith('bounds')) {
                        centerX = Math.floor((parseInt(adMatch[1]) + parseInt(adMatch[3])) / 2);
                        centerY = Math.floor((parseInt(adMatch[2]) + parseInt(adMatch[4])) / 2);
                    } else {
                        centerX = Math.floor((parseInt(adMatch[2]) + parseInt(adMatch[4])) / 2);
                        centerY = Math.floor((parseInt(adMatch[3]) + parseInt(adMatch[5])) / 2);
                    }
                    break;
                }
            }
            
            if (adMatch) {
                console.log(`[ADB] Iklan (Skip) terdeteksi! Mengeklik di ${centerX}, ${centerY}`);
                await this.tap(serial, centerX, centerY);
                return true;
            }
            
            // Fallback koordinat dinamis jika diminta ATAU jika kata "skip" terdeteksi tapi posisi tidak ketemu
            if (useFallback || xml.toLowerCase().includes('skip') || xml.toLowerCase().includes('lewati')) {
                 const res = await this.getDeviceProperties(serial);
                 // Area Skip biasanya di kanan bawah player (sekitar 90% lebar, 25% tinggi)
                 const fx = Math.floor(res.width * 0.90);
                 const fy = Math.floor(res.height * 0.25);
                 console.log(`[ADB] Menggunakan fallback koordinat Skip Ad (${fx}, ${fy}) pada ${serial}...`);
                 await this.tap(serial, fx, fy); 
                 return true;
            }
        } catch (err) {
            // Silently ignore during video playback
        }
        return false;
    }

    async runAutomationFlow(serial) {
        try {
            const video = await this.getRandomVideo();
            if (!video) {
                console.log(`[ADB] video_list.txt kosong atau tidak ada.`);
                return;
            }

            // --- PROXY SETUP DENGAN VERIFIKASI SETTINGS & PING ---
            let proxyApplied = false;
            let retryCount = 0;
            while (!proxyApplied && retryCount < 5) {
                const targetProxy = await this.getRandomProxy();
                if (!targetProxy) {
                    console.log('[ADB] proxy.txt tidak ditemukan atau kosong. Melewati setup proxy.');
                    break;
                }
                
                console.log(`[ADB] Mengetes proxy ${targetProxy} dari Node.js...`);
                const isWorking = await this.testProxyNode(targetProxy);
                if (!isWorking) {
                    retryCount++;
                    console.log(`[ADB] Proxy ${targetProxy} GAGAL tes dari Node.js. Mencoba yang lain... (${retryCount}/5)`);
                    continue;
                }
                console.log(`[ADB] Proxy ${targetProxy} BERHASIL tes dari Node.js.`);

                await this.setProxy(serial, targetProxy);
                await new Promise(resolve => setTimeout(resolve, 2000));
                
                const appliedProxy = await this.getAppliedProxy(serial);
                console.log(`[ADB] Proxy yang terpasang di sistem pada ${serial}: ${appliedProxy || 'None'}`);

                if (appliedProxy && appliedProxy.includes(targetProxy)) {
                    // Verifikasi koneksi internet via proxy dengan ping ke YouTube
                    const canPing = await this.ping(serial);
                    if (canPing) {
                        proxyApplied = true;
                        console.log(`[ADB] Proxy ${targetProxy} BERHASIL (Settings OK & Ping OK) pada ${serial}.`);
                    } else {
                        retryCount++;
                        console.log(`[ADB] Proxy ${targetProxy} GAGAL (Tersambung tapi tidak bisa akses internet). Mencoba lagi... (${retryCount}/5)`);
                        await this.clearProxy(serial);
                        await new Promise(resolve => setTimeout(resolve, 1000));
                    }
                } else {
                    retryCount++;
                    console.log(`[ADB] Proxy ${targetProxy} GAGAL pada ${serial} (System setting tidak cocok). Mencoba lagi... (${retryCount}/5)`);
                    await this.clearProxy(serial);
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
            }

            if (!proxyApplied) {
                console.log(`[ADB] Gagal memasang proxy yang berfungsi pada ${serial} setelah ${retryCount} kali percobaan.`);
            }
            // -----------------------------------------------
            await this.forcePortrait(serial);
            await new Promise(resolve => setTimeout(resolve, 1000));

            await this.stopYouTube(serial);
            await new Promise(resolve => setTimeout(resolve, 1000));

            await this.openYouTube(serial);
            await new Promise(resolve => setTimeout(resolve, 3000)); 
            
            // Paksa portrait lagi setelah YouTube terbuka (mencegah YouTube start di landscape)
            await this.forcePortrait(serial);
            await new Promise(resolve => setTimeout(resolve, 2000));
            
            // Coba lewati dialog login jika ada
            await this.dismissYouTubeDismissibleDialogs(serial);
            await new Promise(resolve => setTimeout(resolve, 2000));

            await this.searchYouTube(serial, video.query);
            await new Promise(resolve => setTimeout(resolve, 5000)); 

            // Cek lagi jika ada dialog "Open with" setelah pencarian
            await this.dismissYouTubeDismissibleDialogs(serial);

            await this.clickFirstVideo(serial, video);
            console.log(`[ADB] Menonton "${video.query}" selama ${video.duration} menit di ${serial}`);

             // Jeda sebelum cek iklan pre-roll (Menghindari scan terlalu dini)
             await new Promise(resolve => setTimeout(resolve, 5000));

             // Pengecekan iklan pre-roll SEGERA (Dipercepat)
             console.log(`[ADB] Menunggu tombol skip di ${serial}...`);
             let skipFound = false;
             for (let i = 0; i < 6; i++) { // Pantau selama ~12-15 detik (Dump + 1s jeda)
                 skipFound = await this.checkAndSkipAds(serial, false);
                 if (skipFound) {
                     console.log(`[ADB] Iklan berhasil dilewati via XML di ${serial}`);
                     break;
                 }
                 await new Promise(resolve => setTimeout(resolve, 1000));
             }

             if (!skipFound) {
                 console.log(`[ADB] Skip tidak terdeteksi via XML dalam 15 detik, gunakan fallback tap...`);
                 await this.checkAndSkipAds(serial, true);
             }

            // Jeda sebentar sebelum cek banner iklan
            await new Promise(resolve => setTimeout(resolve, 2000));
            
            const bannerClicked = await this.findAndClickAdBanner(serial);
            if (bannerClicked) {
                await new Promise(resolve => setTimeout(resolve, 5000));
                
                // Pastikan bukan Play Store yang terbuka
                if (await this.isPlayStoreOpen(serial)) {
                    console.log(`[ADB] Play Store terdeteksi setelah klik iklan, kembali ke YouTube...`);
                    await this.keyevent(serial, 4); // Back
                    await new Promise(resolve => setTimeout(resolve, 2000));
                } else {
                    const scrollDuration = Math.floor(Math.random() * 30) + 60;
                    await this.slowScroll(serial, scrollDuration);
                    
                    console.log(`[ADB] Selesai mengunjungi iklan, kembali ke YouTube pada ${serial}`);
                    await this.keyevent(serial, 4); 
                    await new Promise(resolve => setTimeout(resolve, 3000));
                }
            }

            // Loop pengecekan iklan selama durasi nonton
            const startTime = Date.now();
            const endTime = startTime + (video.duration * 60 * 1000);
            
            const watchLoop = async () => {
                // Stop if device is no longer active
                if (!this.activeDevices.has(serial)) {
                    console.log(`[ADB] Perangkat ${serial} terputus, menghentikan monitoring.`);
                    return;
                }

                if (Date.now() > endTime) {
                    console.log(`[ADB] Selesai menonton di ${serial}.`);
                    await this.stopYouTube(serial);
                    console.log(`[ADB] Jeda 5 detik untuk istirahat pada ${serial}...`);
                    await new Promise(resolve => setTimeout(resolve, 5000));
                    
                    console.log(`[ADB] Memulai ulang alur untuk ${serial}...`);
                    if (this.activeDevices.has(serial)) {
                        this.runAutomationFlow(serial); // REPEAT THE FLOW
                    }
                    return;
                }
                
                await this.checkAndSkipAds(serial);
                console.log(`[ADB] Memantau iklan (real-time) di ${serial}...`);
                setTimeout(watchLoop, 3000); 
            };
            
            watchLoop();

        } catch (err) {
            console.error(`[ADB] Error pada flow otomatisasi ${serial}:`, err.message);
            
            // Only retry if device is still active
            if (this.activeDevices.has(serial)) {
                console.log(`[ADB] Mencoba ulang dalam 10 detik untuk ${serial}...`);
                setTimeout(() => this.runAutomationFlow(serial), 10000);
            } else {
                console.log(`[ADB] Perangkat ${serial} sudah offline, menghentikan percobaan ulang.`);
            }
        }
    }

    trackDevices(onUpdate) {
        // Ambil daftar perangkat yang sudah tersambung di awal
        client.listDevices()
            .then(devices => {
                devices.forEach(device => {
                    if (device.type !== 'offline') {
                        this.activeDevices.add(device.id);
                    }
                });
            })
            .catch(err => console.error('[ADB] Gagal list devices awal:', err.message));

        client.trackDevices()
            .then((tracker) => {
                tracker.on('add', async device => {
                    console.log(`[ADB] Perangkat terdeteksi: ${device.id}`);
                    this.activeDevices.add(device.id);
                    
                    if (!this.processedDevices.has(device.id)) {
                        this.processedDevices.add(device.id);
                        this.saveProcessedDevices();
                        
                        // Jalankan alur otomatisasi
                        setTimeout(() => this.runAutomationFlow(device.id), 2000);
                    }
                    
                    onUpdate('add', device);
                });

                tracker.on('remove', device => {
                    console.log(`[ADB] Perangkat dilepas/reboot: ${device.id}`);
                    this.activeDevices.delete(device.id);
                    // Hapus dari processedDevices agar saat menyala lagi (reconnect), siklus berjalan ulang
                    this.processedDevices.delete(device.id);
                    this.saveProcessedDevices();
                    onUpdate('remove', device);
                });

                tracker.on("end", () => console.log("Tracking stopped"));
            })
            .catch(err => {
                console.error('Error tracking devices (Is ADB running?):', err.message);
                onUpdate('error', err);
            });
    }
}

module.exports = new ADBManager();
