import pyautogui
import win32print
import win32ui
import win32con
import win32gui
import win32api
import win32clipboard
import tkinter as tk
import tkinter.messagebox as messagebox
from tkinter import ttk
from PIL import Image
from PIL import ImageWin
from PIL import ImageDraw
from PIL import ImageFont
from datetime import datetime, timedelta
import keyboard
import threading
import ctypes
from ctypes import wintypes
import json
import os
import winreg
import io
import subprocess
import sys
from screeninfo import get_monitors
from pystray import Icon, MenuItem, Menu
import logging
import traceback
from pathlib import Path

# ------------------------------------------------------------
# ScreenPrintTool v1.3.5 (public edition)
# スクリーンショット取得・印刷・保存に特化した公開版
# ------------------------------------------------------------

shortcut_label_var = None
printer_var = None
save_mode_var = None
display_var = None
orientation_mode_var = None
retention_var = None

root = None
settings_window = None
tray_icon = None
global_devmode = None

# DPIスケーリングの影響を減らす
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class AppConfig:
    def __init__(self, path):
        self.path = path
        self.shortcut_key = "print_screen"
        try:
            self.printer_name = win32print.GetDefaultPrinter()
        except Exception:
            self.printer_name = ""
        self.save_mode = "print_only"
        self.display_id = "DISPLAY1"
        self.orientation_mode = "auto"   # auto / portrait / landscape
        self.retention_days = 10
        self.snipping_opt_out = False
        self.open_app = False
        self.launch_app = "paint"        # paint / photos
        self.exclude_taskbar = False
        self.watermark = {
            "date": True,
            "time": True,
            "username": True,
        }
        self.popup_enabled = True
        self.image_quality = "standard"  # low / standard / high

    def load_or_create(self):
        if not os.path.exists(self.path):
            self.save()

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.shortcut_key = data.get("shortcut_key", self.shortcut_key)
            self.printer_name = data.get("printer_name", self.printer_name)
            self.display_id = data.get("display_id", self.display_id)
            if isinstance(self.display_id, int):
                self.display_id = f"DISPLAY{self.display_id}"

            self.save_mode = data.get("save_mode", self.save_mode)

            self.orientation_mode = data.get("orientation_mode")
            if self.orientation_mode is None:
                # 旧版互換
                self.orientation_mode = data.get("orientation", "auto")
            if self.orientation_mode not in ("auto", "portrait", "landscape"):
                self.orientation_mode = "auto"

            self.retention_days = data.get("retention_days", self.retention_days)
            self.snipping_opt_out = data.get("snipping_opt_out", self.snipping_opt_out)
            self.open_app = data.get("open_app", self.open_app)
            self.launch_app = data.get("launch_app", self.launch_app)
            self.exclude_taskbar = data.get("exclude_taskbar", self.exclude_taskbar)
            self.watermark = data.get("watermark", self.watermark)
            self.popup_enabled = data.get("popup_enabled", self.popup_enabled)
            self.image_quality = data.get("image_quality", self.image_quality)

        except Exception as e:
            messagebox.showerror("エラー", f"設定ファイルの読み込みに失敗しました。\n{e}")

    def save(self):
        data = {
            "shortcut_key": self.shortcut_key,
            "printer_name": self.printer_name,
            "display_id": self.display_id,
            "save_mode": self.save_mode,
            "orientation_mode": self.orientation_mode,
            "retention_days": self.retention_days,
            "snipping_opt_out": self.snipping_opt_out,
            "open_app": self.open_app,
            "launch_app": self.launch_app,
            "exclude_taskbar": self.exclude_taskbar,
            "watermark": self.watermark,
            "popup_enabled": self.popup_enabled,
            "image_quality": self.image_quality,
        }

        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("エラー", f"設定ファイルの保存に失敗しました。\n{e}")


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", wintypes.SHORT),
        ("dmPaperSize", wintypes.SHORT),
    ]


CONFIG_PATH = os.path.join(os.environ["LOCALAPPDATA"], "ScreenPrintTool", "config.json")
SAVE_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "ScreenPrintTool")

VALID_SHORTCUT_KEYS = {
    "print_screen",
    "ctrl+print_screen",
    "shift+print_screen",
    "ctrl+alt+p",
    "ctrl+shift+p",
    "alt+f12",
    "ctrl+shift+f12",
    "ctrl+alt+insert",
}


