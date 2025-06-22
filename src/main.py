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
from datetime import datetime
import keyboard
import threading
import ctypes
import json
import os
import winreg
import io
import subprocess
import time
import sys
from screeninfo import get_monitors
from pystray import Icon, MenuItem, Menu
from datetime import datetime, timedelta
import logging
from pathlib import Path
import psutil

# ---グローバル変数---
# 設定GUIの変数（グローバルで使えるように）
shortcut_label_var = None
printer_var = None
save_mode_var = None
display_var = None
orientation_var = None
retention_var = None
# 設定ウィンドウの多重起動防止用グローバル変数
settings_window = None
# タスクトレイアイコン保持用（GC回避）
tray_icon = None

# DPIスケーリングの影響を排除（BitBlt整合）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # SYSTEM_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # fallback for Win7など旧環境用
    except:
        pass

# アイコンファイルのパス
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)  # EXE実行時（PyInstaller）
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # スクリプト実行時


class AppConfig:
    def __init__(self, path):
        self.path = path
        self.shortcut_key = "print_screen"
        self.printer_name = win32print.GetDefaultPrinter()
        self.save_mode = "print_only"
        self.display_id = "DISPLAY1"  # 初期値を文字列形式に
        self.orientation = "portrait"
        self.retention_days = 10  # デフォルト：10日
        self.snipping_opt_out = False  # Snipping Toolを無効にしない＝許容（チェックON時）
        self.launch_paint = False
        self.exclude_taskbar = False  # タスクバー（下部）除外

    def load_or_create(self):
        if not os.path.exists(self.path):
            self.save()  # 初回起動時に自動生成
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.shortcut_key = data.get("shortcut_key", self.shortcut_key)
                self.printer_name = data.get("printer_name", self.printer_name)
                self.display_id = data.get("display_id", self.display_id)
                if isinstance(self.display_id, int):
                    self.display_id = f"DISPLAY{self.display_id}"
                self.save_mode = data.get("save_mode", self.save_mode)
                self.orientation = data.get("orientation", self.orientation)
                self.retention_days = data.get("retention_days", self.retention_days)
                self.snipping_opt_out = data.get("snipping_opt_out", self.snipping_opt_out)
                self.launch_paint = data.get("launch_paint", self.launch_paint)
                self.exclude_taskbar = data.get("exclude_taskbar", self.exclude_taskbar)
        except Exception as e:
            messagebox.showerror("エラー", f"設定ファイルの読み込みに失敗しました。\n{e}")

    def save(self):
        data = {
            "shortcut_key": self.shortcut_key,
            "printer_name": self.printer_name,
            "display_id": self.display_id,
            "save_mode": self.save_mode,
            "orientation": self.orientation,
            "retention_days": self.retention_days,
            "snipping_opt_out": self.snipping_opt_out,
            "launch_paint": self.launch_paint,
            "exclude_taskbar": self.exclude_taskbar
        }
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True) 
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            messagebox.showerror("エラー", f"設定ファイルの保存に失敗しました。\n{e}")

# 定数
A4_WIDTH_PX = 2480
A4_HEIGHT_PX = 3508
CONFIG_PATH = os.path.join( os.environ["LOCALAPPDATA"], "ScreenPrintTool", "config.json")
SAVE_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "ScreenPrintTool")

# ディスプレイの物理解像度取得
def get_physical_resolution(display_index):
    """
    WindowsのEnumDisplaySettings()を使って物理解像度を取得する
    display_index: 0ベース（DISPLAY1 → 0, DISPLAY2 → 1）
    """
    device_name = f"\\\\.\\DISPLAY{display_index + 1}"
    devmode = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
    return devmode.PelsWidth, devmode.PelsHeight

# ディスプレイの物理左上座標取得（DPIスケーリング補正用）
def get_display_position(index):
    try:
        device_name = f"\\\\.\\DISPLAY{index + 1}"
        devmode = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
        return devmode.Position_x, devmode.Position_y
    except Exception as e:
        logging.warning(f"[位置取得失敗] DISPLAY{index+1}: {e}")
        return 0, 0

