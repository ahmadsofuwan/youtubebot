import subprocess
import time
import pytesseract
import cv2
import sys
import shlex
import random
import re
import os
import threading
import atexit
import unicodedata
import json

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich import box
from rich.text import Text

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def get_range_input(label, default):
    raw = input(f"{label} range detik (min,max) [default {default[0]},{default[1]}]: ").strip()
    if not raw:
        return default
    nums = re.findall(r"\d+", raw)
    if len(nums) >= 2:
        return [int(nums[0]), int(nums[1])]
    if len(nums) == 1:
        val = int(nums[0])
        return [val, val]
    return default

def random_delay(range_secs):
    lo, hi = float(range_secs[0]), float(range_secs[1])
    if hi < lo:
        lo, hi = hi, lo
    return random.uniform(lo, hi)

def sleep_with_stop(seconds, stop_event, step=0.5):
    end = time.time() + max(0, float(seconds))
    while time.time() < end:
        if STOP_ALL_EVENT.is_set():
            return False
        if stop_event is not None and stop_event.is_set():
            return False
        remaining = end - time.time()
        time.sleep(min(step, remaining))
    return True

RANGE_DURASI_NONTON = get_range_input("Durasi nonton", [2,3])
RANGE_DURASI_IKLAN = get_range_input("Durasi iklan", [2,3])
RANGE_DURASI_JEDA = get_range_input("Durasi jeda", [2,3])
JUDUL_FILE = "judul.txt"
PROXY_FILE = "proxy.txt"
ADB_PATH = r"C:\adb\adb.exe"
VERBOSE = False
DEBUG_DIR = "debug"
INPUT_METHOD = "stable"  # "stable" (default), "auto", "clipboard"
RECORD_FILE = "record.json"
ASCII_FILE = "asci.txt"

STATUS_LOCK = threading.Lock()
DEVICE_STATUS = {}
RECORD_LOCK = threading.Lock()
STOP_ALL_EVENT = threading.Event()
DEVICE_EVENTS_LOCK = threading.Lock()
DEVICE_STOP_EVENTS = {}
TITLE_LOCK = threading.Lock()
TITLES = []
TITLES_TOTAL = 0
STATUS_COLUMNS = ["device", "proxy", "judul", "progress", "song_success", "ads_visited", "status"]
TABLE_STYLE = "unicode"  # "unicode" atau "ascii"
STATUS_LABELS = {
    "device": "DEVICE",
    "proxy": "PROXY",
    "judul": "JUDUL",
    "progress": "PROGRESS",
    "song_success": "SONG_SUCCESS",
    "ads_visited": "ADS_VISITED",
    "status": "STATUS",
}
STATUS_MAX_WIDTH = {
    "device": 16,
    "proxy": 30,
    "judul": 48,
    "progress": 10,
    "song_success": 12,
    "ads_visited": 12,
    "status": 48,
}
DEVICE_STABLE_SECS = 18
DEVICE_MISSING_SECS = 6
CONSOLE = Console(force_terminal=True, legacy_windows=False)
LIVE = None
LIVE_LOCK = threading.Lock()
LAST_RENDER = 0.0
RENDER_INTERVAL = 0.2
USE_ANSI = sys.stdout.isatty()
THREAD_CTX = threading.local()