def get_physical_resolution(display_index):
    device_name = f"\\\\.\\DISPLAY{display_index + 1}"
    devmode = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
    return devmode.PelsWidth, devmode.PelsHeight


def get_display_position(index):
    try:
        device_name = f"\\\\.\\DISPLAY{index + 1}"
        devmode = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
        return devmode.Position_x, devmode.Position_y
    except Exception as e:
        logging.warning(f"[位置取得失敗] DISPLAY{index + 1}: {e}")
        return 0, 0


def capture_screen():
    try:
        monitors = get_monitors()
        display_id = config.display_id
        index = int(display_id.replace("DISPLAY", "")) - 1

        if index < 0 or index >= len(monitors):
            raise ValueError(f"ディスプレイ番号 {config.display_id} は存在しません。")

        left, top = get_display_position(index)
        width, height = get_physical_resolution(index)

        if config.exclude_taskbar:
            try:
                monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((left, top)))
                rc_monitor = monitor_info["Monitor"]
                rc_work = monitor_info["Work"]

                monitor_bottom = rc_monitor[3]
                work_bottom = rc_work[3]
                taskbar_height = max(0, monitor_bottom - work_bottom)

                if taskbar_height > 0:
                    height -= taskbar_height
                    logging.info(f"[タスクバー除外] 高さから {taskbar_height}px を差し引き")
            except Exception as e:
                logging.warning(f"[タスクバー除外処理失敗] {e}")

        hdesktop = win32gui.GetDesktopWindow()
        desktop_dc = win32gui.GetWindowDC(hdesktop)
        srcdc = win32ui.CreateDCFromHandle(desktop_dc)

        memdc = srcdc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(srcdc, width, height)
        memdc.SelectObject(bmp)

        memdc.BitBlt((0, 0), (width, height), srcdc, (left, top), win32con.SRCCOPY)

        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        )

        win32gui.DeleteObject(bmp.GetHandle())
        memdc.DeleteDC()
        srcdc.DeleteDC()
        win32gui.ReleaseDC(hdesktop, desktop_dc)

        logging.info(f"[BitBlt] 解像度: {width}x{height} @ ({left},{top})")
        return img

    except Exception as e:
        messagebox.showerror("エラー", f"BitBltによるキャプチャに失敗しました。\n{e}")
        return None