# スクリーンショット取得関数
def capture_screen():
    try:
        monitors = get_monitors()
        display_id = config.display_id  # "DISPLAY1"
        index = int(display_id.replace("DISPLAY", "")) - 1


        if index < 0 or index >= len(monitors):
            raise ValueError(f"ディスプレイ番号 {config.display_id} は存在しません。")

        monitor = monitors[index]
        left, top = get_display_position(index)
        width, height = get_physical_resolution(index)  # ← ここがDPI補正の決め手！

        # キャプチャ範囲補正（下部タスクバーを除外する設定がONなら）
        if config.exclude_taskbar:
            try:
                monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((left, top)))
                rcMonitor = monitor_info["Monitor"]  # (left, top, right, bottom)
                rcWork = monitor_info["Work"]        # 作業領域（タスクバー除く）

                monitor_bottom = rcMonitor[3]
                work_bottom = rcWork[3]
                taskbar_height = max(0, monitor_bottom - work_bottom)

                if taskbar_height > 0:
                    height -= taskbar_height
                    logging.info(f"[タスクバー除外] 高さから {taskbar_height}px を差し引き")
            except Exception as e:
                logging.warning(f"[タスクバー除外処理失敗] {e}")

        # 仮想スクリーン全体のDCを取得
        hdesktop = win32gui.GetDesktopWindow()
        desktop_dc = win32gui.GetWindowDC(hdesktop)
        srcdc = win32ui.CreateDCFromHandle(desktop_dc)

        # メモリDCと互換ビットマップを作成
        memdc = srcdc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(srcdc, width, height)
        memdc.SelectObject(bmp)

        # 指定座標からキャプチャ（BitBlt）
        memdc.BitBlt((0, 0), (width, height), srcdc, (left, top), win32con.SRCCOPY)

        # Pillow画像に変換
        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1)

        # 解放
        win32gui.DeleteObject(bmp.GetHandle())
        memdc.DeleteDC()
        srcdc.DeleteDC()
        win32gui.ReleaseDC(hdesktop, desktop_dc)

        logging.info(f"[BitBlt] 解像度: {width}x{height} @ ({left},{top})")
        return img

    except Exception as e:
        messagebox.showerror("エラー", f"BitBltによるキャプチャに失敗しました。\n{e}")
        return None

# スクリーンショットをクリップボードに常時コピー
clipboard_lock = threading.Lock()

def copy_to_clipboard(image: Image.Image):
    output = io.BytesIO()
    image.convert("RGB").save(output, "BMP")
    data = output.getvalue()[14:]
    output.close()

    with clipboard_lock:
        for attempt in range(3):  # 最大3回までリトライ
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                return
            except Exception as e:
                logging.warning(f"[警告] クリップボード使用中。{attempt+1}回目リトライ中...")
                time.sleep(0.3)  # 少し待って再試行
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except:
                    pass
        logging.error("[エラー] クリップボードにコピーできませんでした。")

# 自動削除処理関数
def cleanup_old_images():
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)

        threshold_date = (datetime.now() - timedelta(days=config.retention_days)).date()

        for file in os.listdir(SAVE_DIR):
            if file.startswith("screenshot_") and file.endswith(".png"):
                try:
                    date_part = file.split("_")[1]  # screenshot_YYYY-MM-DD_...
                    file_date = datetime.strptime(date_part, "%Y-%m-%d").date()

                    if file_date < threshold_date:
                        os.remove(os.path.join(SAVE_DIR, file))
                except Exception as e:
                    logging.warning(f"[削除失敗] {file} → {e}")
    except Exception as e:
        logging.error(f"[削除処理エラー] {e}")

#キャプチャ関数
def handle_capture():
     # Snipping Toolが有効かつPrintScreenキーのときは処理をスキップ
    try:
        key_path = r"Control Panel\Keyboard"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "PrintScreenKeyForSnippingEnabled")
            snipping_enabled = (value == 1)
    except Exception:
        snipping_enabled = False  # 取得できなかった場合は安全側に倒す

    if config.shortcut_key == "print_screen" and snipping_enabled:
        # Snipping Toolが有効でPrintScreenを使っている場合は競合回避のため無効化
        return

    # 通常処理（キャプチャ・保存・印刷）
    config.save()  
    img = capture_screen()
    if img:
        copy_to_clipboard(img)
        if config.save_mode in ("save_only", "print_and_save"):
            os.makedirs(SAVE_DIR, exist_ok=True)
            img.save(generate_filename())

        if config.save_mode in ("print_only", "print_and_save"):
            print_image(img)
    
    if config.launch_paint:
        open_paint_and_paste()

