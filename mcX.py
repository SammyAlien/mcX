import sys
# 強制將 COM 初始化為多執行緒模式 (MTA)，確保與 WinRT 的環境完美一致
sys.coinit_flags = 0  

import time
import json
import threading
import asyncio
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
from PIL import Image, ImageDraw
import pystray

# 載入 WinRT (多媒體控制)
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager

# 檢查 comtypes 支援
try:
    import comtypes
    HAS_COMTYPES = True
except ImportError:
    HAS_COMTYPES = False

# -------------------------------------------------------------
# 🖥️ 0. Windows 高 DPI 自適應縮放設定
# -------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# -------------------------------------------------------------
# 🔒 1. 全域單一實體 (Single Instance) 互斥鎖檢查
# -------------------------------------------------------------
MUTEX_NAME = "Global\\mcX_SingleInstance_Mutex"
kernel32 = ctypes.windll.kernel32
mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
if kernel32.GetLastError() == 183:
    root = tk.Tk()
    root.withdraw()
    messagebox.showwarning("mcX", "mcX is already running!\nCheck the system tray icon.")
    sys.exit(0)


class mcXApp:
    def __init__(self, root):
        self.root = root
        self.root.title("mcX")
        self.root.resizable(False, False)

        # 系統控制變數
        self.running = True
        self.ser = None
        self.manual_reconnect = False 
        self.auto_connect_enabled = tk.BooleanVar(value=True)
        self.selected_port = tk.StringVar(value="Auto Detect")
        self.status_msg = tk.StringVar(value="Status: Searching Device...")
        self.btn_conn_text = tk.StringVar(value="Disconnect")
        
        # 媒體數據顯示變數
        self.title_var = tk.StringVar(value="No Media Playing")
        self.state_var = tk.StringVar(value="|| PAUSED")
        self.time_var = tk.StringVar(value="00:00 / 00:00")
        self.progress_val = tk.DoubleVar(value=0)
        
        # 音量顯示變數
        self.vol_pct_var = tk.StringVar(value="00%")
        self.current_vol = 0
        self.vol_err = ""

        # 時間軸自主計時追蹤變數
        self.internal_pos = 0
        self.last_api_pos = -1
        self.last_sync_time = 0

        # 上一次的媒體狀態紀錄
        self.last_payload = {
            "title": "Waiting...",
            "artist": "-",
            "status": 0,
            "pos": 0,
            "dur": 0,
            "vol": 0,
            "vol_err": ""
        }

        # 建立 UI 介面
        self.create_widgets()

        self.root.update_idletasks()
        req_width = max(450, self.root.winfo_reqwidth())
        req_height = self.root.winfo_reqheight()
        self.root.geometry(f"{req_width}x{req_height}")

        self.root.protocol('WM_DELETE_WINDOW', self.hide_to_tray)
        self.tray_icon = None
        self.init_tray_icon()

        # 啟動完全隔離的音量監聽執行緒
        if HAS_COMTYPES:
            self.vol_thread = threading.Thread(target=self.volume_worker, daemon=True)
            self.vol_thread.start()

        # 啟動背景媒體監聽與通訊執行緒
        self.worker_thread = threading.Thread(target=self.bg_worker, daemon=True)
        self.worker_thread.start()

    def create_widgets(self):
        style = ttk.Style()
        style.theme_use('clam')

        main_container = ttk.Frame(self.root, padding=12)
        main_container.pack(fill="both", expand=True)

        # 1. Serial 控制區域
        frame_conn = ttk.LabelFrame(main_container, padding=10)
        frame_conn.pack(fill="x", pady=(0, 8))
        
        lbl_title_conn = ttk.Label(frame_conn, text="Serial Connection", font=("Segoe UI", 9, "bold"))
        frame_conn.configure(labelwidget=lbl_title_conn)

        ttk.Label(frame_conn, text="COM Port:").grid(row=0, column=0, sticky="w", padx=(0, 5), pady=2)
        
        self.port_combo = ttk.Combobox(frame_conn, textvariable=self.selected_port, width=12, state="readonly")
        self.port_combo.grid(row=0, column=1, padx=2, pady=2)
        self.refresh_ports()

        btn_refresh = ttk.Button(frame_conn, text="Refresh", width=8, command=self.refresh_ports)
        btn_refresh.grid(row=0, column=2, padx=2, pady=2)

        self.btn_toggle_conn = ttk.Button(frame_conn, textvariable=self.btn_conn_text, width=10, command=self.toggle_connection)
        self.btn_toggle_conn.grid(row=0, column=3, padx=2, pady=2)

        chk_auto = ttk.Checkbutton(
            frame_conn, 
            text="Auto Connect (USB / Bluetooth)", 
            variable=self.auto_connect_enabled,
            command=self.on_auto_toggle
        )
        chk_auto.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 2))

        lbl_status = ttk.Label(frame_conn, textvariable=self.status_msg, font=("Consolas", 9, "bold"))
        lbl_status.grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0), padx=0)

        # 2. 媒體即時資訊區域
        frame_media = ttk.LabelFrame(main_container, padding=10)
        frame_media.pack(fill="x", pady=(0, 8))
        
        lbl_title_media = ttk.Label(frame_media, text="Live Media Monitor", font=("Segoe UI", 9, "bold"))
        frame_media.configure(labelwidget=lbl_title_media)

        # 歌名顯示
        ttk.Label(frame_media, textvariable=self.title_var, font=("Segoe UI", 10, "bold"), foreground="#0056b3").pack(anchor="w", pady=(0, 6))

        # 歌曲進度條
        self.pbar = ttk.Progressbar(frame_media, variable=self.progress_val, maximum=100)
        self.pbar.pack(fill="x", pady=(0, 6))

        # 狀態與時間
        frame_sub = ttk.Frame(frame_media)
        frame_sub.pack(fill="x", pady=(0, 8))
        ttk.Label(frame_sub, textvariable=self.state_var, font=("Consolas", 9, "bold")).pack(side="left")
        ttk.Label(frame_sub, textvariable=self.time_var, font=("Consolas", 9)).pack(side="right")

        # 音量顯示區域
        frame_vol = ttk.Frame(frame_media)
        frame_vol.pack(fill="x", pady=(2, 0))
        
        ttk.Label(frame_vol, textvariable=self.vol_pct_var, font=("Consolas", 9, "bold"), foreground="#555555").pack(anchor="w", pady=(0, 2))

        # Canvas 畫布
        bg_color = style.lookup('TFrame', 'background') or "#F0F0F0"
        self.vol_canvas = tk.Canvas(frame_vol, height=12, bg=bg_color, highlightthickness=0)
        self.vol_canvas.pack(fill="x", expand=True) 
        self.vol_canvas.bind("<Configure>", self.draw_vol_bar)

        # 3. 底部完全關閉按鈕區
        frame_bottom = ttk.Frame(main_container)
        frame_bottom.pack(fill="x", pady=(2, 0))
        
        btn_exit = ttk.Button(frame_bottom, text="Exit App", width=10, command=self.quit_app)
        btn_exit.pack(side="right")

    # 完全獨立且乾淨的音量讀取執行緒
    def volume_worker(self):
        import ctypes
        from ctypes import POINTER, cast, c_float, c_uint32
        import comtypes
        from comtypes import GUID, COMMETHOD, HRESULT, IUnknown

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)

        CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")

        class IMMDevice(IUnknown):
            _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
            _methods_ = [
                COMMETHOD([], HRESULT, 'Activate',
                          (['in'], POINTER(GUID), 'iid'),
                          (['in'], c_uint32, 'dwClsCtx'),
                          (['in'], POINTER(c_uint32), 'pActivationParams'),
                          (['out', 'retval'], POINTER(POINTER(IUnknown)), 'ppInterface')),
                COMMETHOD([], HRESULT, 'OpenPropertyStore',
                          (['in'], c_uint32, 'stgmAccess'),
                          (['out', 'retval'], POINTER(POINTER(IUnknown)), 'ppProperties')),
                COMMETHOD([], HRESULT, 'GetId',
                          (['out', 'retval'], POINTER(POINTER(ctypes.c_uint16)), 'ppstrId')),
                COMMETHOD([], HRESULT, 'GetState',
                          (['out', 'retval'], POINTER(c_uint32), 'pdwState'))
            ]

        class IMMDeviceEnumerator(IUnknown):
            _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
            _methods_ = [
                COMMETHOD([], HRESULT, 'EnumAudioEndpoints',
                          (['in'], c_uint32, 'dataFlow'),
                          (['in'], c_uint32, 'dwStateMask'),
                          (['out', 'retval'], POINTER(POINTER(IUnknown)), 'ppDevices')),
                COMMETHOD([], HRESULT, 'GetDefaultAudioEndpoint',
                          (['in'], c_uint32, 'dataFlow'),
                          (['in'], c_uint32, 'role'),
                          (['out', 'retval'], POINTER(POINTER(IMMDevice)), 'ppEndpoint'))
            ]

        class IAudioEndpointVolume(IUnknown):
            _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
            _methods_ = [
                COMMETHOD([], HRESULT, 'RegisterControlChangeNotify', (['in'], POINTER(IUnknown), 'pNotify')),
                COMMETHOD([], HRESULT, 'UnregisterControlChangeNotify', (['in'], POINTER(IUnknown), 'pNotify')),
                COMMETHOD([], HRESULT, 'GetChannelCount', (['out', 'retval'], POINTER(c_uint32), 'pnChannelCount')),
                COMMETHOD([], HRESULT, 'SetMasterVolumeLevel', (['in'], c_float, 'fLevelDB'), (['in'], POINTER(GUID), 'pguidEventContext')),
                COMMETHOD([], HRESULT, 'SetMasterVolumeLevelScalar', (['in'], c_float, 'fLevel'), (['in'], POINTER(GUID), 'pguidEventContext')),
                COMMETHOD([], HRESULT, 'GetMasterVolumeLevel', (['out', 'retval'], POINTER(c_float), 'pfLevelDB')),
                COMMETHOD([], HRESULT, 'GetMasterVolumeLevelScalar', (['out', 'retval'], POINTER(c_float), 'pfLevel')),
            ]
        
        while self.running:
            try:
                enumerator = comtypes.CoCreateInstance(
                    CLSID_MMDeviceEnumerator,
                    IMMDeviceEnumerator,
                    comtypes.CLSCTX_INPROC_SERVER
                )
                
                endpoint = enumerator.GetDefaultAudioEndpoint(0, 1)
                
                if not endpoint:
                    self.current_vol = 0
                    self.vol_err = "No Device"
                else:
                    interface = endpoint.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
                    vol_obj = cast(interface, POINTER(IAudioEndpointVolume))
                    vol = vol_obj.GetMasterVolumeLevelScalar()
                    self.current_vol = round(vol * 100)
                    self.vol_err = ""
            except Exception as e:
                self.vol_err = type(e).__name__
            
            time.sleep(0.5)

    # 動態繪製滿版 PSP 音量條 (細線風格)
    def draw_vol_bar(self, event=None):
        self.vol_canvas.delete("all")
        
        width = self.pbar.winfo_width()
        if width < 10:
            width = self.vol_canvas.winfo_width()
        if width < 10: 
            return

        height = self.vol_canvas.winfo_height()
        total_bars = 45 
        
        dx = width / total_bars
        active_bars = int((self.current_vol * total_bars) / 100)

        for i in range(total_bars):
            cx = i * dx + dx / 2
            
            if i < active_bars:
                line_w = 1 
                self.vol_canvas.create_rectangle(cx - line_w, 0, cx + line_w, height, fill="#555555", outline="")
            else:
                cy = height / 2
                r = 1.5
                self.vol_canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#A0A0A0", outline="")

    def refresh_ports(self):
        ports = serial.tools.list_ports.comports()
        port_list = ["Auto Detect"] + [p.device for p in ports if p.device.upper() != "COM1"]
        self.port_combo['values'] = port_list

    def on_auto_toggle(self):
        if self.auto_connect_enabled.get():
            self.selected_port.set("Auto Detect")
            self.manual_reconnect = True
            self.btn_conn_text.set("Disconnect")
        else:
            self.status_msg.set("Status: Auto Connect Off")

    def toggle_connection(self):
        if self.ser and self.ser.is_open:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
            self.status_msg.set("Status: Disconnected (Manual)")
            self.btn_conn_text.set("Connect")
            self.auto_connect_enabled.set(False)
        else:
            self.manual_reconnect = True
            self.btn_conn_text.set("Disconnect")

    def find_esp32_port(self):
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if port.device.upper() == "COM1":
                continue 
            port_info = f"{port.description} {port.product}".lower()
            if any(name in port_info for name in ["mcx", "esp32", "espressif", "usb jtag/serial"]):
                return port.device
        for port in ports:
            if port.device.upper() == "COM1":
                continue
            if port.vid == 0x303A:
                return port.device
        for port in ports:
            if port.device.upper() == "COM1":
                continue
            port_desc = port.description.lower()
            if port.vid in [0x1A86, 0x10C4] or any(kw in port_desc for kw in ["usb serial", "ch340", "cp210", "standard serial over bluetooth"]):
                return port.device
        return None

    async def get_media_payload(self):
        payload = self.last_payload.copy()
        payload["vol"] = self.current_vol
        payload["vol_err"] = self.vol_err
        
        try:
            sessions = await MediaManager.request_async()
            current_session = sessions.get_current_session()
            
            if current_session:
                playback_info = current_session.get_playback_info()
                status = 1 if playback_info.playback_status == 4 else 0
                payload["status"] = status
                
                media_properties = await current_session.try_get_media_properties_async()
                new_title = media_properties.title if media_properties and media_properties.title else "No Title"
                new_artist = media_properties.artist if media_properties and media_properties.artist else "Unknown Artist"
                
                is_song_changed = (new_title != payload["title"]) or (new_artist != payload["artist"])
                payload["title"] = new_title
                payload["artist"] = new_artist
                
                timeline = current_session.get_timeline_properties()
                if timeline and timeline.position:
                    api_pos = int(timeline.position.total_seconds())
                    dur = int(timeline.end_time.total_seconds()) if timeline.end_time else 0
                    
                    # 🌟 完美直覺邏輯：
                    # 1. 如果換歌、暫停、或是 API 回報的時間跟上一次紀錄不一樣（代表你有快轉、倒轉、手動拉進度條，或 API 推進了），立刻更新時間！
                    # 2. 如果 API 回報的時間跟上一次一模一樣（代表它沒動），我們就不做任何事，讓內部計時器自己順順 +1。
                    now = time.time()
                    if is_song_changed or status == 0:
                        self.internal_pos = api_pos
                        self.last_api_pos = api_pos
                        self.last_sync_time = now
                    elif api_pos != self.last_api_pos:
                        # API 時間有變化（不管是快轉、倒轉還是正常更新），直接採信！
                        self.internal_pos = api_pos
                        self.last_api_pos = api_pos
                        self.last_sync_time = now
                    else:
                        # API 時間沒變，若在播放中則自主 +1 保持流暢
                        if status == 1:
                            if now - self.last_sync_time >= 1.0:
                                if self.internal_pos < dur:
                                    self.internal_pos += 1
                                self.last_sync_time = now

                    payload["pos"] = self.internal_pos
                    payload["dur"] = dur
            else:
                payload["status"] = 0
                payload["title"] = "No Media"
                payload["pos"] = 0
                payload["dur"] = 0
                self.internal_pos = 0
                self.last_api_pos = -1
        except Exception:
            payload["status"] = 0
            payload["pos"] = 0
            payload["dur"] = 0
            
        self.last_payload = payload
        return payload

    def bg_worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self.running:
            data = loop.run_until_complete(self.get_media_payload())
            if data:
                self.root.after(0, self.update_ui_media, data)

            if self.manual_reconnect:
                self.manual_reconnect = False
                if self.ser and self.ser.is_open:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None

            if self.ser and self.ser.is_open:
                try:
                    _ = self.ser.in_waiting
                except (serial.SerialException, OSError):
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None

            target_port = self.selected_port.get()
            if self.auto_connect_enabled.get() or target_port == "Auto Detect":
                target_port = self.find_esp32_port()

            if (self.ser is None or not self.ser.is_open) and self.btn_conn_text.get() == "Disconnect":
                if target_port:
                    try:
                        self.ser = serial.Serial(target_port, 115200, timeout=1)
                        self.status_msg.set(f"Status: Connected ({target_port})")
                    except (serial.SerialException, OSError):
                        self.ser = None
                        self.status_msg.set(f"Status: Connecting ({target_port})...")
                else:
                    self.status_msg.set("Status: Searching Device...")

            # 使用高效字串通訊協議格式發送：V<音量>S<狀態>P<進度>D<總長>\n
            if self.ser and self.ser.is_open and data:
                try:
                    vol = data.get("vol", 0)
                    status = data.get("status", 0)
                    pos = data.get("pos", 0)
                    dur = data.get("dur", 0)
                    
                    packet = f"V{vol}S{status}P{pos}D{dur}\n"
                    self.ser.write(packet.encode('utf-8'))
                except (serial.SerialException, OSError):
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
                    
                    if target_port:
                        self.status_msg.set(f"Status: Reconnecting ({target_port})...")
                    else:
                        self.status_msg.set("Status: Searching Device...")

            time.sleep(1)

        loop.close()

    def update_ui_media(self, data):
        self.title_var.set(data["title"])
        
        self.current_vol = data.get('vol', 0)
        vol_err = data.get('vol_err', "")
        
        if not HAS_COMTYPES:
            self.vol_pct_var.set("No comtypes installed")
        elif vol_err:
            if vol_err == "No Device":
                self.vol_pct_var.set("00% (No Device)")
            else:
                self.vol_pct_var.set(f"ERR: {vol_err}")
        else:
            self.vol_pct_var.set(f"{self.current_vol:02d}%")
            
        self.draw_vol_bar()
        
        self.state_var.set("> PLAYING" if data["status"] == 1 else "|| PAUSED")
        
        pos_m, pos_s = divmod(data["pos"], 60)
        dur_m, dur_s = divmod(data["dur"], 60)
        self.time_var.set(f"{pos_m:02d}:{pos_s:02d} / {dur_m:02d}:{dur_s:02d}")

        if data["dur"] > 0:
            pct = (data["pos"] / data["dur"]) * 100
            self.progress_val.set(pct)
        else:
            self.progress_val.set(0)

    def create_tray_image(self):
        image = Image.new('RGB', (64, 64), color=(0, 102, 204))
        dc = ImageDraw.Draw(image)
        dc.rectangle((16, 16, 48, 48), fill=(255, 255, 255))
        return image

    def init_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem('Open', self.show_from_tray, default=True),
            pystray.MenuItem('Quit', self.quit_app)
        )
        self.tray_icon = pystray.Icon("mcX", self.create_tray_image(), "mcX", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def hide_to_tray(self):
        self.root.withdraw()

    def show_from_tray(self, icon=None, item=None):
        self.root.after(0, self.restore_window)

    def restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit_app(self, icon=None, item=None):
        self.running = False
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        
        self.root.after(0, self._destroy_app)

    def _destroy_app(self):
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = mcXApp(root)
    root.mainloop()