def _load_ascii_banner(path=ASCII_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().rstrip()
    except Exception:
        return ""

ASCII_BANNER = _load_ascii_banner()

def _truncate(text, max_len):
    if max_len is None:
        return text
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[:max_len - 3] + "..."

def _format_status_table():
    rows = [DEVICE_STATUS[k] for k in sorted(DEVICE_STATUS)]
    header = [STATUS_LABELS[c] for c in STATUS_COLUMNS]

    styles = {
        "ascii": {
            "h": "-",
            "v": "|",
            "tl": "+",
            "tr": "+",
            "bl": "+",
            "br": "+",
            "tm": "+",
            "bm": "+",
            "lm": "+",
            "rm": "+",
            "mm": "+",
        },
        "unicode": {
            "h": "─",
            "v": "│",
            "tl": "┌",
            "tr": "┐",
            "bl": "└",
            "br": "┘",
            "tm": "┬",
            "bm": "┴",
            "lm": "├",
            "rm": "┤",
            "mm": "┼",
        },
    }
    style = styles.get(TABLE_STYLE, styles["ascii"])

    # compute widths
    widths = []
    for col in STATUS_COLUMNS:
        max_width = STATUS_MAX_WIDTH.get(col)
        values = [str(r.get(col, "")) for r in rows]
        max_len = max([len(STATUS_LABELS[col])] + [len(v) for v in values]) if values else len(STATUS_LABELS[col])
        if max_width is not None:
            max_len = min(max_len, max_width)
        widths.append(max_len)

    def fmt_row(items):
        cells = []
        for i, item in enumerate(items):
            text = _truncate(str(item), widths[i])
            cells.append(text.ljust(widths[i]))
        return f" {style['v']} ".join(cells)

    def border(left, mid, right):
        return left + mid.join(style["h"] * w for w in widths) + right

    lines = []
    if ASCII_BANNER:
        lines.extend(ASCII_BANNER.splitlines())
        lines.append("")
    lines.append(border(style["tl"], style["tm"], style["tr"]))
    lines.append(style["v"] + fmt_row(header) + style["v"])
    lines.append(border(style["lm"], style["mm"], style["rm"]))
    for r in rows:
        values = [r.get(c, "") for c in STATUS_COLUMNS]
        lines.append(style["v"] + fmt_row(values) + style["v"])
    lines.append(border(style["bl"], style["bm"], style["br"]))
    return lines

def _build_rich_table():
    table = Table(box=box.SQUARE, show_header=True, header_style="bold")
    for col in STATUS_COLUMNS:
        max_w = STATUS_MAX_WIDTH.get(col)
        justify = "right" if col in ("song_success", "ads_visited", "progress") else "left"
        overflow = "ellipsis"
        if col == "proxy":
            overflow = "fold"
        table.add_column(
            STATUS_LABELS[col],
            overflow=overflow,
            no_wrap=False,
            max_width=max_w,
            justify=justify,
        )
    for device in sorted(DEVICE_STATUS):
        row = DEVICE_STATUS[device]
        values = []
        for c in STATUS_COLUMNS:
            val = row.get(c, "")
            if c == "status":
                values.append(_style_status(val))
            elif c == "progress":
                values.append(Text(str(val), style="bright_yellow"))
            else:
                values.append(str(val))
        table.add_row(*values)
    if ASCII_BANNER:
        return Group(Text(ASCII_BANNER, style="bright_red"), Text(""), table)
    return table

def _style_status(value):
    text = "" if value is None else str(value)
    lower = text.lower()
    upper = text.upper()

    if "stabilizing" in lower or "offline (grace)" in lower or "restart" in lower:
        return Text(text)
    if "[STOP]" in upper:
        return Text(text, style="bright_red")
    if "[START]" in upper:
        return Text(text, style="bright_green")
    if (
        "tidak ditemukan" in lower
        or "gagal" in lower
        or "error" in lower
        or "bot terdeteksi" in lower
        or "not found" in lower
    ):
        return Text(text, style="bright_red")
    return Text(text, style="bright_green")

def _ensure_live():
    global LIVE
    with LIVE_LOCK:
        if LIVE is None:
            LIVE = Live(
                _build_rich_table(),
                console=CONSOLE,
                refresh_per_second=4,
                transient=True,
                screen=True,
                auto_refresh=False,
            )
            LIVE.start()

def _stop_live():
    global LIVE
    with LIVE_LOCK:
        if LIVE is not None:
            LIVE.stop()
            LIVE = None

def clear_terminal():
    try:
        if CONSOLE.is_terminal:
            CONSOLE.clear()
    except Exception:
        pass
    try:
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
    except Exception:
        pass
    try:
        os.system("cls")
    except Exception:
        pass

atexit.register(_stop_live)

def render_status(force=False):
    global LAST_RENDER
    if STOP_ALL_EVENT.is_set():
        return
    now = time.time()
    if not force and (now - LAST_RENDER) < RENDER_INTERVAL:
        return
    LAST_RENDER = now

    if CONSOLE.is_terminal:
        _ensure_live()
        with LIVE_LOCK:
            if LIVE is not None:
                LIVE.update(_build_rich_table())
                LIVE.refresh()
        return

    lines = _format_status_table()
    if lines:
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

def update_device_status(device, **fields):
    if not device:
        return
    with STATUS_LOCK:
        current = DEVICE_STATUS.get(device, {})
        current["device"] = device
        if "song_success" not in current or "ads_visited" not in current:
            with RECORD_LOCK:
                rec = RECORD_DATA.get("devices", {}).get(device, {})
            current.setdefault("song_success", int(rec.get("song_success", 0)))
            current.setdefault("ads_visited", int(rec.get("ads_visited", 0)))
        if "progress" not in current:
            current["progress"] = ""
        for key, value in fields.items():
            if value is None:
                continue
            current[key] = value
        DEVICE_STATUS[device] = current
        render_status()

def increment_device_counter(device, field, delta=1):
    if not device:
        return
    with STATUS_LOCK:
        current = DEVICE_STATUS.get(device, {})
        current["device"] = device
        if "song_success" not in current or "ads_visited" not in current:
            with RECORD_LOCK:
                rec = RECORD_DATA.get("devices", {}).get(device, {})
            current.setdefault("song_success", int(rec.get("song_success", 0)))
            current.setdefault("ads_visited", int(rec.get("ads_visited", 0)))
        current[field] = int(current.get(field, 0)) + int(delta)
        DEVICE_STATUS[device] = current
        render_status()

    with RECORD_LOCK:
        devices = RECORD_DATA.setdefault("devices", {})
        rec = devices.setdefault(device, {"song_success": 0, "ads_visited": 0, "title_index": 0})
        rec[field] = int(rec.get(field, 0)) + int(delta)
        _save_record(RECORD_DATA)

def log_device(device, message):
    if not device:
        return
    fields = {"status": message}
    if message.startswith("proxy "):
        fields["proxy"] = message[len("proxy "):].strip()
    if message.startswith("judul "):
        fields["judul"] = message[len("judul "):].strip()
    update_device_status(device, **fields)

def log_step(device, message):
    log_device(device, f"{message}")

def register_device_event(device, stop_event):
    if not device:
        return
    with DEVICE_EVENTS_LOCK:
        DEVICE_STOP_EVENTS[device] = stop_event

def unregister_device_event(device):
    if not device:
        return
    with DEVICE_EVENTS_LOCK:
        DEVICE_STOP_EVENTS.pop(device, None)

def stop_all_devices():
    with DEVICE_EVENTS_LOCK:
        for ev in DEVICE_STOP_EVENTS.values():
            ev.set()

def log_current(message):
    device = getattr(THREAD_CTX, "device", None)
    if device:
        log_device(device, message)
    else:
        print(message)

def remove_device_status(device):
    with STATUS_LOCK:
        if device in DEVICE_STATUS:
            del DEVICE_STATUS[device]
            render_status(force=True)

def ensure_debug_dir():
    os.makedirs(DEBUG_DIR, exist_ok=True)

def debug_path(filename):
    ensure_debug_dir()
    if os.path.isabs(filename):
        return filename
    normalized = filename.replace("/", os.sep).replace("\\", os.sep)
    if normalized.startswith(DEBUG_DIR + os.sep):
        return normalized
    return os.path.join(DEBUG_DIR, filename)

def get_random_title(judul_file=JUDUL_FILE, device=None):
    # backward-compatible alias (now sequential)
    title, _, _ = get_next_title(device)
    return title

def run_adb(device_name, adb_command):
    args = [ADB_PATH]
    if device_name:
        args += ["-s", device_name]
    args += shlex.split(adb_command)
    result = subprocess.run(args, capture_output=True, text=True)
    return result.stdout.strip()

def run_adb_raw(device_name, adb_command):
    args = [ADB_PATH]
    if device_name:
        args += ["-s", device_name]
    args += shlex.split(adb_command)
    return subprocess.run(args, capture_output=True, text=True)


def _load_record(path=RECORD_FILE):
    if not os.path.exists(path):
        return {"devices": {}}
    if os.path.getsize(path) == 0:
        return {"devices": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"devices": {}}
        if "devices" not in data or not isinstance(data.get("devices"), dict):
            data["devices"] = {}
        return data
    except Exception:
        return {"devices": {}}

def _save_record(data, path=RECORD_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

RECORD_DATA = _load_record()
if not os.path.exists(RECORD_FILE) or os.path.getsize(RECORD_FILE) == 0:
    _save_record(RECORD_DATA)

def _load_titles(path=JUDUL_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            titles = [line.strip() for line in f if line.strip()]
    except Exception:
        titles = []
    return titles

def _ensure_titles_loaded():
    global TITLES, TITLES_TOTAL
    if TITLES:
        return
    with TITLE_LOCK:
        if TITLES:
            return
        titles = _load_titles()
        TITLES = titles
        TITLES_TOTAL = len(titles)

def get_next_title(device):
    _ensure_titles_loaded()
    if not TITLES:
        raise ValueError("judul.txt kosong")
    with TITLE_LOCK:
        with RECORD_LOCK:
            devices = RECORD_DATA.setdefault("devices", {})
            key = device or "default"
            rec = devices.setdefault(
                key,
                {"song_success": 0, "ads_visited": 0, "title_index": 0},
            )
            idx = int(rec.get("title_index", 0))
            total = TITLES_TOTAL
            if total <= 0:
                raise ValueError("judul.txt kosong")
            if idx < 0 or idx >= total:
                idx = 0
            title = TITLES[idx]
            next_idx = idx + 1
            if next_idx >= total:
                next_idx = 0
            rec["title_index"] = next_idx
            _save_record(RECORD_DATA)
        return title, idx + 1, total

def set_random_proxy(device_name, proxy_file=PROXY_FILE):
    with open(proxy_file, "r", encoding="utf-8") as f:
        proxies = [line.strip() for line in f if line.strip()]
    if not proxies:
        raise ValueError("proxy.txt kosong")
    proxy = random.choice(proxies)
    log_device(device_name, f"proxy {proxy}")
    run_adb(device_name, f"shell settings put global http_proxy {proxy}")
    return proxy

def reset_proxy(device_name):
    run_adb(device_name, "shell settings put global http_proxy :0")

def open_youtube_app(device_name):
    run_adb(device_name, "shell am force-stop com.google.android.youtube")
    run_adb(device_name, "shell pm clear com.google.android.youtube")
    time.sleep(0.5)
    run_adb(device_name, "shell monkey -p com.google.android.youtube -c android.intent.category.LAUNCHER 1")
    disable_auto_rotation(device_name)

def get_adb_devices():
    result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
    lines = result.stdout.strip().splitlines()
    devices = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices

def screenshot_emulator(device_name, save_path=None):
    if not save_path:
        safe_name = device_name.replace(":", "_")
        save_path = f"screen_{safe_name}.png"
    save_path = debug_path(save_path)
    run_adb(device_name, "shell screencap -p /sdcard/screen.png")
    run_adb(device_name, f'pull /sdcard/screen.png "{save_path}"')

def tap(device_name, x, y):
    run_adb(device_name, f"shell input tap {x} {y}")

def press_back(device_name):
    run_adb(device_name, "shell input keyevent 4")

def disable_auto_rotation(device_name):
    run_adb(device_name, "shell settings put system accelerometer_rotation 0")
    run_adb(device_name, "shell settings put system user_rotation 0")

def get_current_focus(device_name):
    output = run_adb(device_name, "shell dumpsys window | findstr mCurrentFocus")
    return output.strip().lower()

def is_play_store_open(device_name):
    focus = get_current_focus(device_name)
    if not focus:
        return False
    return ("playstore" in focus) or ("play store" in focus) or ("google play" in focus)

def wait_until_text(device_name, keyword, timeout=5):
    for _ in range(timeout):
        if STOP_ALL_EVENT.is_set():
            return False
        safe_name = device_name.replace(":", "_")
        image_path = debug_path(f"screen_{safe_name}.png")
        screenshot_emulator(device_name, image_path)
        if find_text_on_screen(image_path, keyword):
            if VERBOSE:
                log_device(device_name, f"OK ditemukan '{keyword}'")
            return True
        time.sleep(1)

def wait_until_text_fallback(device_name, keyword, timeout=5):
    for _ in range(timeout):
        if STOP_ALL_EVENT.is_set():
            return False
        safe_name = device_name.replace(":", "_")
        image_path = debug_path(f"screen_{safe_name}.png")
        screenshot_emulator(device_name, image_path)
        if find_text_on_screen(image_path, keyword):
            if VERBOSE:
                log_device(device_name, f"OK ditemukan '{keyword}'")
            return True
        if find_text_on_screen_invert(image_path, keyword):
            if VERBOSE:
                log_device(device_name, f"OK ditemukan '{keyword}' (invert)")
            return True
        time.sleep(1)
    return False

ACCEPT_KEYWORDS = [
    "accept",
    "accept all",
    "agree",
    "i agree",
    "allow all",
    "allow",
    "ok",
    "got it",
]

def try_click_accept_cookies(device_name, timeout=1, verbose=False):
    for keyword in ACCEPT_KEYWORDS:
        if wait_until_text_fallback(device_name, keyword, timeout=timeout):
            if verbose:
                log_device(device_name, f"accept ditemukan ({keyword}), mencoba klik")
            click_all_by_text_fallback(device_name, keyword)
            return True
    return False

def _preprocess_for_ocr(img, invert=False):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    thresh = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    bw = cv2.threshold(gray, 0, 255, thresh + cv2.THRESH_OTSU)[1]
    return bw

def find_text_on_screen(image_path, keyword):
    img = cv2.imread(image_path)
    if img is None:
        return False

    bw = _preprocess_for_ocr(img, invert=False)

    base = os.path.basename(image_path)
    safe_base = base.replace(".", "_")
    bw_path = debug_path(f"debug_bw_{safe_base}.png")
    cv2.imwrite(bw_path, bw)

    text = pytesseract.image_to_string(
        bw,
        lang="eng",
        config="--oem 3 --psm 6"
    )

    # if VERBOSE:
        # print("OCR RESULT:", text, file=sys.stderr)

    return keyword.lower() in text.lower()

def find_text_on_screen_invert(image_path, keyword):
    img = cv2.imread(image_path)
    if img is None:
        return False

    bw = _preprocess_for_ocr(img, invert=True)

    base = os.path.basename(image_path)
    safe_base = base.replace(".", "_")
    bw_path = debug_path(f"debug_bw_inv_{safe_base}.png")
    cv2.imwrite(bw_path, bw)

    text = pytesseract.image_to_string(
        bw,
        lang="eng",
        config="--oem 3 --psm 6"
    )

    return keyword.lower() in text.lower()

def debug_click_position(image_path):
    import cv2
    image_path = debug_path(image_path)

    def click_event(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            if scale < 1.0:
                orig_x = int(x / scale)
                orig_y = int(y / scale)
                print(f"Koordinat klik (scaled): X={x}, Y={y}")
                print(f"Koordinat klik (original): X={orig_x}, Y={orig_y}")
            else:
                print(f"Koordinat klik: X={x}, Y={y}")

    img = cv2.imread(image_path)
    window_name = "Klik untuk lihat koordinat"
    if img is None:
        print(f"[!]: Gagal membaca gambar: {image_path}")
        return

    max_w, max_h = 900, 600
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, img.shape[1], img.shape[0])
    cv2.imshow(window_name, img)
    cv2.setMouseCallback(window_name, click_event)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

def input_text_fast(device_name, text):
    buffer = ""
    for char in text:
        if char in ["@", ".", " "]:
            if buffer:
                run_adb(device_name, f"shell input text {buffer}")
                buffer = ""
                time.sleep(0.05)

            if char == " ":
                run_adb(device_name, "shell input keyevent 62")
            else:
                run_adb(device_name, f"shell input text {char}")
            time.sleep(0.05)
        else:
            buffer += char

    if buffer:
        run_adb(device_name, f"shell input text {buffer}")

def _has_non_ascii(text):
    return any(ord(ch) > 127 for ch in text)

def _normalize_ascii(text):
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if ord(ch) < 128)

def input_text_clipboard(device_name, text):
    quoted = shlex.quote(text)
    res = run_adb_raw(device_name, f"shell cmd clipboard set {quoted}")
    if res.returncode != 0:
        return False
    if res.stderr and "Unknown command" in res.stderr:
        return False
    time.sleep(0.1)
    run_adb(device_name, "shell input keyevent 279")
    return True

def input_text(device_name, text):
    if INPUT_METHOD == "clipboard":
        if input_text_clipboard(device_name, text):
            return
    elif INPUT_METHOD == "auto":
        if _has_non_ascii(text) and input_text_clipboard(device_name, text):
            return

    safe_text = _normalize_ascii(text)
    input_text_fast(device_name, safe_text)

def check_text_near_coordinate(device, x, y, keyword, radius=80, debug=False):
    """
    x, y      : titik target
    radius    : jarak area sekitar (px)
    keyword   : teks yang dicari
    """

    img_path = debug_path(f"screen_{device}.png")
    screenshot_emulator(device, img_path)

    img = cv2.imread(img_path)
    h, w = img.shape[:2]

    x1 = max(0, x - radius)
    y1 = max(0, y - radius)
    x2 = min(w, x + radius)
    y2 = min(h, y + radius)

    crop = img[y1:y2, x1:x2]

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    if debug:
        cv2.imwrite(debug_path("debug_crop.png"), crop)
        cv2.imwrite(debug_path("debug_crop_bw.png"), bw)

    text = pytesseract.image_to_string(
        bw,
        lang="eng",
        config="--oem 3 --psm 6"
    )

    # print(f"OCR sekitar ({x},{y}):", text.strip())

    return keyword.lower() in text.lower()

def find_text_position(image_path, keyword, index=0):
    from pytesseract import image_to_data
    import cv2

    img = cv2.imread(image_path)
    data = pytesseract.image_to_data(img, lang='eng', output_type=pytesseract.Output.DICT)
    tokens = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text:
            continue
        left = data["left"][i]
        top = data["top"][i]
        right = left + data["width"][i]
        bottom = top + data["height"][i]
        tokens.append({
            "text": text.lower(),
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        })

    kw = keyword.strip().lower()
    if not kw:
        return None

    kw_tokens = [t for t in kw.split() if t]
    if not kw_tokens:
        return None

    matches = []
    if len(kw_tokens) == 1:
        for tok in tokens:
            if kw in tok["text"]:
                center_x = (tok["left"] + tok["right"]) // 2
                center_y = (tok["top"] + tok["bottom"]) // 2
                matches.append((center_x, center_y))
    else:
        n = len(kw_tokens)
        for i in range(len(tokens) - n + 1):
            ok = True
            for j, kw_tok in enumerate(kw_tokens):
                if kw_tok not in tokens[i + j]["text"]:
                    ok = False
                    break
            if not ok:
                continue
            left = min(t["left"] for t in tokens[i:i + n])
            right = max(t["right"] for t in tokens[i:i + n])
            top = min(t["top"] for t in tokens[i:i + n])
            bottom = max(t["bottom"] for t in tokens[i:i + n])
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            matches.append((center_x, center_y))

    if not matches:
        return None

    if index < 0 or index >= len(matches):
        return None
    if VERBOSE:
        log_current(f"total index {keyword}: {len(matches)}")
    return matches[index]

def find_text_positions_preprocessed(image_path, keyword, invert=False):
    from pytesseract import image_to_data
    import cv2

    img = cv2.imread(image_path)
    if img is None:
        return []
    bw = _preprocess_for_ocr(img, invert=invert)
    data = pytesseract.image_to_data(bw, lang='eng', output_type=pytesseract.Output.DICT)
    tokens = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text:
            continue
        left = data["left"][i]
        top = data["top"][i]
        right = left + data["width"][i]
        bottom = top + data["height"][i]
        tokens.append({
            "text": text.lower(),
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        })

    kw = keyword.strip().lower()
    if not kw:
        return []

    kw_tokens = [t for t in kw.split() if t]
    if not kw_tokens:
        return []

    matches = []
    if len(kw_tokens) == 1:
        for tok in tokens:
            if kw in tok["text"]:
                center_x = (tok["left"] + tok["right"]) // 2
                center_y = (tok["top"] + tok["bottom"]) // 2
                matches.append((center_x, center_y))
    else:
        n = len(kw_tokens)
        for i in range(len(tokens) - n + 1):
            ok = True
            for j, kw_tok in enumerate(kw_tokens):
                if kw_tok not in tokens[i + j]["text"]:
                    ok = False
                    break
            if not ok:
                continue
            left = min(t["left"] for t in tokens[i:i + n])
            right = max(t["right"] for t in tokens[i:i + n])
            top = min(t["top"] for t in tokens[i:i + n])
            bottom = max(t["bottom"] for t in tokens[i:i + n])
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            matches.append((center_x, center_y))

    return matches

def go_home(device_name):
    run_adb(device_name, "shell input keyevent 3")
    time.sleep(1)
    run_adb(device_name, "reboot")

def wait_until_text_two_matches(device_name, keyword, timeout=5):
    for _ in range(timeout):
        if STOP_ALL_EVENT.is_set():
            return False
        safe_name = device_name.replace(":", "_")
        image_path = debug_path(f"screen_{safe_name}.png")
        screenshot_emulator(device_name, image_path)
        count = count_text_matches(image_path, keyword)
        if count >= 2:
            if VERBOSE:
                log_device(device_name, f"OK >=2 '{keyword}' ({count})")
            return True
        time.sleep(1)

def count_text_matches(image_path, keyword):
    from pytesseract import image_to_data
    import cv2

    img = cv2.imread(image_path)
    data = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT)
    tokens = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text:
            continue
        tokens.append(text.lower())

    kw = keyword.strip().lower()
    if not kw:
        return 0

    kw_tokens = [t for t in kw.split() if t]
    if not kw_tokens:
        return 0

    if len(kw_tokens) == 1:
        return sum(1 for t in tokens if kw in t)

    count = 0
    n = len(kw_tokens)
    for i in range(len(tokens) - n + 1):
        ok = True
        for j, kw_tok in enumerate(kw_tokens):
            if kw_tok not in tokens[i + j]:
                ok = False
                break
        if ok:
            count += 1
    return count

def wait_until_text_unique(device_name, keyword, timeout=5):
    for _ in range(timeout):
        if STOP_ALL_EVENT.is_set():
            return False
        token = (keyword.split()[0] if keyword else "text")
        safe = "".join(ch for ch in token if ch.isalnum() or ch in ("_", "-"))
        if not safe:
            safe = "text"
        image_path = debug_path(f"screen_{device_name}_{safe}.png")
        screenshot_emulator(device_name, image_path)
        if find_text_on_screen(image_path, keyword):
            if VERBOSE:
                log_device(device_name, f"OK ditemukan '{keyword}'")
            return True
        time.sleep(1)

def get_device_size(device_name):
    output = run_adb(device_name, "shell wm size")
    # Expected: "Physical size: 1080x2400"
    match = re.search(r"(\d+)\s*x\s*(\d+)", output)
    if not match:
        raise ValueError(f"Gagal parse ukuran device: {output}")
    return int(match.group(1)), int(match.group(2))

def scroll_page(device_name, duration_ms=300, scroll_ratio=0.5, direction="down"):
    """
    scroll_ratio: 0.1 - 0.9 (proporsi tinggi layar yang ingin discroll)
    direction: "down" (default) atau "up"
    """
    width, height = get_device_size(device_name)
    x = width // 2
    ratio = max(0.1, min(float(scroll_ratio), 0.9))

    if direction == "up":
        # swipe dari atas ke bawah -> konten bergerak ke atas (scroll naik)
        y_start = int(height * 0.2)
        y_end = int(y_start + (height * ratio))
        y_end = min(height, y_end)
    else:
        # swipe dari bawah ke atas -> konten bergerak ke bawah (scroll turun)
        y_start = int(height * 0.8)
        y_end = int(y_start - (height * ratio))
        y_end = max(0, y_end)

    run_adb(device_name, f"shell input swipe {x} {y_start} {x} {y_end} {duration_ms}")

def scroll_natural(device_name, total_seconds, verbose=False, stop_event=None):
    """
    Pola scroll lebih natural dalam total_seconds.
    """
    start = time.time()
    phases = [
        ("down", random.randint(3, 6)),
        ("up", random.randint(1, 3)),
        ("down", random.randint(2, 5)),
        ("up", random.randint(1, 2)),
        ("down", random.randint(2, 4)),
    ]

    phase_idx = 0
    while True:
        if STOP_ALL_EVENT.is_set():
            break
        if stop_event is not None and stop_event.is_set():
            break
        elapsed = time.time() - start
        if elapsed >= total_seconds:
            break

        direction, remaining = phases[phase_idx]
        if remaining <= 0:
            phase_idx = (phase_idx + 1) % len(phases)
            continue

        ratio = random.uniform(0.3, 0.8)
        duration_ms = random.randint(220, 650)
        if verbose:
            log_device(device_name, f"scroll {direction} ratio={ratio:.2f} dur={duration_ms}ms")
        scroll_page(device_name, duration_ms=duration_ms, scroll_ratio=ratio, direction=direction)

        # coba klik accept cookie selama proses scroll
        try_click_accept_cookies(device_name, timeout=1, verbose=verbose)

        # update phase
        phases[phase_idx] = (direction, remaining - 1)

        # jeda kecil biar natural
        pause = random.uniform(0.4, 1.4)
        remaining_time = total_seconds - (time.time() - start)
        if remaining_time <= 0:
            break
        if stop_event is not None and stop_event.is_set():
            break
        time.sleep(min(pause, remaining_time))

def init_proxy(device, max_attempts=3):
    attempts = 0
    while attempts < max_attempts and (
        wait_until_text_unique(device, "indonesia", timeout=3)
        or wait_until_text_unique(device, "be reached", timeout=1)
    ):
        if STOP_ALL_EVENT.is_set():
            return False
        log_device(device, "IP Indonesia")
        set_random_proxy(device)
        open_chrome_and_visit(device)
        log_device(device, "IP Luar Negeri")
        attempts += 1

    if attempts >= max_attempts and (
        wait_until_text_unique(device, "indonesia", timeout=2)
        or wait_until_text_unique(device, "be reached", timeout=1)
    ):
        if STOP_ALL_EVENT.is_set():
            return False
        log_device(device, "proxy gagal 3x, restart")
        go_home(device)
        return False
    return True

def clear_chrome_data_and_open(device_name, url="https://whoer.net"):
    # Wipes Chrome data then opens URL. UI onboarding may appear afterward.
    run_adb(device_name, "shell pm clear com.android.chrome")
    time.sleep(0.5)
    run_adb(
        device_name,
        "shell am start -a android.intent.action.VIEW "
        f"-d {url} "
        "-n com.android.chrome/com.google.android.apps.chrome.Main"
    )
    disable_auto_rotation(device_name)
    wait_until_text(device_name, "use without")
    tap(device_name, 518, 1558)
    if not wait_until_text(device_name, "my ip"):
        return False
    else:
        return True

def wait_until_text_top_crop(device_name, keyword, height=300, save_path=None):
    if STOP_ALL_EVENT.is_set():
        return False
    if not save_path:
        safe_name = device_name.replace(":", "_")
        save_path = f"debug/screen_{safe_name}.png"
    save_path = debug_path(save_path)
    run_adb(device_name, "shell screencap -p /sdcard/screen.png")
    run_adb(device_name, f'pull /sdcard/screen.png "{save_path}"')
    img = cv2.imread(save_path)
    if img is None:
        return False
    h = img.shape[0]
    crop_h = min(max(int(height), 0), h)
    cropped = img[crop_h:h, :]
    cv2.imwrite(save_path, cropped)
    if find_text_on_screen(save_path, keyword):
        log_device(device_name, f"{keyword} found")
        return True
    else:
        log_device(device_name, f"{keyword} not found")
        return False


def click_filter_option(device_name, label, index=0, wait_timeout=3):
    if not wait_until_text(device_name, label, timeout=wait_timeout):
        return None
    # Reuse the last screenshot from wait_until_text to avoid missing the dialog
    return click_by_text(device_name, label, index=index, refresh=False)

def click_by_text(device_name, keyword, index=0, refresh=True):
    safe_name = device_name.replace(":", "_")
    image_path = debug_path(f"screen_{safe_name}.png")
    if refresh:
        screenshot_emulator(device_name, image_path)
    pos = find_text_position(image_path, keyword, index=index)
    if pos:
        tap(device_name, *pos)
    return pos

def find_text_positions(image_path, keyword):
    from pytesseract import image_to_data
    import cv2

    img = cv2.imread(image_path)
    data = pytesseract.image_to_data(img, lang='eng', output_type=pytesseract.Output.DICT)
    tokens = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text:
            continue
        left = data["left"][i]
        top = data["top"][i]
        right = left + data["width"][i]
        bottom = top + data["height"][i]
        tokens.append({
            "text": text.lower(),
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        })

    kw = keyword.strip().lower()
    if not kw:
        return []

    kw_tokens = [t for t in kw.split() if t]
    if not kw_tokens:
        return []

    matches = []
    if len(kw_tokens) == 1:
        for tok in tokens:
            if kw in tok["text"]:
                center_x = (tok["left"] + tok["right"]) // 2
                center_y = (tok["top"] + tok["bottom"]) // 2
                matches.append((center_x, center_y))
    else:
        n = len(kw_tokens)
        for i in range(len(tokens) - n + 1):
            ok = True
            for j, kw_tok in enumerate(kw_tokens):
                if kw_tok not in tokens[i + j]["text"]:
                    ok = False
                    break
            if not ok:
                continue
            left = min(t["left"] for t in tokens[i:i + n])
            right = max(t["right"] for t in tokens[i:i + n])
            top = min(t["top"] for t in tokens[i:i + n])
            bottom = max(t["bottom"] for t in tokens[i:i + n])
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            matches.append((center_x, center_y))

    return matches

def click_all_by_text(device_name, keyword, delay=0.2, max_clicks=None, order="top_down"):
    safe_name = device_name.replace(":", "_")
    image_path = debug_path(f"screen_{safe_name}.png")
    screenshot_emulator(device_name, image_path)
    positions = find_text_positions(image_path, keyword)

    if order == "bottom_up":
        positions.sort(key=lambda p: (p[1], p[0]), reverse=True)
    else:
        positions.sort(key=lambda p: (p[1], p[0]))

    if max_clicks is not None:
        positions = positions[:max_clicks]

    for x, y in positions:
        tap(device_name, x, y)
        if delay:
            time.sleep(delay)

    return len(positions)

def click_all_by_text_fallback(device_name, keyword, delay=0.2, max_clicks=None, order="top_down"):
    safe_name = device_name.replace(":", "_")
    image_path = debug_path(f"screen_{safe_name}.png")
    screenshot_emulator(device_name, image_path)

    positions = find_text_positions(image_path, keyword)
    if not positions:
        positions = find_text_positions_preprocessed(image_path, keyword, invert=True)

    if order == "bottom_up":
        positions.sort(key=lambda p: (p[1], p[0]), reverse=True)
    else:
        positions.sort(key=lambda p: (p[1], p[0]))

    if max_clicks is not None:
        positions = positions[:max_clicks]

    for x, y in positions:
        tap(device_name, x, y)
        if delay:
            time.sleep(delay)

    return len(positions)

def open_chrome_and_visit(device_name, url="https://whoer.net"):
    run_adb(
        device_name,
        "shell am start -a android.intent.action.VIEW "
        f"-d {url} "
        "--activity-clear-top --activity-single-top "
        "-n com.android.chrome/com.google.android.apps.chrome.Main"
    )
    disable_auto_rotation(device_name)
    tap(device_name, 518, 1558)
    wait_until_text(device_name, "my ip")

def reset_apps(device_name):
    run_adb(device_name, "shell am force-stop com.android.chrome")
    run_adb(device_name, "shell pm clear com.android.chrome")
    run_adb(device_name, "shell am force-stop com.google.android.youtube")
    run_adb(device_name, "shell pm clear com.google.android.youtube")

def run_device_loop(device, stop_event):
    while not stop_event.is_set() and not STOP_ALL_EVENT.is_set():
        if STOP_ALL_EVENT.is_set():
            return
        disable_auto_rotation(device)
        log_step(device, "reset proxy")
        reset_proxy(device)
        log_step(device, "set proxy")
        set_random_proxy(device)
        log_step(device, "clear chrome & open whoer")
        if not clear_chrome_data_and_open(device):
            continue

        log_step(device, "cek IP/proxy")
        if not init_proxy(device, max_attempts=3):
            return

        log_step(device, "buka YouTube")
        open_youtube_app(device)

        # # # cek robot popup
        log_step(device, "cek bot popup")
        if wait_until_text(device, 'not a bot', timeout=3):
            log_device(device, "bot terdeteksi")
            continue

        log_step(device, "tunggu home")
        if not wait_until_text(device, "home"):
            continue
        log_step(device, "buka search")
        if wait_until_text(device, "try searching", timeout=5):
            click_by_text(device, "search")
        else:
            tap(device, 1017, 124)
        wait_until_text(device, "search")
        tap(device, 272, 128)
        log_step(device, "ambil judul")
        judul, idx, total = get_next_title(device)
        update_device_status(device, judul=judul, progress=f"{idx}/{total}")
        # judul = "Joy shines bright Yanis Charles"
        safe = " ".join(judul.split()[:2])
        log_step(device, "input judul")
        input_text(device, judul)
        run_adb(device, "shell input keyevent 66")

        # delay sebelum filter muncul, sekalian nunggu biar bot popup muncul dulu kalau ada
        time.sleep(30)

        # cek robot popup
        log_step(device, "cek bot popup")
        if wait_until_text(device, 'not a bot', timeout=3):
            continue

        # #filter
        log_step(device, "buka filter")
        tap(device, 1014, 134)

        filter = True
        if not wait_until_text(device, "search filters"):
            press_back(device)
            filter = False

        if not filter:
            tap(device, 1014, 134)
            
        time.sleep(0.5)
        tap(device, 1014, 134)
        wait_until_text(device, "search filters")
        time.sleep(0.5)
        log_step(device, "apply filter")
        click_filter_option(device, "relevance", index=0)
        if not click_filter_option(device, "popularity", index=0, wait_timeout=1):
            tap(device, 521, 995)
            # debug_click_position(f"screen_{device}.png")
        time.sleep(0.5)
        tap(device, 915, 1548)

        # delay setelah filter
        time.sleep(10)

        # cek robot popup
        log_step(device, "cek bot popup")
        if wait_until_text(device, 'not a bot', timeout=3):
            continue

        search_timeout = 0
        video_status = True
        log_step(device, "cari video")
        # while (not wait_until_text_unique(device, "check your network") and
        #     not wait_until_text_unique(device, "retry")) and \
        #     not wait_until_text_top_crop(device, safe):
            # search_timeout += 1
            # # scroll_page(device)
            # if search_timeout == 3:
            #     # set_random_proxy(device)
            #     # init_proxy(device)
            #     # open_youtube_app(device)
            #     # search_timeout = 0
            #     video_status = False
            #     break
        while not wait_until_text_top_crop(device, safe):
            search_timeout += 1
            if search_timeout == 3:
                video_status = False
                break

        if not video_status:
            continue

        if wait_until_text_top_crop(device, safe):
            log_device(device, "sukses")
            log_device(device, f"count '{safe}': {count_text_matches(f'debug/screen_{device}.png', safe)}")

            # first video
            log_step(device, "klik video")
            if click_by_text(device, safe, index=1):
                increment_device_counter(device, "song_success", 1)
        else:
            continue

        # skip iklan di awal
        log_step(device, "tunggu tombol skip")
        skip_timeout = 0
        skip = True
        while not wait_until_text(device, "skip"):
            time.sleep(1)
            skip_timeout += 1
            if skip_timeout == 2:
                skip = False
                break

        if not skip:
            log_device(device, "skip not found")
        else:
            log_step(device, "klik skip")
            if not click_by_text(device, "skip"):
                log_device(device, "fallback click")
                tap(device, 972, 483)

        # nonton dengan RANGE_DURASI_NONTON
        log_step(device, "nonton video")
        sleep_with_stop(random_delay(RANGE_DURASI_NONTON), stop_event)

        # scroll cari sponsored 2 page
        timeout_iklan = 0
        status_iklan = True
        log_step(device, "cari sponsored")
        while not wait_until_text(device, "sponsored", timeout=1):
            scroll_page(device, scroll_ratio=0.3)
            timeout_iklan += 1
            if timeout_iklan == 3:
                status_iklan = False
                break
        
        if not status_iklan:
            log_device(device, "iklan tidak ditemukan")
            continue
        
        # klik iklan
        log_step(device, "klik sponsored")
        if click_by_text(device, "sponsored"):
            increment_device_counter(device, "ads_visited", 1)
        log_step(device, "tunggu iklan")
        sleep_with_stop(10, stop_event)

        # skip iklan jika play store
        if is_play_store_open(device):
            log_device(device, "play store terdeteksi, balik home")
            go_home(device)
            continue

        # aksi didalam iklan (dengan RANGE_DURASI_IKLAN)
        log_step(device, "cari & klik accept")
        try_click_accept_cookies(device, timeout=2, verbose=VERBOSE)

        dur_iklan = random_delay(RANGE_DURASI_IKLAN)
        if VERBOSE:
            log_device(device, f"scroll natural {dur_iklan:.2f}s")
        log_step(device, "scroll iklan")
        scroll_natural(device, dur_iklan, verbose=VERBOSE, stop_event=stop_event)

        try_click_accept_cookies(device, timeout=2, verbose=VERBOSE)
        
        # jeda
        log_step(device, "jeda")
        sleep_with_stop(random_delay(RANGE_DURASI_JEDA), stop_event)

        # home and restart (device akan disconnect, lalu muncul lagi)
        log_step(device, "restart device")
        go_home(device)
        return

def device_worker(device, stop_event):
    THREAD_CTX.device = device
    disable_auto_rotation(device)
    log_device(device, "[START]")
    try:
        log_step(device, "reset chrome & youtube")
        reset_apps(device)
        run_device_loop(device, stop_event)
    except Exception as exc:
        log_device(device, f"[ERROR] {exc}")
    log_device(device, "[STOP]")

def print_summary(show_banner=True):
    with RECORD_LOCK:
        devices = RECORD_DATA.get("devices", {})
        summary_items = sorted(devices.items())
    total_song = 0
    total_ads = 0
    table = Table(box=box.SQUARE, show_header=True, header_style="bold")
    table.add_column("device", overflow="ellipsis")
    table.add_column("song_success", justify="right")
    table.add_column("ads_visited", justify="right")

    for dev, data in summary_items:
        song = int(data.get("song_success", 0))
        ads = int(data.get("ads_visited", 0))
        total_song += song
        total_ads += ads
        table.add_row(dev, str(song), str(ads))
    table.add_row("TOTAL", str(total_song), str(total_ads))
    if show_banner and ASCII_BANNER:
        CONSOLE.print(Text(ASCII_BANNER, style="bright_red"))
    CONSOLE.print("\nRingkasan:")
    CONSOLE.print(table)

def _key_listener():
    msvcrt = None
    get_async = None
    try:
        import msvcrt as _msvcrt
        msvcrt = _msvcrt
    except Exception:
        pass
    try:
        import ctypes
        get_async = ctypes.windll.user32.GetAsyncKeyState
    except Exception:
        get_async = None

    while not STOP_ALL_EVENT.is_set():
        pressed = False
        if msvcrt and msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch and ch.lower() == "q":
                pressed = True
        if not pressed and get_async:
            if get_async(0x51) & 0x8000:  # 'Q' key
                pressed = True

        if pressed:
            STOP_ALL_EVENT.set()
            stop_all_devices()
            break
        time.sleep(0.05)

def start_key_listener():
    t = threading.Thread(target=_key_listener, daemon=True)
    t.start()
    return t

def device_manager(poll_interval=5):
    device_threads = {}
    device_seen = {}
    # pastikan auto-rotasi aktif saat awal script
    for dev in get_adb_devices():
        disable_auto_rotation(dev)
    while not STOP_ALL_EVENT.is_set():
        now = time.time()
        current = set(get_adb_devices())

        # update seen timestamps
        for dev in current:
            if dev not in device_seen:
                device_seen[dev] = {"first": now, "last": now, "missing_since": None}
                update_device_status(dev, status="stabilizing")
            else:
                info = device_seen[dev]
                info["last"] = now
                if info["missing_since"] is not None:
                    info["first"] = now
                    info["missing_since"] = None
                    update_device_status(dev, status="reconnected, stabilizing")

        # handle missing devices with grace period
        for dev in list(device_seen.keys()):
            if dev in current:
                continue
            info = device_seen[dev]
            if info["missing_since"] is None:
                info["missing_since"] = now
                update_device_status(dev, status="offline (grace)")

            if now - info["missing_since"] >= DEVICE_MISSING_SECS:
                if dev in device_threads:
                    log_device(dev, "[DISCONNECT]")
                    stop_event = device_threads[dev][1]
                    stop_event.set()
                    device_threads[dev][0].join(timeout=2)
                    del device_threads[dev]
                    unregister_device_event(dev)
                remove_device_status(dev)
                del device_seen[dev]

        # start devices after stable period
        for dev in current:
            info = device_seen.get(dev)
            if info is None:
                continue
            if dev in device_threads and device_threads[dev][0].is_alive():
                continue

            stable_for = now - info["first"]
            if stable_for < DEVICE_STABLE_SECS:
                remaining = int(max(0, DEVICE_STABLE_SECS - stable_for))
                update_device_status(dev, status=f"stabilizing {remaining}s")
                continue

            stop_event = threading.Event()
            t = threading.Thread(target=device_worker, args=(dev, stop_event), daemon=True)
            device_threads[dev] = (t, stop_event)
            register_device_event(dev, stop_event)
            t.start()

        time.sleep(poll_interval)

    # stop all devices on quit
    stop_all_devices()
    for dev, (t, stop_event) in list(device_threads.items()):
        stop_event.set()
        t.join(timeout=2)
        unregister_device_event(dev)
    _stop_live()
    clear_terminal()
    # pastikan auto-rotasi aktif saat akhir script
    for dev in get_adb_devices():
        disable_auto_rotation(dev)
    if ASCII_BANNER:
        CONSOLE.print(Text(ASCII_BANNER, style="bright_red"))
    CONSOLE.print("shutdown all process...")
    print_summary(show_banner=False)
    if sys.stdin.isatty():
        try:
            input("Tekan Enter untuk keluar...")
        except Exception:
            pass

start_key_listener()
clear_terminal()
device_manager()