# ファイル名生成関数
def generate_filename():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return os.path.join(SAVE_DIR, f"screenshot_{timestamp}.png")

#ペイントアプリ起動
def open_paint_and_paste():
    try:
        subprocess.Popen(["mspaint.exe"])
        logging.info("[Paint] mspaint.exe を起動しました")

        for i in range(7):  # 最大7回まで待機（1秒ごと） 
            time.sleep(1)
            for proc in psutil.process_iter(attrs=["name"]):
                if proc.info["name"] and proc.info["name"].lower() == "mspaint.exe":
                    logging.info("[Paint] 起動を確認 → Ctrl+V を送信します")
                    time.sleep(0.5)
                    pyautogui.hotkey("ctrl", "v")
                    return

        logging.warning("[Paint] 起動確認ができませんでした（7秒間リトライ）") 

    except Exception as e:
        logging.error(f"[Paint] 起動または貼り付け処理でエラーが発生しました: {e}")

# スクリーンショットをA4縦・横に調整
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
    return canvas

def print_image(image: Image.Image):
    try:
        logging.info(f"[印刷開始] printer={config.printer_name}, orientation={config.orientation}")
        logging.info(f"[元画像サイズ] image.width={image.width}, image.height={image.height}")

        # === 印刷方向に応じたサイズと回転処理 ===
        if config.orientation == "portrait":
            dev_orientation = 1
            width, height = 2480, 3508  # A4縦（ピクセル）
            
            # 横長画像の場合は右回転（縦向きに収める）
            if image.width > image.height:
                image = image.rotate(270, expand=True)

            # 用紙サイズ（0.1mm単位で指定）
            paper_width = 2100    # 210mm
            paper_length = 2970   # 297mm

        else:
            dev_orientation = 2
            width, height = 3508, 2480  # A4横（ピクセル）

            # landscapeでは回転せずそのまま印刷
            paper_width = 2970    # 297mm
            paper_length = 2100   # 210mm

        logging.info(f"[印刷用サイズ] width={width}, height={height}, paper={paper_width}×{paper_length} (0.1mm単位)")

        # === 画像を用紙サイズに合わせてリサイズ＆中央配置 ===
        processed_image = prepare_image_for_print(image, width, height)
        processed_image = processed_image.convert("RGB").copy()

        # === プリンター設定の取得・用紙サイズ・方向を指定 ===
        hprinter = win32print.OpenPrinter(config.printer_name)
        devmode = win32print.GetPrinter(hprinter, 2)["pDevMode"]

        devmode.Orientation = dev_orientation
        devmode.PaperSize = 9  # A4（DMPAPER_A4 = 9）
        devmode.PaperWidth = paper_width
        devmode.PaperLength = paper_length
        devmode.Fields |= (
            win32con.DM_ORIENTATION |
            win32con.DM_PAPERSIZE |
            win32con.DM_PAPERWIDTH |
            win32con.DM_PAPERLENGTH
        )

        # === プリンターデバイスコンテキスト作成 ===
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(config.printer_name)
        hdc.SetMapMode(win32con.MM_TEXT)

        # 印刷可能サイズの取得（ピクセル単位）
        printable_width = hdc.GetDeviceCaps(win32con.HORZRES)
        printable_height = hdc.GetDeviceCaps(win32con.VERTRES)

        logging.info(f"[プリンタ描画可能範囲] printable_width={printable_width}, printable_height={printable_height}")

        # landscape時に幅・高さが逆転していたら補正（ドライバの仕様対策）
        if config.orientation == "landscape" and printable_width < printable_height:
            printable_width, printable_height = printable_height, printable_width

        # === 印刷処理開始 ===
        hdc.StartDoc("ScreenPrint")
        hdc.StartPage()

        # DIBを描画（画像を印刷用紙に貼り付け）
        dib = ImageWin.Dib(processed_image)
        dib.draw(hdc.GetHandleOutput(), (0, 0, printable_width, printable_height))

        hdc.EndPage()
        hdc.EndDoc()
        hdc.DeleteDC()
        win32print.ClosePrinter(hprinter)

    except Exception as e:
        messagebox.showerror("エラー", f"印刷に失敗しました。\n{e}")