def adjust_image_quality(img):
    if img is None:
        return None

    quality = getattr(config, "image_quality", "standard")
    if quality == "low":
        return img.resize((max(1, img.width // 2), max(1, img.height // 2)), Image.LANCZOS)
    if quality == "high":
        return img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    return img


clipboard_lock = threading.Lock()


def copy_to_clipboard(image: Image.Image):
    output = io.BytesIO()
    image.convert("RGB").save(output, "BMP")
    data = output.getvalue()[14:]
    output.close()

    with clipboard_lock:
        for attempt in range(3):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                return
            except Exception:
                logging.warning(f"[警告] クリップボード使用中。{attempt + 1}回目リトライ中...")
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass

        logging.error("[エラー] クリップボードにコピーできませんでした。")


def init_devmode(printer_name):
    global global_devmode

    try:
        hprinter = win32print.OpenPrinter(printer_name)
        try:
            raw_handle = int(hprinter)

            dll = ctypes.windll.LoadLibrary("winspool.drv")
            document_properties = dll.DocumentPropertiesW
            document_properties.argtypes = [
                wintypes.HWND,
                wintypes.HANDLE,
                wintypes.LPWSTR,
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            document_properties.restype = wintypes.LONG

            needed = document_properties(0, raw_handle, printer_name, None, None, 0)
            if needed <= 0:
                raise RuntimeError("DEVMODE サイズ取得に失敗")

            buf = ctypes.create_string_buffer(needed)
            res = document_properties(
                0,
                raw_handle,
                printer_name,
                buf,
                None,
                win32con.DM_OUT_BUFFER,
            )
            if res != 1:
                raise RuntimeError("DocumentPropertiesW(DM_OUT_BUFFER) failed")

            global_devmode = buf
            logging.info(f"[DEVMODE取得] {printer_name} の DEVMODE を初期化しました")

        finally:
            win32print.ClosePrinter(hprinter)

    except Exception as e:
        logging.error(f"[DEVMODE取得失敗] {printer_name}: {e}")
        global_devmode = None


def cleanup_old_images():
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        threshold_date = (datetime.now() - timedelta(days=config.retention_days)).date()

        for file in os.listdir(SAVE_DIR):
            if file.startswith("screenshot_") and file.endswith(".png"):
                try:
                    date_part = file.split("_")[1]
                    file_date = datetime.strptime(date_part, "%Y-%m-%d").date()
                    if file_date < threshold_date:
                        os.remove(os.path.join(SAVE_DIR, file))
                except Exception as e:
                    logging.warning(f"[削除失敗] {file} → {e}")
    except Exception as e:
        logging.error(f"[削除処理エラー] {e}")


def resolve_target_printer():
    try:
        default = win32print.GetDefaultPrinter()
        if default:
            return default
    except Exception:
        pass

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    try:
        names = [p[2] for p in win32print.EnumPrinters(flags)]
    except Exception:
        names = []

    for name in names:
        low = name.lower()
        if ("microsoft" in low) and ("pdf" in low):
            return name

    return None


def generate_filename():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return os.path.join(SAVE_DIR, f"screenshot_{timestamp}.png")


def open_paint_direct(file_path):
    subprocess.Popen(["mspaint.exe", file_path])


def handle_capture():
    try:
        key_path = r"Control Panel\Keyboard"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "PrintScreenKeyForSnippingEnabled")
            snipping_enabled = (value == 1)
    except Exception:
        snipping_enabled = False

    if config.shortcut_key == "print_screen" and snipping_enabled:
        return

    config.save()
    img = capture_screen()
    img = adjust_image_quality(img)
    saved_path = None

    if img:
        copy_to_clipboard(img)

        if config.save_mode in ("save_only", "print_and_save"):
            os.makedirs(SAVE_DIR, exist_ok=True)
            saved_path = generate_filename()
            img.save(saved_path)

        if config.save_mode in ("print_only", "print_and_save"):
            print_image(img)

    if saved_path and config.open_app:
        if config.launch_app == "paint":
            open_paint_direct(saved_path)
        elif config.launch_app == "photos":
            os.startfile(saved_path)

    return saved_path


def prepare_image_for_print(image, width, height):
    img_ratio = image.width / image.height
    target_ratio = width / height

    if img_ratio > target_ratio:
        new_width = width
        new_height = int(width / img_ratio)
    else:
        new_height = height
        new_width = int(height * img_ratio)

    resized = image.resize((new_width, new_height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), "white")
    offset = ((width - new_width) // 2, (height - new_height) // 2)
    canvas.paste(resized, offset)
    return canvas, offset, resized


def get_user_display_name():
    try:
        import win32net
        import win32com.client

        info = win32com.client.Dispatch("WScript.Network")
        user = info.UserName
        domain = info.UserDomain
        user_info = win32net.NetUserGetInfo(domain, user, 2)
        return user_info.get("full_name", "")
    except Exception:
        return ""


def add_watermark(image, offset, resized_img, rotated=False):
    now = datetime.now()

    # 公開版では庁内向け色を少し弱めた文言に調整
    left_notice = "ScreenPrintTool"

    meta_parts = []
    if config.watermark.get("date", True) or config.watermark.get("time", True):
        dt = []
        if config.watermark.get("date", True):
            dt.append(now.strftime("%Y/%m/%d"))
        if config.watermark.get("time", True):
            dt.append(now.strftime("%H:%M:%S"))
        meta_parts.append("取得日時：" + " ".join(dt))

    if config.watermark.get("username", True):
        try:
            user_id = os.getlogin()
        except Exception:
            user_id = os.environ.get("USERNAME", "user")

        display_name = get_user_display_name()
        if display_name:
            meta_parts.append(f"実行者：{display_name}（{user_id}）")
        else:
            meta_parts.append(f"実行者：{user_id}")

    right_meta = "　".join(meta_parts) if meta_parts else ""

    if not left_notice and not right_meta:
        return image

    canvas = image.convert("RGBA")
    width, _ = canvas.size

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msgothic.ttc", 32)
    except Exception:
        font = ImageFont.load_default()

    pad_x = 16
    pad_y = 10

    def text_size(text):
        if not text:
            return (0, 0)
        draw = ImageDraw.Draw(canvas)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return (bbox[2] - bbox[0], bbox[3] - bbox[1])
        except AttributeError:
            return draw.textsize(text, font=font)

    _, lh = text_size(left_notice)
    rw, rh = text_size(right_meta)
    band_h = max(lh, rh) + pad_y * 2

    overlay = Image.new("RGBA", (width, band_h), (255, 255, 255, 220))
    draw = ImageDraw.Draw(overlay)

    if left_notice:
        draw.text((pad_x, pad_y), left_notice, font=font, fill=(0, 0, 0, 255))

    if right_meta:
        draw.text((width - pad_x - rw, pad_y), right_meta, font=font, fill=(0, 0, 0, 255))

    canvas.alpha_composite(overlay, (0, 0))
    return canvas.convert("RGB")


def print_image(image: Image.Image):
    global global_devmode

    try:
        use_printer = None
        try:
            use_printer = config.printer_name or None
            if use_printer:
                test_handle = win32print.OpenPrinter(use_printer)
                win32print.ClosePrinter(test_handle)
        except Exception:
            use_printer = None

        if not use_printer:
            use_printer = resolve_target_printer()
            if not use_printer:
                raise RuntimeError("印刷先が見つかりません。（既定なし／Microsoft Print to PDFも未検出）")

            init_devmode(use_printer)
            config.printer_name = use_printer
            config.save()

        orientation_mode = config.orientation_mode
        if orientation_mode == "auto":
            orientation = "landscape" if image.width > image.height else "portrait"
        else:
            orientation = orientation_mode

        logging.info(f"[印刷開始] printer={use_printer}, orientation_mode={config.orientation_mode}")
        logging.info(f"[元画像サイズ] image.width={image.width}, image.height={image.height}")

        show_printing_popup()

        rotated = False
        if orientation == "portrait":
            dev_orientation = 1
            width, height = 2480, 3508
            if image.width > image.height:
                image = image.rotate(270, expand=True)
                rotated = True
        else:
            dev_orientation = 2
            width, height = 3508, 2480

        logging.info(f"[印刷用サイズ] width={width}, height={height}")

        processed_image, offset, resized_img = prepare_image_for_print(image, width, height)
        processed_image = add_watermark(processed_image.convert("RGB").copy(), offset, resized_img, rotated)

        if global_devmode is None:
            raise RuntimeError("global_devmode が初期化されていません")

        devmode_buf = ctypes.create_string_buffer(len(global_devmode))
        ctypes.memmove(devmode_buf, global_devmode, len(global_devmode))

        devmode_obj = DEVMODE.from_buffer(devmode_buf)
        devmode_obj.dmOrientation = dev_orientation
        devmode_obj.dmFields |= win32con.DM_ORIENTATION

        create_dcw = ctypes.windll.gdi32.CreateDCW
        create_dcw.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
        create_dcw.restype = wintypes.HDC

        hdc = create_dcw(use_printer, use_printer, None, devmode_buf)
        if not hdc:
            raise RuntimeError("CreateDCW failed")

        dc = win32ui.CreateDCFromHandle(hdc)

        printable_width = dc.GetDeviceCaps(win32con.HORZRES)
        printable_height = dc.GetDeviceCaps(win32con.VERTRES)
        logging.info(f"[プリンタ描画可能範囲] printable_width={printable_width}, printable_height={printable_height}")

        if orientation == "landscape" and printable_width < printable_height:
            printable_width, printable_height = printable_height, printable_width

        dc.StartDoc("ScreenPrint")
        dc.StartPage()
        dib = ImageWin.Dib(processed_image)
        dib.draw(dc.GetHandleOutput(), (0, 0, printable_width, printable_height))
        dc.EndPage()
        dc.EndDoc()
        dc.DeleteDC()

    except Exception as e:
        messagebox.showerror("エラー", f"印刷に失敗しました。\n{e}\n\n{traceback.format_exc()}")


def parse_shortcut_expression(expr: str):
    parts = [p.strip().lower() for p in expr.split("+") if p.strip()]
    mods = set()
    main = None

    for p in parts:
        if p in ("ctrl", "shift", "alt"):
            mods.add(p)
        else:
            main = p

    return mods, main


def event_matches_hotkey(event, shortcut_expr: str) -> bool:
    if getattr(event, "event_type", "") != "down":
        return False

    mods, main = parse_shortcut_expression(shortcut_expr)
    name = (event.name or "").lower()

    if main == "print_screen":
        if name not in ("print screen", "print_screen", "print-screen", "prtsc", "printscrn"):
            return False
    elif main:
        if name != main:
            return False

    for mod in mods:
        if not keyboard.is_pressed(mod):
            return False

    return True


def start_hotkey_listener():
    logging.info("[ショートカットキー監視] フック方式で開始")

    def on_key_event(event):
        try:
            shortcut_expr = config.shortcut_key
            if not shortcut_expr:
                return

            if event_matches_hotkey(event, shortcut_expr):
                logging.info(f"[キー検知] {shortcut_expr} → handle_capture 実行")

                if settings_window is not None and settings_window.winfo_exists():
                    try:
                        settings_window.update()
                        update_config_from_gui()
                    except Exception as e:
                        logging.warning(f"[GUI反映失敗] {e}")

                handle_capture()

        except Exception as e:
            logging.warning(f"[ホットキー処理エラー] {e}")

    keyboard.hook(on_key_event)


def prevent_multiple_instances():
    mutex_name = "screenprinttool_single_instance"
    ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
    if ctypes.GetLastError() == 183:
        messagebox.showwarning("警告", "このアプリはすでに起動しています。")
        sys.exit(0)


def show_printing_popup():
    if not config.popup_enabled or root is None:
        return

    try:
        popup = tk.Toplevel(root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)

        width, height = 220, 70
        bg_color = "#F0F0F0"
        fg_color = "black"
        border_color = "#666666"
        border_width = 3

        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2

        frame = tk.Frame(
            popup,
            bg=bg_color,
            bd=border_width,
            relief="solid",
            highlightbackground=border_color,
            highlightthickness=border_width,
        )
        frame.pack(fill="both", expand=True)

        label = tk.Label(
            frame,
            text="印刷中です…",
            font=("MS UI Gothic", 16, "bold"),
            bg=bg_color,
            fg=fg_color,
        )
        label.place(relx=0.5, rely=0.5, anchor="center")

        popup.geometry(f"{width}x{height}+{x}+{y}")
        popup.after(700, popup.destroy)
        popup.update()

    except Exception as e:
        logging.warning(f"[Popup error] {e}")


config = AppConfig(CONFIG_PATH)


def on_restart(icon, item):
    icon.stop()
    os.execl(sys.executable, sys.executable, *sys.argv)


def setup_tray_icon():
    global tray_icon

    def on_open_settings(icon, item):
        show_settings_window()

    def open_save_folder(icon, item):
        subprocess.Popen(f'explorer "{SAVE_DIR}"')

    def on_show_version(icon, item):
        messagebox.showinfo("バージョン情報", "ScreenPrintTool v1.3.5")

    def on_exit(icon, item):
        icon.stop()
        os._exit(0)

    try:
        icon_image = Image.open(os.path.join(BASE_DIR, "icon.png"))
    except Exception:
        icon_image = Image.new("RGB", (64, 64), "gray")

    menu = Menu(
        MenuItem("基本設定", on_open_settings),
        MenuItem("画像の保存先を開く", open_save_folder),
        MenuItem("バージョン情報", on_show_version),
        MenuItem("再起動（設定再取得）", on_restart),
        MenuItem("アプリ終了", on_exit),
    )

    tray_icon = Icon("ScreenPrintTool", icon_image, "ScreenPrintTool", menu)
    tray_icon.run()


shortcut_options = {
    "Print Screen（デフォルト）": "print_screen",
    "Ctrl + Print Screen": "ctrl+print_screen",
    "Shift + Print Screen": "shift+print_screen",
    "Ctrl + Alt + P": "ctrl+alt+p",
    "Ctrl + Shift + P": "ctrl+shift+p",
    "Alt + F12": "alt+f12",
    "Ctrl + Shift + F12": "ctrl+shift+f12",
    "Ctrl + Alt + Insert": "ctrl+alt+insert",
}
reverse_options = {v: k for k, v in shortcut_options.items()}


def show_settings_window():
    global settings_window, root
    global shortcut_label_var, printer_var, save_mode_var, display_var, orientation_mode_var, retention_var

    if settings_window is not None and settings_window.winfo_exists():
        settings_window.lift()
        settings_window.attributes("-topmost", True)
        settings_window.attributes("-topmost", False)
        return

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    base_w, base_h = int(sw * 0.3), int(sh * 0.6)

    win = tk.Toplevel(root)
    settings_window = win

    canvas = tk.Canvas(win)
    scrollbar = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    try:
        icon_path = os.path.join(BASE_DIR, "icon.png")
        win.iconphoto(False, tk.PhotoImage(file=icon_path))
    except Exception:
        pass

    win.title("ScreenPrintTool 設定")
    win.geometry(f"{base_w}x{base_h}")
    win.resizable(True, True)

    printer_var = tk.StringVar(value=config.printer_name)
    save_mode_var = tk.StringVar(value=config.save_mode)
    display_var = tk.StringVar(value=str(config.display_id))
    exclude_taskbar_var = tk.BooleanVar(value=config.exclude_taskbar)

    printer_list = [p[2] for p in win32print.EnumPrinters(2)]

    monitor_list = []
    for i in range(10):
        try:
            device = win32api.EnumDisplayDevices(None, i)
            if not device.DeviceName.startswith("\\\\.\\DISPLAY"):
                continue
            if not (device.StateFlags & 1):
                continue
            display_id = device.DeviceName[4:]
            monitor_list.append(display_id)
        except Exception:
            break

    def on_save_mode_change():
        save_mode = save_mode_var.get()
        if save_mode == "print_only":
            open_app_chk.config(state="disabled")
            open_app_var.set(False)
            paint_radio.config(state="disabled")
            photo_radio.config(state="disabled")
        else:
            open_app_chk.config(state="normal")
            state = "normal" if open_app_var.get() else "disabled"
            paint_radio.config(state=state)
            photo_radio.config(state=state)

    shortcut_label_var = tk.StringVar(
        value=reverse_options.get(config.shortcut_key, "Print Screen（デフォルト）")
    )

    tk.Label(scrollable_frame, text="ショートカットキー:").pack(anchor="w", padx=10, pady=2)
    ttk.Combobox(
        scrollable_frame,
        textvariable=shortcut_label_var,
        values=list(shortcut_options.keys()),
        state="readonly",
    ).pack(fill="x", padx=10)

    tk.Label(scrollable_frame, text="プリンター:").pack(anchor="w", padx=10, pady=2)
    ttk.Combobox(
        scrollable_frame,
        textvariable=printer_var,
        values=printer_list,
        state="readonly",
    ).pack(fill="x", padx=10)

    tk.Label(scrollable_frame, text="ディスプレイ番号:").pack(anchor="w", padx=10, pady=2)
    ttk.Combobox(
        scrollable_frame,
        textvariable=display_var,
        values=monitor_list,
        state="readonly",
    ).pack(fill="x", padx=10)

    tk.Label(scrollable_frame, text="保存モード:").pack(anchor="w", padx=10, pady=2)
    for text, value in {
        "印刷のみ": "print_only",
        "保存のみ": "save_only",
        "印刷＋保存": "print_and_save",
    }.items():
        ttk.Radiobutton(
            scrollable_frame,
            text=text,
            variable=save_mode_var,
            value=value,
            command=on_save_mode_change,
        ).pack(anchor="w", padx=20)

    def on_exclude_taskbar_toggle():
        config.exclude_taskbar = exclude_taskbar_var.get()
        config.save()

    snipping_opt_out_var = tk.BooleanVar(value=config.snipping_opt_out)

    def on_snipping_toggle():
        config.snipping_opt_out = snipping_opt_out_var.get()
        config.save()

        try:
            key_path = r"Control Panel\\Keyboard"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                value = 1 if config.snipping_opt_out else 0
                winreg.SetValueEx(key, "PrintScreenKeyForSnippingEnabled", 0, winreg.REG_DWORD, value)

            messagebox.showinfo(
                "設定変更",
                "Snipping Tool の起動設定を変更しました。\n"
                "反映されない場合はPCの再起動をお試しください。\n"
                "必要に応じてこのアプリのショートカットキーを変更してください。"
            )
        except Exception as e:
            messagebox.showerror("エラー", f"Snipping Toolの設定変更に失敗しました。\n{e}")

    tk.Checkbutton(
        scrollable_frame,
        text="「Prt Scr」でSnipping Toolを起動する",
        variable=snipping_opt_out_var,
        command=on_snipping_toggle,
    ).pack(anchor="w", padx=20, pady=5)

    open_app_var = tk.BooleanVar(value=config.open_app)
    launch_app_var = tk.StringVar(value=config.launch_app)

    def on_open_app_toggle():
        config.open_app = open_app_var.get()
        config.save()
        state = "normal" if open_app_var.get() else "disabled"
        paint_radio.config(state=state)
        photo_radio.config(state=state)

    def on_launch_app_change():
        config.launch_app = launch_app_var.get()
        config.save()

    open_app_chk = tk.Checkbutton(
        scrollable_frame,
        text="別のアプリからスクリーンショットを開く（印刷のみの場合不可）",
        variable=open_app_var,
        command=on_open_app_toggle,
    )
    open_app_chk.pack(anchor="w", padx=10, pady=(10, 0))

    paint_radio = tk.Radiobutton(
        scrollable_frame,
        text="ペイントアプリ",
        variable=launch_app_var,
        value="paint",
        state="normal" if config.open_app else "disabled",
        command=on_launch_app_change,
    )
    paint_radio.pack(anchor="w", padx=30)

    photo_radio = tk.Radiobutton(
        scrollable_frame,
        text="フォトアプリ",
        variable=launch_app_var,
        value="photos",
        state="normal" if config.open_app else "disabled",
        command=on_launch_app_change,
    )
    photo_radio.pack(anchor="w", padx=30)

    on_save_mode_change()

    tk.Checkbutton(
        scrollable_frame,
        text="タスクバー（下部）を除外する　※検証中",
        variable=exclude_taskbar_var,
        command=on_exclude_taskbar_toggle,
    ).pack(anchor="w", padx=20, pady=5)

    tk.Label(scrollable_frame, text="画像保存日数（古い画像の自動削除）").pack(anchor="w", padx=10, pady=2)
    retention_var = tk.IntVar(value=config.retention_days)
    tk.Spinbox(scrollable_frame, from_=1, to=90, textvariable=retention_var).pack(fill="x", padx=10)

    def on_test():
        config.shortcut_key = shortcut_options.get(shortcut_label_var.get(), "print_screen")
        config.printer_name = printer_var.get()
        config.save_mode = save_mode_var.get()
        config.display_id = display_var.get()
        config.orientation_mode = orientation_mode_var.get()
        config.retention_days = retention_var.get()
        config.save()
        init_devmode(config.printer_name)
        handle_capture()

    def on_save():
        config.shortcut_key = shortcut_options.get(shortcut_label_var.get(), "print_screen")
        config.printer_name = printer_var.get()
        config.save_mode = save_mode_var.get()
        config.display_id = display_var.get()
        config.orientation_mode = orientation_mode_var.get()
        config.retention_days = retention_var.get()
        config.save()
        init_devmode(config.printer_name)
        messagebox.showinfo("保存", "設定を保存しました。")

    frame = tk.Frame(scrollable_frame)
    frame.pack(pady=10)
    ttk.Button(frame, text=" テスト印刷", command=on_test).pack(side="left", padx=10)
    ttk.Button(frame, text=" 保存", command=on_save).pack(side="left", padx=10)

    details_frame = tk.Frame(scrollable_frame, borderwidth=1, relief="groove")

    def toggle_details():
        if details_frame.winfo_ismapped():
            details_frame.pack_forget()
            toggle_btn.config(text="▶ 詳細設定")
            win.geometry(f"{base_w}x{base_h}")
        else:
            details_frame.pack(anchor="w", fill="x", padx=10, pady=5)
            toggle_btn.config(text="▼ 詳細設定")
            new_w = min(base_w, sw)
            new_h = min(base_h + 200, sh)
            win.geometry(f"{new_w}x{new_h}")

    toggle_btn = tk.Button(
        scrollable_frame,
        text="▶ 詳細設定",
        command=toggle_details,
        relief="raised",
        bg="#e7eaf0",
        fg="#222",
        font=("Yu Gothic UI", 10, "bold"),
        cursor="hand2",
        activebackground="#c6dbf7",
        activeforeground="#004080",
        bd=2,
        highlightthickness=1,
    )
    toggle_btn.pack(anchor="w", padx=10, pady=5)

    orientation_mode_var = tk.StringVar(value=config.orientation_mode)
    tk.Label(details_frame, text="印刷方向モード").pack(anchor="w", padx=10, pady=2)
    ttk.Radiobutton(details_frame, text="自動（推奨）", variable=orientation_mode_var, value="auto").pack(anchor="w", padx=20)
    ttk.Radiobutton(details_frame, text="縦固定", variable=orientation_mode_var, value="portrait").pack(anchor="w", padx=20)
    ttk.Radiobutton(details_frame, text="横固定", variable=orientation_mode_var, value="landscape").pack(anchor="w", padx=20)

    popup_var = tk.BooleanVar(value=config.popup_enabled)

    def on_popup_toggle():
        config.popup_enabled = popup_var.get()
        config.save()

    tk.Checkbutton(
        details_frame,
        text="印刷中ポップアップを表示",
        variable=popup_var,
        command=on_popup_toggle,
    ).pack(anchor="w", padx=20, pady=2)

    watermark_date_var = tk.BooleanVar(value=config.watermark.get("date", True))
    watermark_time_var = tk.BooleanVar(value=config.watermark.get("time", True))
    watermark_user_var = tk.BooleanVar(value=config.watermark.get("username", True))

    def on_watermark_toggle():
        config.watermark = {
            "date": watermark_date_var.get(),
            "time": watermark_time_var.get(),
            "username": watermark_user_var.get(),
        }
        config.save()

    tk.Label(details_frame, text="印刷画像の右上に表示する情報").pack(anchor="w", padx=10, pady=2)
    tk.Checkbutton(details_frame, text="日付", variable=watermark_date_var, command=on_watermark_toggle).pack(anchor="w", padx=30)
    tk.Checkbutton(details_frame, text="時刻", variable=watermark_time_var, command=on_watermark_toggle).pack(anchor="w", padx=30)
    tk.Checkbutton(details_frame, text="ユーザー名", variable=watermark_user_var, command=on_watermark_toggle).pack(anchor="w", padx=30)

    image_quality_var = tk.StringVar(value=getattr(config, "image_quality", "standard"))

    def on_quality_change():
        config.image_quality = image_quality_var.get()
        config.save()

    tk.Label(details_frame, text="画質設定").pack(anchor="w", padx=10, pady=2)
    for text, value in [("低", "low"), ("標準", "standard"), ("高", "high")]:
        ttk.Radiobutton(
            details_frame,
            text=text,
            variable=image_quality_var,
            value=value,
            command=on_quality_change,
        ).pack(anchor="w", padx=30)

    def on_close():
        global settings_window
        settings_window = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)


def update_config_from_gui():
    global shortcut_label_var, printer_var, save_mode_var, display_var, orientation_mode_var, retention_var
    try:
        config.shortcut_key = shortcut_options.get(shortcut_label_var.get(), "print_screen")
        config.printer_name = printer_var.get()
        config.save_mode = save_mode_var.get()
        config.display_id = display_var.get()
        config.orientation_mode = orientation_mode_var.get()
        config.retention_days = retention_var.get()
        config.save()
        init_devmode(config.printer_name)
    except Exception as e:
        logging.warning(f"[GUI設定反映失敗] {e}")


def disable_snipping_tool_if_needed():
    try:
        key_path = r"Control Panel\\Keyboard"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            value = 1 if config.snipping_opt_out else 0
            winreg.SetValueEx(key, "PrintScreenKeyForSnippingEnabled", 0, winreg.REG_DWORD, value)
    except Exception:
        pass


def setup_logging():
    log_dir = Path(os.getenv("APPDATA")) / "ScreenPrintTool" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / datetime.now().strftime("%Y%m%d_%H%M.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )

    logging.info("[起動] ScreenPrintTool v1.3.5 起動開始")

    hdc = win32gui.GetDC(0)
    dpi_x = win32print.GetDeviceCaps(hdc, win32con.LOGPIXELSX)
    dpi_y = win32print.GetDeviceCaps(hdc, win32con.LOGPIXELSY)
    win32gui.ReleaseDC(0, hdc)
    logging.info(f"[DPI設定] dpi_x={dpi_x}, dpi_y={dpi_y}")

    for i, m in enumerate(get_monitors(), 1):
        logging.info(f"[モニター{i}] x={m.x}, y={m.y}, width={m.width}, height={m.height}")


if __name__ == "__main__":
    prevent_multiple_instances()
    setup_logging()
    config.load_or_create()

    if config.shortcut_key not in VALID_SHORTCUT_KEYS:
        logging.warning(
            f"[ショートカットキー異常] 不明な値 {config.shortcut_key!r} を検出したため "
            "Print Screen にリセットします。"
        )
        config.shortcut_key = "print_screen"
        config.save()

    fallback = resolve_target_printer()
    if not config.printer_name and fallback:
        config.printer_name = fallback
        config.save()

    printer_for_devmode = config.printer_name or fallback
    if printer_for_devmode:
        init_devmode(printer_for_devmode)
    else:
        logging.warning(
            "[起動] 利用可能なプリンタが見つからないため、DEVMODE初期化をスキップします。"
            "（保存のみモードは利用可能です）"
        )

    cleanup_old_images()
    disable_snipping_tool_if_needed()

    root = tk.Tk()
    root.withdraw()

    start_hotkey_listener()
    threading.Thread(target=setup_tray_icon, daemon=True).start()

    root.mainloop()