# 別プロセスでキー監視V1.2.4から
def start_hotkey_listener():
    def listen():
        logging.info("[ショートカットキー監視] 監視スレッド開始")
        while True:
            try:
                if keyboard.is_pressed(config.shortcut_key):
                    logging.info(f"[キー検知] {config.shortcut_key} → handle_capture 実行")

                    # GUIが開いている場合、設定値を反映
                    if settings_window is not None and settings_window.winfo_exists():
                        try:
                            settings_window.update()
                            update_config_from_gui()
                        except Exception as e:
                            logging.warning(f"[GUI反映失敗] {e}")

                    handle_capture()
                    time.sleep(1)  # 連打防止
                time.sleep(0.1)
            except Exception as e:
                logging.warning(f"[監視エラー] {e}")
                time.sleep(2)
    threading.Thread(target=listen, daemon=True).start()

# 多重起動防止
def prevent_multiple_instances():
    mutex_name = "screenprinttool_single_instance"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
    if ctypes.GetLastError() == 183:
        messagebox.showwarning("警告", "このアプリはすでに起動しています。")
        exit(0)

# 起動時に設定読み込み
config = AppConfig(CONFIG_PATH)
config.load_or_create()

# 再起動処理関数
def on_restart(icon, item):
    icon.stop()
    os.execl(sys.executable, sys.executable, *sys.argv)

#タスクトレイ表示関数
def setup_tray_icon():
    global tray_icon  # ← これを追加

    def on_open_settings(icon, item):
        show_settings_window()

    # スクリーンショット保存先を開く
    def open_save_folder(icon, item):
        path = os.path.expanduser("~\\Pictures\\ScreenPrintTool")
        subprocess.Popen(f'explorer "{path}"')

    def on_show_version(icon, item):
        messagebox.showinfo("バージョン情報", "ScreenPrintTool v1.2.6")

    def on_exit(icon, item):
        icon.stop()
        os._exit(0)  # 強制終了（スレッドも停止）

    # アイコン画像の読み込み
    try:
        icon_image = Image.open(os.path.join(BASE_DIR, "icon.png"))
    except Exception:
        icon_image = Image.new("RGB", (64, 64), "gray")  # 代替アイコン

    menu = Menu(
        MenuItem("設定", on_open_settings),
        MenuItem("スクショ保存先を開く", open_save_folder),
        MenuItem("バージョン情報", on_show_version),
        MenuItem("再起動（設定再取得）", on_restart),
        MenuItem("アプリ終了", on_exit)
    )

    tray_icon = Icon("ScreenPrintTool", icon_image, "ScreenPrintTool", menu)
    tray_icon.run()

# ▼ 表示名⇔キー変換dict
shortcut_options = {
    "Print Screen（デフォルト）": "print_screen",
    "Ctrl + Print Screen": "ctrl+print_screen",
    "Shift + Print Screen": "shift+print_screen",
    "Ctrl + Alt + P": "ctrl+alt+p",
    "Ctrl + Shift + P": "ctrl+shift+p",
    "Alt + F12": "alt+f12",
    "Ctrl + Shift + F12": "ctrl+shift+f12",
    "Ctrl + Alt + Insert": "ctrl+alt+insert"
}
reverse_options = {v: k for k, v in shortcut_options.items()}

#設定ウィンドウ表示関数
def show_settings_window():
    global settings_window
    global shortcut_label_var, printer_var, save_mode_var, display_var, orientation_var, retention_var
    if settings_window is not None and settings_window.winfo_exists():
        settings_window.lift()
        settings_window.attributes("-topmost", True)
        settings_window.attributes("-topmost", False)
        return

    root = tk.Tk()
    try:
        icon_path = os.path.join(BASE_DIR, "icon.png")
        root.iconphoto(False, tk.PhotoImage(file=icon_path))
    except Exception:
        pass  # 読み込み失敗時はデフォルトのまま

    settings_window = root
    root.title("ScreenPrintTool 設定")
    root.geometry("500x600")

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
            if not (device.StateFlags & 1):  # DISPLAY_DEVICE_ACTIVE
                continue
            display_id = device.DeviceName[4:]
            monitor_list.append(display_id)
        except:
            break


    # UI配置
    shortcut_label_var = tk.StringVar(value=reverse_options.get(config.shortcut_key, "Print Screen（デフォルト）"))

    tk.Label(root, text="ショートカットキー:").pack(anchor="w", padx=10, pady=2)
    ttk.Combobox(
        root, textvariable=shortcut_label_var,
        values=list(shortcut_options.keys()),
        state="readonly"
    ).pack(fill="x", padx=10)

    tk.Label(root, text="プリンター:").pack(anchor="w", padx=10, pady=2)
    ttk.Combobox(root, textvariable=printer_var, values=printer_list, state="readonly").pack(fill="x", padx=10)

    # ディスプレイの選択
    tk.Label(root, text="ディスプレイ番号:").pack(anchor="w", padx=10, pady=2)
    ttk.Combobox(root, textvariable=display_var, values=monitor_list, state="readonly").pack(fill="x", padx=10)

    #保存モードの選択
    tk.Label(root, text="保存モード:").pack(anchor="w", padx=10, pady=2)
    for text, value in {
        "印刷のみ": "print_only",
        "保存のみ": "save_only",
        "印刷＋保存": "print_and_save"
    }.items():
        ttk.Radiobutton(root, text=text, variable=save_mode_var, value=value).pack(anchor="w", padx=20)

    # 「タスクバー（下部）を除外して撮影」チェックボックスの状態が変更されたときの処理
    # チェック状態を config.exclude_taskbar に反映し、設定ファイル (config.json) に保存する
    def on_exclude_taskbar_toggle():
        config.exclude_taskbar = exclude_taskbar_var.get()
        config.save()

    # 起動時にSnipping Toolの設定値をレジストリ値を読んで反映
    def get_snipping_tool_status():
        try:
            key_path = r"Control Panel\Keyboard"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, "PrintScreenKeyForSnippingEnabled")
                return value == 0  # 0ならSnipping Tool 無効化
        except Exception:
            return False  # 読み取れなければ OFF 扱いにする
    snipping_disable_var = tk.BooleanVar(value=get_snipping_tool_status())

    # Snipping Toolを無効にしない（競合を許容）設定 ← configと連動
    snipping_opt_out_var = tk.BooleanVar(value=config.snipping_opt_out)

    # チェック状態が変更されたときに config に保存
    def on_snipping_toggle():
        config.snipping_opt_out = snipping_opt_out_var.get()
        config.save()

        try:
            key_path = r"Control Panel\\Keyboard"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                value = 1 if config.snipping_opt_out else 0
                winreg.SetValueEx(key, "PrintScreenKeyForSnippingEnabled", 0, winreg.REG_DWORD, value)
            messagebox.showinfo("設定変更", "Snipping Tool の起動設定を変更しました。\n反映されない場合はPCの再起動をお試しください。\n必要に応じてこのアプリのショートカットキーを変更してください。")
        except Exception as e:
            messagebox.showerror("エラー", f"Snipping Toolの設定変更に失敗しました。\n{e}")

    # チェックボックス表示
    tk.Checkbutton(
        root,
        text="「Prt Scr」でSnipping Toolを起動する",
        variable=snipping_opt_out_var,
        command=on_snipping_toggle
    ).pack(anchor="w", padx=20, pady=5)

    # ペイントアプリ起動選択
    launch_paint_var = tk.BooleanVar(value=config.launch_paint)
    def on_launch_paint_toggle():
        config.launch_paint = launch_paint_var.get()
        config.save()

    tk.Checkbutton(
        root,
        text="ペイントアプリに貼り付ける",
        variable=launch_paint_var,
        command=on_launch_paint_toggle
    ).pack(anchor="w", padx=20, pady=5)

    # タスクバー（下部）を除外するチェック
    tk.Checkbutton(
        root,
        text="タスクバー（下部）を除外する　※検証中",
        variable=exclude_taskbar_var,
        command=on_exclude_taskbar_toggle
    ).pack(anchor="w", padx=20, pady=5)

    # 画像保存日数の設定
    tk.Label(root, text="画像保存日数（古い画像の自動削除）").pack(anchor="w", padx=10, pady=2)
    retention_var = tk.IntVar(value=config.retention_days)
    tk.Spinbox(root, from_=1, to=90, textvariable=retention_var).pack(fill="x", padx=10)

    # 印刷方向の設定
    tk.Label(root, text="印刷方向:").pack(anchor="w", padx=10, pady=2)
    orientation_var = tk.StringVar(value=config.orientation)
    ttk.Radiobutton(root, text="縦（標準）", variable=orientation_var, value="portrait").pack(anchor="w", padx=20)
    ttk.Radiobutton(root, text="横（挙動テスト用）", variable=orientation_var, value="landscape").pack(anchor="w", padx=20)

    # テスト印刷ボタン
    def on_test():
        config.shortcut_key = shortcut_options.get(shortcut_label_var.get(), "print_screen")
        config.printer_name = printer_var.get()
        config.save_mode = save_mode_var.get()
        config.display_id = display_var.get()
        config.orientation = orientation_var.get()
        config.retention_days = retention_var.get()
        config.save()
        handle_capture()

    def on_save():
        config.shortcut_key = shortcut_options.get(shortcut_label_var.get(), "print_screen")
        config.printer_name = printer_var.get()
        config.save_mode = save_mode_var.get()
        config.display_id = display_var.get()
        config.orientation = orientation_var.get()
        config.retention_days = retention_var.get()
        config.save()
        messagebox.showinfo("保存", "設定を保存しました。")

    frame = tk.Frame(root)
    frame.pack(pady=10)
    ttk.Button(frame, text=" テスト印刷", command=on_test).pack(side="left", padx=10)
    ttk.Button(frame, text=" 保存", command=on_save).pack(side="left", padx=10)

    # 「×」を押したら閉じるだけ（アプリは終了しない）
    def on_close():
        global settings_window
        settings_window = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

# GUI設定値をショートカット起動時に保存
def update_config_from_gui():
    global shortcut_label_var, printer_var, save_mode_var, display_var, orientation_var, retention_var
    try:
        config.shortcut_key = shortcut_options.get(shortcut_label_var.get(), "print_screen")
        config.printer_name = printer_var.get()
        config.save_mode = save_mode_var.get()
        config.display_id = display_var.get()
        config.orientation = orientation_var.get()
        config.retention_days = retention_var.get()
        config.save()
    except Exception as e:
        logging.warning(f"[GUI設定反映失敗] {e}")

# configの内容に応じて、ツール起動時にSnippingTool起動設定（レジストリ）を強制反映
def disable_snipping_tool_if_needed():
    try:
        key_path = r"Control Panel\\Keyboard"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            value = 1 if config.snipping_opt_out else 0
            winreg.SetValueEx(key, "PrintScreenKeyForSnippingEnabled", 0, winreg.REG_DWORD, value)
    except:
        pass

# ログ初期化関数
def setup_logging():
    log_dir = Path(os.getenv("APPDATA")) / "ScreenPrintTool" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / datetime.now().strftime("%Y%m%d_%H%M.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")]
    )

    logging.info("[起動] ScreenPrintTool v1.2.4 起動開始")

    # DPI 情報とモニター情報ログ
    hdc = win32gui.GetDC(0)
    dpi_x = win32print.GetDeviceCaps(hdc, win32con.LOGPIXELSX)
    dpi_y = win32print.GetDeviceCaps(hdc, win32con.LOGPIXELSY)
    win32gui.ReleaseDC(0, hdc)
    logging.info(f"[DPI設定] dpi_x={dpi_x}, dpi_y={dpi_y}")

    for i, m in enumerate(get_monitors(), 1):
        logging.info(f"[モニター{i}] x={m.x}, y={m.y}, width={m.width}, height={m.height}")


# メイン処理
if __name__ == "__main__":
    prevent_multiple_instances()
    setup_logging()
    config.load_or_create()
    cleanup_old_images()
    disable_snipping_tool_if_needed()
    start_hotkey_listener()
    setup_tray_icon() 