import os
import sys
import json
import time
import queue
import shutil
import threading
import traceback
import subprocess
import webbrowser
import base64
import io
import re
import smtplib
import datetime
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- Required third-party dependencies (clear guidance if missing) ---
_MISSING = []
try:
    import requests
except ImportError:
    _MISSING.append("requests")
try:
    import psutil
except ImportError:
    _MISSING.append("psutil")
try:
    import webview
except ImportError:
    _MISSING.append("pywebview")
try:
    import pyautogui
except ImportError:
    _MISSING.append("pyautogui")
try:
    from PIL import Image, ImageGrab, ImageDraw
except ImportError:
    _MISSING.append("pillow")

if _MISSING:
    msg = ("J.A.R.V.I.S. cannot start — missing required packages: "
           + ", ".join(_MISSING)
           + "\n\nInstall them with:\n    pip install "
           + " ".join(_MISSING)
           + "\n\n(On Windows, also recommended: pip install pywin32 pystray)")
    print(msg)
    sys.exit(1)

# --- Optional: clipboard support ---
try:
    import pyperclip
    HAS_CLIPBOARD = True
except ImportError:
    pyperclip = None
    HAS_CLIPBOARD = False

# System Tray support
try:
    import pystray
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# Native Windows API for app window focus
try:
    import ctypes
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import pythoncom
    HAS_PYTHONCOM = True
except ImportError:
    HAS_PYTHONCOM = False

try:
    import speech_recognition as sr
    SPEECH_AVAILABLE = True
except ImportError:
    SPEECH_AVAILABLE = False

try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIPY_AVAILABLE = True
except ImportError:
    SPOTIPY_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


def log_debug(msg):
    try:
        _base = os.path.dirname(os.path.abspath(__file__))
        _dir = os.path.join(_base, "jarvis_data")
        try:
            os.makedirs(_dir, exist_ok=True)
        except Exception:
            _dir = _base
        _log_path = os.path.join(_dir, "jarvis_debug.log")
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ==========================================================================
# --- CONFIGURATION (all keys/IDs live in jarvis_config.json) ---
# ==========================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "jarvis_config.json")

# Everything a user might need to edit lives here. On first run this file is
# written to disk; after that, edit jarvis_config.json (no need to touch code).
DEFAULT_CONFIG = {
    "email": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "address": "YOUR_EMAIL@gmail.com",
        "app_password": "YOUR_APP_PASSWORD_HERE",
        "default_receiver": "YOUR_RECEIVER_EMAIL@gmail.com",
        # "browser" = open Gmail in Chrome and send visibly; "smtp" = silent background send.
        "mode": "browser",
        "auto_send": True,
    },
    "spotify": {
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "http://127.0.0.1:8888/callback",
    },
    "ollama": {
        "chat_url": "http://localhost:11434/api/chat",
        "generate_url": "http://localhost:11434/api/generate",
        "model": "llama3.2",
        "vision_model": "llava",
    },
    "assistant": {
        "wake_word": "jarvis",
        "user_title": "Sir",
        "default_city": "London",
        "voice_rate": 0,          # SAPI rate -3..+3, JARVIS sounds best slightly measured
        "speak_greeting": True,
    },
    "contacts": {
        "me": "YOUR_RECEIVER_EMAIL@gmail.com",
    },
    # Which widgets/buttons appear in the bottom bar (user-customisable in-app).
    "ui": {
        "bottom_bar": {
            "chat": True, "telemetry": True, "spotify": True, "scratchpad": True,
            "todo": True, "viewer": True, "overlay": True, "mic": True,
            "screenshot": True, "sysinfo": True, "vision": True, "autovision": True,
            "stop": True, "settings": True,
        }
    },
}


def _deep_merge(base, override):
    """Recursively merge override into base (base is the default template)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    cfg = DEFAULT_CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = _deep_merge(DEFAULT_CONFIG, json.load(f))
        except Exception:
            cfg = DEFAULT_CONFIG
    else:
        # First run: write a template the user can fill in.
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
        except Exception:
            pass
    # Environment variables still override the file if present.
    env = os.getenv
    cfg["email"]["smtp_server"] = env("JARVIS_SMTP_SERVER", cfg["email"]["smtp_server"])
    cfg["email"]["smtp_port"] = int(env("JARVIS_SMTP_PORT", cfg["email"]["smtp_port"]))
    cfg["email"]["address"] = env("JARVIS_EMAIL_ADDRESS", cfg["email"]["address"])
    cfg["email"]["app_password"] = env("JARVIS_EMAIL_PASSWORD", cfg["email"]["app_password"])
    cfg["email"]["default_receiver"] = env("USER_RECEIVER_EMAIL", cfg["email"]["default_receiver"])
    cfg["spotify"]["client_id"] = env("SPOTIPY_CLIENT_ID", cfg["spotify"]["client_id"])
    cfg["spotify"]["client_secret"] = env("SPOTIPY_CLIENT_SECRET", cfg["spotify"]["client_secret"])
    cfg["spotify"]["redirect_uri"] = env("SPOTIPY_REDIRECT_URI", cfg["spotify"]["redirect_uri"])
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        return True
    except Exception as e:
        log_debug(f"Failed to save config: {e}")
        return False


CONFIG = load_config()

# Flat convenience aliases used throughout the code.
SMTP_SERVER = CONFIG["email"]["smtp_server"]
SMTP_PORT = int(CONFIG["email"]["smtp_port"])
EMAIL_ADDRESS = CONFIG["email"]["address"]
EMAIL_PASSWORD = CONFIG["email"]["app_password"]
USER_RECEIVER_EMAIL = CONFIG["email"]["default_receiver"]
EMAIL_MODE = str(CONFIG["email"]["mode"]).lower()
EMAIL_AUTO_SEND = bool(CONFIG["email"]["auto_send"])

SPOTIPY_CLIENT_ID = CONFIG["spotify"]["client_id"]
SPOTIPY_CLIENT_SECRET = CONFIG["spotify"]["client_secret"]
SPOTIPY_REDIRECT_URI = CONFIG["spotify"]["redirect_uri"]

OLLAMA_URL = CONFIG["ollama"]["chat_url"]
OLLAMA_GENERATE_URL = CONFIG["ollama"]["generate_url"]
MODEL_NAME = CONFIG["ollama"]["model"]
VISION_MODEL = CONFIG["ollama"]["vision_model"]

WAKE_WORD = str(CONFIG["assistant"]["wake_word"]).lower()
USER_TITLE = CONFIG["assistant"]["user_title"]
DEFAULT_CITY = CONFIG["assistant"]["default_city"]
VOICE_RATE = int(CONFIG["assistant"]["voice_rate"])

# Address book: friendly name -> email.
CONTACTS = dict(CONFIG.get("contacts", {"me": USER_RECEIVER_EMAIL}))

pyautogui.PAUSE = 0.02          # faster automation
pyautogui.FAILSAFE = True

# All generated/runtime files (except the user-facing config) live in a subfolder
# to keep the app directory tidy.
DATA_DIR = os.path.join(BASE_DIR, "jarvis_data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = BASE_DIR
DOCS_DIR = os.path.join(DATA_DIR, "documents")
try:
    os.makedirs(DOCS_DIR, exist_ok=True)
except Exception:
    DOCS_DIR = DATA_DIR

DEBUG_LOG = os.path.join(DATA_DIR, "jarvis_debug.log")
MEMORY_FILE = os.path.join(DATA_DIR, "jarvis_memory.json")
SPOTIFY_CACHE_FILE = os.path.join(DATA_DIR, ".spotify_cache")

SYSTEM_PROMPT = (
    f"You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), the AI created by Tony Stark, "
    f"now serving {USER_TITLE}. You have a refined British butler's demeanour: unfailingly courteous, "
    f"dry wit used sparingly, calm under any circumstance, and quietly brilliant. "
    f"Address the user as '{USER_TITLE}'. Speak in crisp, elegant British English. "
    "Be concise: 1-3 short sentences for spoken replies. Offer a subtle, understated wit when appropriate, "
    "never slapstick. You may gently anticipate needs. "
    "CRITICAL TRUTHFULNESS RULE: Always be truthful. Never claim to have executed a PC action, sent an email, "
    "or completed a task unless the local Python backend has actually done it and confirmed success."
)

# Dedicated prompt used only for drafting email bodies.
EMAIL_WRITER_PROMPT = (
    "You are an expert email writer. Write a clear, professional, well-structured email body based on the user's request. "
    "Do NOT include a subject line, and do NOT include placeholder tokens like [Your Name] unless asked. "
    "Sign off as 'Best regards'. Output ONLY the email body text, nothing else."
)


def init_thread_com():
    if HAS_PYTHONCOM:
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

def focus_window_by_title(app_name):
    if not HAS_WIN32:
        return False
    try:
        def enum_windows_callback(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).lower()
                if app_name.lower() in title:
                    extra.append(hwnd)
        
        hwnds = []
        win32gui.EnumWindows(enum_windows_callback, hwnds)
        if hwnds:
            hwnd = hwnds[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return True
    except Exception as e:
        log_debug(f"Window focus error: {e}")
    return False


def find_chrome_path():
    """Locate the Chrome executable across common install locations."""
    # First try PATH.
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    # Windows common locations.
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


EMAIL_REGEX = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')


def extract_email_address(text):
    """Return the first raw email address found in text, or None."""
    m = EMAIL_REGEX.search(text or "")
    return m.group(0) if m else None


def resolve_contact(name_or_addr, contacts):
    """Turn a friendly name or raw address into an email address using the address book."""
    if not name_or_addr:
        return None
    candidate = name_or_addr.strip().strip(".,")
    # Already an address?
    addr = extract_email_address(candidate)
    if addr:
        return addr
    # Look up by friendly name (case-insensitive).
    lowered = candidate.lower()
    for key, val in contacts.items():
        if key.lower() == lowered and extract_email_address(val):
            return val
    # Partial match (e.g. "john" matches "john smith").
    for key, val in contacts.items():
        if lowered in key.lower() and extract_email_address(val):
            return val
    return None


# --- IN-APP DYNAMIC CARDS MODEL ---
class JarvisInAppCard:
    def __init__(self, card_id, title, card_type, content):
        self.card_id = card_id
        self.title = title
        self.card_type = card_type  # e.g., 'carousel', 'code_bug', 'email_draft'
        self.content = content

    def to_dict(self):
        return {
            "card_id": self.card_id,
            "title": self.title,
            "card_type": self.card_type,
            "content": self.content
        }


HTML_UI = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>J.A.R.V.I.S. HUD System</title>
    <style>
        :root {
            --accent: #00e5ff;
            --accent-soft: rgba(0, 229, 255, 0.4);
            --accent-dim: rgba(0, 229, 255, 0.12);
            --green: #1ed760;
            --bg: #05070b;
            --panel: rgba(9, 13, 22, 0.85);
            --panel-solid: #090d16;
            --border: #1e293b;
            --border-lit: rgba(0, 229, 255, 0.35);
            --text-dim: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Consolas', 'Courier New', monospace; user-select: none; }

        body {
            background-color: var(--bg);
            color: var(--accent);
            height: 100vh;
            overflow: hidden;
            background-image:
                linear-gradient(rgba(12, 18, 32, 0.45) 1px, transparent 1px),
                linear-gradient(90deg, rgba(12, 18, 32, 0.45) 1px, transparent 1px);
            background-size: 40px 40px;
        }

        /* ===== COOL WINDOW TITLE BAR ===== */
        .titlebar {
            position: absolute; top: 0; left: 0; right: 0; height: 42px;
            display: flex; align-items: center; justify-content: space-between;
            padding: 0 18px; z-index: 500;
            background: linear-gradient(90deg, rgba(9,13,22,0.95), rgba(6,16,26,0.85), rgba(9,13,22,0.95));
            border-bottom: 1px solid var(--border-lit);
            box-shadow: 0 2px 20px rgba(0, 229, 255, 0.12);
            -webkit-app-region: drag;
        }
        .titlebar .tb-left { display: flex; align-items: center; gap: 12px; }
        .tb-orb { width: 14px; height: 14px; border-radius: 50%;
            background: radial-gradient(circle at 35% 35%, #7fe9ff, var(--accent) 60%, #026 100%);
            box-shadow: 0 0 12px var(--accent); animation: tb-pulse 2.6s ease-in-out infinite; }
        @keyframes tb-pulse { 50% { box-shadow: 0 0 4px var(--accent); opacity: 0.7; } }
        .tb-title { font-size: 13px; font-weight: bold; letter-spacing: 6px; color: #cfefff;
            text-shadow: 0 0 10px var(--accent-soft); }
        .tb-sub { font-size: 9px; letter-spacing: 3px; color: var(--text-dim); margin-left: 6px; }
        .tb-right { display: flex; align-items: center; gap: 14px; -webkit-app-region: no-drag; }
        .tb-stat { font-size: 9px; letter-spacing: 2px; color: var(--text-dim); }
        .tb-stat b { color: var(--green); }
        .tb-lines { display: flex; gap: 3px; align-items: flex-end; height: 14px; }
        .tb-lines i { width: 3px; background: var(--accent); border-radius: 2px; animation: eq 1s ease-in-out infinite; opacity: 0.8; }
        .tb-lines i:nth-child(1){height:6px;animation-delay:0s} .tb-lines i:nth-child(2){height:12px;animation-delay:.15s}
        .tb-lines i:nth-child(3){height:8px;animation-delay:.3s} .tb-lines i:nth-child(4){height:14px;animation-delay:.45s}
        @keyframes eq { 50% { transform: scaleY(0.4); } }

        /* ===== CENTRAL ARC REACTOR ===== */
        .reactor-container {
            position: absolute; top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            width: 300px; height: 300px;
            display: flex; justify-content: center; align-items: center;
            pointer-events: none; z-index: 1;
            transition: transform 0.25s ease;
        }
        .ring { position: absolute; border-radius: 50%; border: 2px dashed var(--accent-soft); animation: spin 20s linear infinite; }
        .ring-1 { width: 260px; height: 260px; border-color: rgba(0,229,255,0.3); border-style: solid; border-width: 1px; }
        .ring-2 { width: 210px; height: 210px; animation-direction: reverse; animation-duration: 15s; }
        .ring-3 { width: 170px; height: 170px; border: 3px solid var(--accent);
            box-shadow: 0 0 25px rgba(0,229,255,0.6), inset 0 0 25px rgba(0,229,255,0.6); animation-duration: 10s; }
        .core-center {
            width: 120px; height: 120px; background: rgba(9,13,22,0.9);
            border: 2px solid var(--accent); border-radius: 50%;
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            box-shadow: 0 0 30px rgba(0,229,255,0.5);
            font-size: 16px; font-weight: bold; letter-spacing: 2px; color: var(--accent);
            transition: all 0.25s ease;
        }
        .reactor-container.listening .core-center { border-color: var(--green); color: var(--green); box-shadow: 0 0 45px rgba(30,215,96,0.7); }
        .reactor-container.listening .ring-3 { border-color: var(--green); box-shadow: 0 0 30px rgba(30,215,96,0.6); }
        .reactor-container.wake-triggered .core-center { border-color: #06b6d4; color: #06b6d4; transform: scale(1.12); box-shadow: 0 0 55px rgba(6,182,212,0.9); }
        .reactor-container.active-speech .core-center { border-color: #ff2e63; color: #ff2e63; box-shadow: 0 0 50px rgba(255,46,99,0.7); transform: scale(1.06); }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

        /* ===== WIDGETS ===== */
        .workspace { position: relative; width: 100vw; height: 100vh; z-index: 10; pointer-events: none; }
        .widget {
            position: absolute;
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 12px;
            backdrop-filter: blur(10px);
            pointer-events: auto;
            display: flex; flex-direction: column; overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .widget:hover { border-color: var(--border-lit); box-shadow: 0 12px 34px rgba(0,229,255,0.15); }
        .widget.hidden { display: none !important; }
        .widget-header {
            padding: 12px 16px; font-size: 12px; font-weight: bold; letter-spacing: 1px;
            border-bottom: 1px solid var(--border); cursor: grab;
            display: flex; justify-content: space-between; align-items: center;
            color: var(--accent);
        }
        .widget-header:active { cursor: grabbing; }
        .header-title-wrap { display: flex; align-items: center; gap: 8px; }
        .status-dot { width: 7px; height: 7px; background: var(--accent); border-radius: 50%; box-shadow: 0 0 8px var(--accent); }
        .close-widget-btn { background: none; border: none; color: var(--accent); font-weight: bold; cursor: pointer; font-size: 15px; line-height: 1; }
        .close-widget-btn:hover { color: #ff2e63; }

        #chatWidget { width: 430px; height: 480px; top: 70px; left: 30px; }
        #telemetryWidget { width: 300px; height: 190px; top: 565px; left: 30px; }
        #scratchpadWidget { width: 320px; height: 200px; top: 70px; right: 30px; }
        #todoWidget { width: 340px; height: 270px; top: 285px; right: 30px; }
        #spotifyWidget { width: 320px; height: 175px; top: 565px; right: 30px; border-color: rgba(30,215,96,0.4); }
        #spotifyWidget .widget-header { color: var(--green); border-bottom-color: rgba(30,215,96,0.2); }
        #viewerWidget { width: 560px; height: 470px; top: 90px; left: 480px; }
        #docWidget { width: 540px; height: 520px; top: 70px; left: 440px; }
        #settingsWidget { width: 320px; height: 380px; top: 200px; left: 50%; margin-left: -160px; }
        #viewerBody img { max-width: 100%; max-height: 100%; object-fit: contain; }
        #viewerBody iframe { width: 100%; height: 100%; border: none; background: #fff; }

        /* Dynamic cards */
        #cardsGrid { position: absolute; top: 70px; left: 480px; width: 360px; max-height: calc(100vh - 170px);
            overflow-y: auto; display: flex; flex-direction: column; gap: 12px; pointer-events: auto; z-index: 15; }
        .jarvis-inapp-card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
            backdrop-filter: blur(10px); padding: 12px; color: #e2e8f0; box-shadow: 0 8px 26px rgba(0,0,0,0.5);
            animation: fadeInCard 0.25s ease-out; }
        @keyframes fadeInCard { from { opacity: 0; transform: translateY(-8px);} to { opacity: 1; transform: translateY(0);} }
        .jarvis-inapp-card:hover { border-color: var(--border-lit); }
        .card-header-bar { display: flex; justify-content: space-between; align-items: center;
            border-bottom: 1px solid var(--border); padding-bottom: 6px; margin-bottom: 8px; }
        .card-title-text { font-size: 11px; font-weight: bold; color: #fff; letter-spacing: 1px; }
        .card-type-badge { font-size: 9px; background: var(--accent-dim); color: var(--accent);
            padding: 2px 6px; border-radius: 6px; border: 1px solid var(--border-lit); text-transform: uppercase; }
        .card-body-content { font-size: 11px; line-height: 1.5; margin-bottom: 8px; max-height: 200px; overflow-y: auto; }
        .card-actions-bar { display: flex; justify-content: flex-end; gap: 6px; }

        /* Chat */
        .chat-messages { flex: 1; padding: 16px; overflow-y: auto; font-size: 12px; line-height: 1.6; color: var(--text-dim); user-select: text; }
        .chat-input-area { display: flex; padding: 12px; border-top: 1px solid var(--border); background: rgba(5,7,11,0.8); }
        .chat-input-area input { flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
            padding: 10px 14px; color: #fff; font-size: 12px; outline: none; user-select: text; }
        .chat-input-area input:focus { border-color: var(--accent); }
        .chat-input-area button { background: var(--accent); color: var(--bg); border: none; border-radius: 8px;
            padding: 0 16px; margin-left: 8px; font-weight: bold; cursor: pointer; letter-spacing: 1px; }
        .chat-input-area button:hover { box-shadow: 0 0 14px var(--accent); }

        .widget-body-pad { padding: 16px; font-size: 12px; display: flex; flex-direction: column; gap: 10px; flex: 1; }
        .telemetry-bar-bg { background: rgba(0,0,0,0.5); border: 1px solid var(--border); border-radius: 6px; height: 10px; width: 100%; overflow: hidden; padding: 1px; }
        .telemetry-fill { background: linear-gradient(90deg, rgba(0,229,255,0.4), var(--accent)); height: 100%; width: 0%; border-radius: 4px; transition: width 0.4s ease; box-shadow: 0 0 8px var(--accent); }
        .scratchpad-area { flex: 1; background: rgba(0,0,0,0.5); border: 1px solid var(--border); border-radius: 8px; color: #fff; padding: 10px; font-size: 12px; resize: none; outline: none; user-select: text; }
        .scratchpad-area:focus { border-color: var(--accent); }
        .action-btn { background: var(--accent-dim); color: var(--accent); border: 1px solid var(--border-lit); border-radius: 8px; padding: 8px 12px; font-weight: bold; cursor: pointer; font-size: 11px; text-align: center; letter-spacing: 1px; transition: all 0.2s ease; }
        .action-btn:hover { background: var(--accent); color: var(--bg); box-shadow: 0 0 12px var(--accent); }
        .todo-list-container { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; user-select: text; }
        .todo-item { display: flex; align-items: center; justify-content: space-between; background: rgba(0,229,255,0.05); padding: 6px 10px; border-radius: 8px; border: 1px solid rgba(0,229,255,0.2); }
        .todo-item span { font-size: 11px; color: #e2e8f0; }

        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: rgba(0,0,0,0.3); }
        ::-webkit-scrollbar-thumb { background: var(--border-lit); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--accent); }

        /* Bottom dock */
        .bottom-control-bar { position: absolute; bottom: 22px; left: 50%; transform: translateX(-50%);
            display: flex; gap: 10px; z-index: 1000; pointer-events: auto; align-items: center;
            background: rgba(9,13,22,0.9); padding: 9px 18px; border-radius: 40px; border: 1px solid var(--border-lit);
            backdrop-filter: blur(12px); box-shadow: 0 8px 30px rgba(0,0,0,0.7), 0 0 18px rgba(0,229,255,0.12); }
        .hud-btn { background: rgba(0,229,255,0.06); border: 1px solid var(--border-lit); color: var(--accent);
            width: 42px; height: 42px; border-radius: 50%; font-size: 16px; cursor: pointer;
            display: flex; align-items: center; justify-content: center; transition: all 0.2s ease; }
        .hud-btn:hover { background: var(--accent); color: var(--bg); box-shadow: 0 0 18px var(--accent); transform: translateY(-3px); }
        .hud-btn.danger { border-color: #ff2e63; color: #ff2e63; }
        .hud-btn.danger:hover { background: #ff2e63; color: #fff; box-shadow: 0 0 18px #ff2e63; }
        .hud-btn.active-mute { border-color: #f59e0b; color: #f59e0b; }
        .hud-btn.listening { border-color: var(--green); color: var(--green); animation: pulse-green 1.5s infinite; }
        .hud-btn.auto-vision-active { border-color: #c084fc; color: #c084fc; box-shadow: 0 0 18px rgba(192,132,252,0.7); }
        .separator { width: 1px; height: 26px; background: rgba(0,229,255,0.3); margin: 0 3px; }
        @keyframes pulse-green { 0% { box-shadow: 0 0 5px rgba(30,215,96,0.4);} 50% { box-shadow: 0 0 18px rgba(30,215,96,0.9);} 100% { box-shadow: 0 0 5px rgba(30,215,96,0.4);} }

        .watermark { position: absolute; bottom: 14px; right: 22px; font-size: 10px; color: rgba(0,229,255,0.3); letter-spacing: 2px; z-index: 5; pointer-events: none; }

        .spotify-controls { display: flex; gap: 12px; }
        .spotify-controls button { background: #131c2e; border: 1px solid var(--green); color: var(--green);
            width: 34px; height: 34px; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 12px; }
        .spotify-controls button.play-btn { background: var(--green); color: var(--bg); }
    </style>
</head>
<body>

    <!-- COOL TITLE BAR -->
    <div class="titlebar">
        <div class="tb-left">
            <div class="tb-orb"></div>
            <div><span class="tb-title">J.A.R.V.I.S</span><span class="tb-sub">HUD SYSTEM</span></div>
        </div>
        <div class="tb-right">
            <div class="tb-stat">CORE <b>ONLINE</b></div>
            <div class="tb-lines"><i></i><i></i><i></i><i></i></div>
        </div>
    </div>

    <div class="reactor-container" id="reactorContainer">
        <div class="ring ring-1"></div>
        <div class="ring ring-2"></div>
        <div class="ring ring-3"></div>
        <div class="core-center" id="coreLabel"><div>JARVIS</div></div>
    </div>

    <div class="workspace">
        <div id="cardsGrid"></div>

        <div class="widget" id="chatWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot"></div><span>SYSTEM LOG &amp; CHAT</span></div>
                <button class="close-widget-btn" onclick="toggleWidget('chatWidget')">&times;</button>
            </div>
            <div class="chat-messages" id="chatMessages"></div>
            <div class="chat-input-area">
                <input type="text" id="commandInput" placeholder="Enter command..." autocomplete="off">
                <button onclick="submitCommand()">SEND</button>
            </div>
        </div>

        <div class="widget hidden" id="telemetryWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot"></div><span>SYSTEM TELEMETRY</span></div>
                <button class="close-widget-btn" onclick="toggleWidget('telemetryWidget')">&times;</button>
            </div>
            <div class="widget-body-pad">
                <div>CPU UTILIZATION: <span id="cpuVal">0%</span></div>
                <div class="telemetry-bar-bg"><div class="telemetry-fill" id="cpuFill"></div></div>
                <div>RAM MEMORY: <span id="ramVal">0%</span></div>
                <div class="telemetry-bar-bg"><div class="telemetry-fill" id="ramFill"></div></div>
            </div>
        </div>

        <div class="widget hidden" id="scratchpadWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot"></div><span>MEMORY SCRATCHPAD</span></div>
                <button class="close-widget-btn" onclick="toggleWidget('scratchpadWidget')">&times;</button>
            </div>
            <div class="widget-body-pad">
                <textarea class="scratchpad-area" id="scratchpadInput" placeholder="Quick notes..."></textarea>
                <button class="action-btn" onclick="saveScratchpad()">SAVE TO MEMORY</button>
            </div>
        </div>

        <div class="widget hidden" id="todoWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot"></div><span>MISSION DIRECTIVES</span></div>
                <button class="close-widget-btn" onclick="toggleWidget('todoWidget')">&times;</button>
            </div>
            <div class="widget-body-pad">
                <div style="display: flex; gap: 6px;">
                    <input type="text" id="todoInput" placeholder="Add directive..." style="flex:1; background:rgba(0,0,0,0.5); border:1px solid var(--border); border-radius:8px; padding:7px; color:#fff; font-size:11px; outline:none;" autocomplete="off">
                    <button class="action-btn" onclick="addTodoItem()">ADD</button>
                </div>
                <div class="todo-list-container" id="todoListContainer"></div>
            </div>
        </div>

        <div class="widget hidden" id="spotifyWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot" style="background:var(--green);box-shadow:0 0 8px var(--green);"></div><span>SPOTIFY LINK</span></div>
                <button class="close-widget-btn" style="color:var(--green);" onclick="toggleWidget('spotifyWidget')">&times;</button>
            </div>
            <div class="widget-body-pad" style="align-items: center; justify-content: center; text-align: center;">
                <img id="spotifyAlbumArt" src="" style="width: 56px; height: 56px; border-radius: 8px; border: 1px solid var(--green); display: none; margin-bottom: 6px;">
                <div id="spotifyTrack" style="font-weight: bold; font-size: 12px; color: #fff; width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Connecting Spotify...</div>
                <div id="spotifyArtist" style="font-size: 10px; color: #64748b; margin-bottom: 8px; width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Please wait</div>
                <div class="spotify-controls">
                    <button onclick="pywebview.api.spotify_previous_js()">&#9198;</button>
                    <button class="play-btn" id="spotPlayBtn" onclick="pywebview.api.spotify_toggle_play_js()">&#9199;</button>
                    <button onclick="pywebview.api.spotify_next_js()">&#9197;</button>
                </div>
            </div>
        </div>

        <div class="widget hidden" id="viewerWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot"></div><span id="viewerTitle">VIEWER</span></div>
                <button class="close-widget-btn" onclick="toggleWidget('viewerWidget')">&times;</button>
            </div>
            <div id="viewerBody" style="flex:1; overflow:auto; background:rgba(0,0,0,0.4); display:flex; align-items:center; justify-content:center;">
                <div style="color:#64748b; font-size:11px; padding:20px;">Ask JARVIS to show an image or open a website.</div>
            </div>
        </div>

        <div class="widget hidden" id="docWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot"></div><span id="docTitle">DOCUMENT</span></div>
                <button class="close-widget-btn" onclick="toggleWidget('docWidget')">&times;</button>
            </div>
            <div style="display:flex; gap:4px; padding:8px 10px 0;">
                <button class="action-btn" id="docTabEdit" style="flex:1; padding:5px;" onclick="setDocTab('edit')">EDIT</button>
                <button class="action-btn" id="docTabPreview" style="flex:1; padding:5px; opacity:0.6;" onclick="setDocTab('preview')">PREVIEW</button>
            </div>
            <textarea id="docEditor" class="scratchpad-area" style="flex:1; margin:8px 10px; font-family:'Consolas',monospace;" placeholder="Your document will appear here..." oninput="onDocEdited()"></textarea>
            <iframe id="docPreview" style="flex:1; margin:8px 10px; border:1px solid var(--border); border-radius:8px; background:#0a0e17; display:none;"></iframe>
            <div style="display:flex; gap:6px; padding:0 10px 10px 10px;">
                <input type="text" id="docEditInstruction" placeholder="Tell JARVIS how to edit..." style="flex:1; background:rgba(0,0,0,0.5); border:1px solid var(--border); border-radius:8px; padding:7px; color:#fff; font-size:11px; outline:none;" autocomplete="off">
                <button class="action-btn" onclick="submitDocEdit()">REVISE</button>
                <button class="action-btn" onclick="pywebview.api.export_current_document_pdf()">EXPORT</button>
            </div>
        </div>

        <div class="widget hidden" id="settingsWidget">
            <div class="widget-header">
                <div class="header-title-wrap"><div class="status-dot"></div><span>CONTROL BAR SETTINGS</span></div>
                <button class="close-widget-btn" onclick="toggleWidget('settingsWidget')">&times;</button>
            </div>
            <div class="widget-body-pad">
                <div style="font-size:10px; color:var(--text-dim); margin-bottom:4px;">Toggle which buttons appear in the bottom bar:</div>
                <div id="settingsToggles" style="display:flex; flex-direction:column; gap:5px; overflow-y:auto; flex:1;"></div>
                <button class="action-btn" onclick="saveBarSettings()">SAVE LAYOUT</button>
            </div>
        </div>
    </div>

    <div class="bottom-control-bar" id="bottomBar"></div>
    <div class="watermark">J.A.R.V.I.S. ENTERPRISE HUD V8</div>
    <script>
        window.addEventListener('pywebviewready', function() {
            renderBottomBar();          // render with defaults immediately
            renderSettingsToggles();
            pywebview.api.set_ui_ready();
            appendLog("J.A.R.V.I.S. Neural Core initialized.", false);
        });

        const chatMessages = document.getElementById('chatMessages');
        const commandInput = document.getElementById('commandInput');
        const reactorContainer = document.getElementById('reactorContainer');
        const coreLabel = document.getElementById('coreLabel').firstElementChild;

        commandInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitCommand(); });

        function appendLog(text, isUser = false) {
            const div = document.createElement('div');
            div.style.color = isUser ? 'var(--accent-cyan)' : '#38bdf8';
            div.style.marginBottom = '6px';
            div.textContent = text;
            chatMessages.appendChild(div);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        async function submitCommand() {
            const cmd = commandInput.value.trim();
            if (!cmd) return;
            commandInput.value = '';
            pywebview.api.handle_text_command(cmd);
        }

        function renderInAppCard(cardData) {
            const grid = document.getElementById('cardsGrid');
            if (!grid) return;

            const cardEl = document.createElement('div');
            cardEl.className = 'jarvis-inapp-card';
            cardEl.id = cardData.card_id;

            let bodyHtml = '';
            if (cardData.card_type === 'carousel') {
                bodyHtml = `<div style="display:flex; gap:8px; overflow-x:auto; padding:4px 0;">`;
                if (Array.isArray(cardData.content)) {
                    cardData.content.forEach(item => {
                        bodyHtml += `<div style="min-width:120px; background:rgba(0,0,0,0.5); border:1px solid var(--border-cyan); padding:8px; border-radius:4px; font-size:10px;">${item}</div>`;
                    });
                } else {
                    bodyHtml += `<div>${cardData.content}</div>`;
                }
                bodyHtml += `</div>`;
            } else if (cardData.card_type === 'code_bug') {
                bodyHtml = `<pre style="background:#000; color:#38bdf8; padding:8px; font-family:monospace; font-size:10px; border-radius:4px; overflow-x:auto;">${cardData.content}</pre>`;
            } else if (cardData.card_type === 'email_draft') {
                bodyHtml = `<div style="background:rgba(0,0,0,0.3); padding:8px; border-left:3px solid var(--accent-cyan); font-size:11px;">${cardData.content}</div>`;
            } else {
                bodyHtml = `<div>${cardData.content}</div>`;
            }

            cardEl.innerHTML = `
                <div class="card-header-bar">
                    <span class="card-title-text">${cardData.title}</span>
                    <span class="card-type-badge">${cardData.card_type}</span>
                </div>
                <div class="card-body-content">${bodyHtml}</div>
                <div class="card-actions-bar">
                    <button class="action-btn" style="padding:3px 8px; font-size:9px;" onclick="dismissCard('${cardData.card_id}')">DISMISS</button>
                </div>
            `;

            grid.prepend(cardEl);
        }

        function dismissCard(cardId) {
            const el = document.getElementById(cardId);
            if (el) el.remove();
        }

        function toggleWidget(widgetId) {
            const el = document.getElementById(widgetId);
            if (el) el.classList.toggle('hidden');
        }

        function showWidget(widgetId) {
            const el = document.getElementById(widgetId);
            if (el) el.classList.remove('hidden');
        }

        function escapeHtml(s) {
            return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        // ---- IN-APP VIEWER (images + websites) ----
        function openViewer(data) {
            showWidget('viewerWidget');
            const title = document.getElementById('viewerTitle');
            const body = document.getElementById('viewerBody');
            title.textContent = (data.title || 'VIEWER').substring(0, 40).toUpperCase();
            if (data.kind === 'image') {
                body.innerHTML = `<img src="${escapeHtml(data.src)}" alt="image" onerror="this.parentNode.innerHTML='<div style=color:#f43f5e;padding:20px;font-size:11px>Image could not be loaded.</div>'">`;
            } else {
                body.innerHTML = `<iframe src="${escapeHtml(data.src)}" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>`;
            }
        }

        // ---- DOCUMENT EDITOR ----
        let docDirtyTimer = null;
        let docPreviewHtml = '';
        function showDocumentEditor(data) {
            showWidget('docWidget');
            document.getElementById('docTitle').textContent = (data.topic || 'DOCUMENT').substring(0, 40).toUpperCase();
            document.getElementById('docEditor').value = data.content || '';
            docPreviewHtml = data.preview_html || '';
            // If preview tab is active, refresh it.
            const pv = document.getElementById('docPreview');
            if (pv.style.display !== 'none') { pv.srcdoc = docPreviewHtml; }
        }
        function setDocTab(tab) {
            const editor = document.getElementById('docEditor');
            const preview = document.getElementById('docPreview');
            const tabEdit = document.getElementById('docTabEdit');
            const tabPreview = document.getElementById('docTabPreview');
            const inputRow = document.getElementById('docEditInstruction');
            if (tab === 'preview') {
                editor.style.display = 'none';
                preview.style.display = 'block';
                preview.srcdoc = docPreviewHtml || '<div style="color:#94a3b8;padding:20px;font-family:sans-serif">Generate or edit the document to see a styled preview.</div>';
                tabEdit.style.opacity = '0.6'; tabPreview.style.opacity = '1';
            } else {
                editor.style.display = 'block';
                preview.style.display = 'none';
                tabEdit.style.opacity = '1'; tabPreview.style.opacity = '0.6';
            }
        }
        function onDocEdited() {
            clearTimeout(docDirtyTimer);
            docDirtyTimer = setTimeout(() => {
                pywebview.api.update_document_from_ui(String(document.getElementById('docEditor').value));
            }, 600);
        }
        function submitDocEdit() {
            const inp = document.getElementById('docEditInstruction');
            const val = inp.value.trim();
            if (!val) return;
            inp.value = '';
            pywebview.api.edit_document_ui(String(val));
        }

        // ---- CUSTOMISABLE BOTTOM BAR ----
        // Master list of every available control. `on` is the default state.
        const BAR_BUTTONS = [
            { key:'chat',       icon:'💬',  title:'Terminal',        action:()=>toggleWidget('chatWidget') },
            { key:'telemetry',  icon:'📊',  title:'Telemetry',       action:()=>toggleWidget('telemetryWidget') },
            { key:'spotify',    icon:'🎵',  title:'Spotify',         action:()=>toggleWidget('spotifyWidget') },
            { key:'scratchpad', icon:'📝',  title:'Scratchpad',      action:()=>toggleWidget('scratchpadWidget') },
            { key:'todo',       icon:'☑️',  title:'Directives',      action:()=>toggleWidget('todoWidget') },
            { key:'viewer',     icon:'🖼️',  title:'Viewer',          action:()=>toggleWidget('viewerWidget') },
            { key:'doc',        icon:'📄',  title:'Document',        action:()=>toggleWidget('docWidget') },
            { key:'overlay',    icon:'🔳',  title:'Corner Overlay',  action:()=>pywebview.api.toggle_overlay() },
            { key:'sep1',       sep:true },
            { key:'mic',        icon:'🎙️',  title:'Mute Mic', id:'muteBtn', action:()=>toggleMute() },
            { key:'screenshot', icon:'📸',  title:'Screenshot',      action:()=>pywebview.api.take_screenshot() },
            { key:'sysinfo',    icon:'🩺',  title:'System Status',   action:()=>pywebview.api.report_system_status() },
            { key:'vision',     icon:'👁️',  title:'Vision',          action:()=>pywebview.api.capture_screen_vision() },
            { key:'autovision', icon:'👁️‍🗨️', title:'Auto Screen AI', id:'autoVisionBtn', action:()=>toggleContinuousVision() },
            { key:'stop',       icon:'⏹️',  title:'Stop',            action:()=>stopSpeech() },
            { key:'settings',   icon:'⚙️',  title:'Bar Settings',    action:()=>toggleWidget('settingsWidget') },
            { key:'shutdown',   icon:'❌',  title:'Shutdown', danger:true, always:true, action:()=>pywebview.api.close_app() },
        ];

        let barConfig = {};   // key -> bool, loaded from backend

        function renderBottomBar() {
            const bar = document.getElementById('bottomBar');
            bar.innerHTML = '';
            BAR_BUTTONS.forEach(b => {
                if (b.sep) {
                    // Only show separator if at least one visible button follows.
                    const sep = document.createElement('div');
                    sep.className = 'separator';
                    bar.appendChild(sep);
                    return;
                }
                const enabled = b.always || barConfig[b.key] !== false;
                if (!enabled) return;
                const btn = document.createElement('button');
                btn.className = 'hud-btn' + (b.danger ? ' danger' : '');
                if (b.id) btn.id = b.id;
                btn.title = b.title;
                btn.textContent = b.icon;
                btn.onclick = b.action;
                bar.appendChild(btn);
            });
        }

        function renderSettingsToggles() {
            const box = document.getElementById('settingsToggles');
            box.innerHTML = '';
            BAR_BUTTONS.filter(b => !b.sep && !b.always).forEach(b => {
                const row = document.createElement('label');
                row.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:11px;color:#e2e8f0;cursor:pointer;';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.checked = barConfig[b.key] !== false;
                cb.onchange = () => { barConfig[b.key] = cb.checked; renderBottomBar(); };
                row.appendChild(cb);
                row.appendChild(document.createTextNode(`${b.icon}  ${b.title}`));
                box.appendChild(row);
            });
        }

        function applyBarConfig(cfg) {
            barConfig = cfg || {};
            renderBottomBar();
            renderSettingsToggles();
        }

        function saveBarSettings() {
            pywebview.api.save_bar_config(JSON.stringify(barConfig));
            toggleWidget('settingsWidget');
        }

        let isMuted = false;
        async function toggleMute() {
            isMuted = !isMuted;
            const btn = document.getElementById('muteBtn');
            if (btn) {
                if (isMuted) {
                    btn.classList.add('active-mute');
                    btn.classList.remove('listening');
                } else {
                    btn.classList.remove('active-mute');
                }
            }
            if (isMuted) reactorContainer.classList.remove('listening', 'wake-triggered', 'active-speech');
            pywebview.api.set_mic_mute(isMuted);
        }

        let autoVisionActive = false;
        function toggleContinuousVision() {
            autoVisionActive = !autoVisionActive;
            const btn = document.getElementById('autoVisionBtn');
            if (btn) btn.classList.toggle('auto-vision-active', autoVisionActive);
            appendLog("System: Continuous Screen AI Monitoring " + (autoVisionActive ? "ENABLED." : "DISABLED."));
            pywebview.api.toggle_continuous_vision(autoVisionActive);
        }

        function setAutoVisionState(state) {
            autoVisionActive = state;
            const btn = document.getElementById('autoVisionBtn');
            if (btn) btn.classList.toggle('auto-vision-active', autoVisionActive);
        }

        function updateListeningUI(listening) {
            const btn = document.getElementById('muteBtn');
            if (!isMuted) {
                if (listening) {
                    if (btn) btn.classList.add('listening');
                    reactorContainer.classList.add('listening');
                    coreLabel.textContent = "LISTENING";
                } else {
                    if (btn) btn.classList.remove('listening');
                    reactorContainer.classList.remove('listening');
                    coreLabel.textContent = "JARVIS";
                }
            }
        }

        function updateWakeTriggerUI(triggered) {
            if (triggered) {
                reactorContainer.classList.add('wake-triggered');
                reactorContainer.classList.remove('listening');
                coreLabel.textContent = "ACTIVE";
            } else {
                reactorContainer.classList.remove('wake-triggered');
                coreLabel.textContent = "JARVIS";
            }
        }

        function updateSpeechAnimation(active) {
            if (active) {
                reactorContainer.classList.add('active-speech');
                coreLabel.textContent = "SPEAKING";
            } else {
                reactorContainer.classList.remove('active-speech', 'listening', 'wake-triggered');
                coreLabel.textContent = "JARVIS";
            }
        }

        async function stopSpeech() {
            pywebview.api.stop_speech();
            setAutoVisionState(false);
            updateSpeechAnimation(false);
        }

        function updateTelemetryUI(cpu, ram) {
            document.getElementById('cpuVal').textContent = cpu + '%';
            document.getElementById('cpuFill').style.width = cpu + '%';
            document.getElementById('ramVal').textContent = ram + '%';
            document.getElementById('ramFill').style.width = ram + '%';
        }

        function updateSpotifyUI(data) {
            const trackEl = document.getElementById('spotifyTrack');
            const artistEl = document.getElementById('spotifyArtist');
            const artEl = document.getElementById('spotifyAlbumArt');
            
            if (data && data.is_active) {
                trackEl.textContent = data.track_name || 'Unknown Track';
                artistEl.textContent = data.artist_name || 'Unknown Artist';
                if (data.album_art) {
                    artEl.src = data.album_art;
                    artEl.style.display = 'block';
                }
            } else if (data && !data.configured) {
                trackEl.textContent = "Spotify API Unconfigured";
                artistEl.textContent = "Check Client ID & Secret";
                artEl.style.display = 'none';
            } else {
                trackEl.textContent = "Spotify API Idle";
                artistEl.textContent = "No track currently playing";
                artEl.style.display = 'none';
            }
        }

        function saveScratchpad() {
            const text = document.getElementById('scratchpadInput').value;
            pywebview.api.save_scratchpad_note(String(text));
        }

        function addTodoItem() {
            const input = document.getElementById('todoInput');
            const task = input.value.trim();
            if (!task) return;
            input.value = '';
            pywebview.api.add_todo(String(task));
        }

        function renderTodoList(todos) {
            const container = document.getElementById('todoListContainer');
            if (!container) return;
            container.innerHTML = '';
            todos.forEach((item, idx) => {
                const div = document.createElement('div');
                div.className = 'todo-item';
                div.innerHTML = `<span>${item}</span><button class="action-btn" style="padding:2px 6px;" onclick="pywebview.api.remove_todo(${idx})">✓</button>`;
                container.appendChild(div);
            });
        }

        let activeWidget = null;
        let startX, startY, initialX, initialY;
        let highestZ = 10;

        document.querySelectorAll('.widget-header').forEach(header => {
            header.addEventListener('mousedown', (e) => {
                if (e.target.classList.contains('close-widget-btn')) return;
                activeWidget = header.closest('.widget');
                highestZ++;
                activeWidget.style.zIndex = highestZ;

                startX = e.clientX;
                startY = e.clientY;
                const rect = activeWidget.getBoundingClientRect();
                initialX = rect.left;
                initialY = rect.top;
                
                activeWidget.style.position = 'absolute';
                activeWidget.style.left = initialX + 'px';
                activeWidget.style.top = initialY + 'px';
                e.preventDefault();
            });
        });

        document.addEventListener('mousemove', (e) => {
            if (!activeWidget) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            activeWidget.style.left = (initialX + dx) + 'px';
            activeWidget.style.top = (initialY + dy) + 'px';
        });

        document.addEventListener('mouseup', () => { activeWidget = null; });
    </script>
</body>
</html>
"""


OVERLAY_HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><style>
  * { margin:0; padding:0; box-sizing:border-box; font-family:'Consolas',monospace; user-select:none; }
  html, body { background:transparent; height:100%; overflow:hidden; }
  .wrap { width:100%; height:100%; display:flex; align-items:center; justify-content:center;
          -webkit-app-region: drag; position:relative; }
  .reactor { width:150px; height:150px; position:relative; display:flex; align-items:center; justify-content:center;
             cursor:pointer; }
  .ring { position:absolute; border-radius:50%; }
  .r1 { width:150px; height:150px; border:1px solid rgba(0,229,255,0.3); animation:spin 20s linear infinite; }
  .r2 { width:118px; height:118px; border:2px dashed rgba(0,229,255,0.4); animation:spin 15s linear infinite reverse; }
  .r3 { width:92px; height:92px; border:3px solid #00e5ff;
        box-shadow:0 0 22px rgba(0,229,255,0.6), inset 0 0 22px rgba(0,229,255,0.6); animation:spin 10s linear infinite; }
  .core { width:66px; height:66px; border-radius:50%; background:rgba(9,13,22,0.92);
          border:2px solid #00e5ff; display:flex; align-items:center; justify-content:center;
          font-size:11px; font-weight:bold; letter-spacing:1px; color:#00e5ff;
          box-shadow:0 0 26px rgba(0,229,255,0.5); text-shadow:0 0 8px #00e5ff; }
  .core.speaking { border-color:#ff2e63; color:#ff2e63; box-shadow:0 0 30px rgba(255,46,99,0.7); text-shadow:0 0 8px #ff2e63; }
  .core.listening { border-color:#1ed760; color:#1ed760; box-shadow:0 0 30px rgba(30,215,96,0.7); }
  @keyframes spin { to { transform:rotate(360deg); } }
  .x { position:absolute; top:2px; right:8px; color:rgba(0,229,255,0.6); font-size:14px; cursor:pointer;
       -webkit-app-region:no-drag; }
  .x:hover { color:#ff2e63; }
</style></head>
<body>
  <div class="wrap">
    <span class="x" onclick="pywebview.api.toggle_overlay()">&times;</span>
    <div class="reactor" title="Click: Main UI  ·  Double-click: Vision" ondblclick="pywebview.api.capture_screen_vision()" onclick="pywebview.api.restore_main()">
      <div class="ring r1"></div>
      <div class="ring r2"></div>
      <div class="ring r3"></div>
      <div class="core" id="ovCore">JARVIS</div>
    </div>
  </div>
  <script>
    function setState(s){ var c=document.getElementById('ovCore'); c.className='core'+(s?(' '+s):''); }
  </script>
</body>
</html>
"""


class JarvisApp:
    def __init__(self):
        log_debug("Initializing J.A.R.V.I.S. Autonomous Neural Engine...")
        self.memory_lock = threading.Lock()
        self.memory = self.load_memory()
        self.window = None
        self.ui_ready = False
        self.mic_muted = False
        self.is_speaking = False
        self.continuous_vision_active = False
        self.tray_icon = None
        self.sp = None
        self.hud_overlay = None
        self.current_doc = None
        self._spotify_device_cache = None
        self._spotify_device_cache_ts = 0

        self.sapi_speaker = None
        self.current_tts_proc = None
        self.speech_queue = queue.Queue()

        self.setup_system_tray()

        threading.Thread(target=self._tts_worker, daemon=True).start()
        threading.Thread(target=self.telemetry_loop, daemon=True).start()
        threading.Thread(target=self.spotify_loop, daemon=True).start()
        threading.Thread(target=self._continuous_vision_worker, daemon=True).start()

        def delayed_audio_start():
            time.sleep(1.0)
            threading.Thread(target=self.voice_listener_loop, daemon=True).start()

        threading.Thread(target=delayed_audio_start, daemon=True).start()
        log_debug("J.A.R.V.I.S. initialization complete.")

    def setup_system_tray(self):
        if not TRAY_AVAILABLE:
            log_debug("pystray not installed; skipping system tray icon.")
            return

        def on_show_hud(icon, item):
            if self.window:
                try:
                    self.window.restore()
                    self.window.focus()
                except Exception as e:
                    log_debug(f"Tray restore error: {e}")

        def on_toggle_overlay(icon, item):
            self.toggle_overlay()

        def on_shutdown(icon, item):
            self.close_app()

        try:
            img = Image.new('RGB', (64, 64), color=(5, 7, 11))
            d = ImageDraw.Draw(img)
            d.ellipse((8, 8, 56, 56), outline=(0, 243, 255), width=3)
            d.ellipse((20, 20, 44, 44), fill=(0, 243, 255))

            menu = pystray.Menu(
                pystray.MenuItem('Show JARVIS HUD', on_show_hud, default=True),
                pystray.MenuItem('Toggle Corner HUD Overlay', on_toggle_overlay),
                pystray.MenuItem('Shutdown Core', on_shutdown)
            )
            self.tray_icon = pystray.Icon("JARVIS", img, "J.A.R.V.I.S. Core", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            log_debug("System Tray icon initialized successfully.")
        except Exception as e:
            log_debug(f"Failed to initialize System Tray: {e}")

    # --- IN-APP DYNAMIC CARDS ENGINE (JarvisInAppCard) ---
    def instantiate_card(self, title, card_type, content):
        card_id = f"card_{int(time.time() * 1000)}"
        card = JarvisInAppCard(card_id, title, card_type, content)
        card_json = json.dumps(card.to_dict())
        self.run_js(f"renderInAppCard({card_json})")
        return card_id

    # --- OS APPLICATION CONTROL (launch_external_app) ---
    def launch_external_app(self, target, args=None):
        try:
            target_str = str(target).strip()
            # Handle URLs or Web Pages via Webbrowser
            if target_str.startswith(("http://", "https://", "www.")) or target_str.endswith((".com", ".org", ".net", ".io")):
                if not target_str.startswith(("http://", "https://")):
                    target_str = "https://" + target_str
                threading.Thread(target=lambda: webbrowser.open(target_str), daemon=True).start()
                self.safe_log(f"OS App Control: Launched web browser -> {target_str}")
                return True

            app_map = {
                "chrome": "chrome",
                "google chrome": "chrome",
                "browser": "chrome",
                "notepad": "notepad",
                "calculator": "calc",
                "calc": "calc",
                "cmd": "cmd",
                "command prompt": "cmd",
                "terminal": "wt",
                "file explorer": "explorer",
                "explorer": "explorer",
                "vs code": "code",
                "vscode": "code",
                "code": "code",
                "edge": "msedge",
                "task manager": "taskmgr"
            }

            exe = app_map.get(target_str.lower(), target_str)
            
            if focus_window_by_title(target_str):
                self.safe_log(f"OS App Control: Focused active window for {target_str}")
                return True

            cmd = [exe]
            if args:
                if isinstance(args, list):
                    cmd.extend(args)
                else:
                    cmd.append(str(args))

            creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if exe.endswith(".exe") else 0
            
            if sys.platform == 'win32' and not exe.startswith("ms-"):
                subprocess.Popen(cmd, shell=True, creationflags=creationflags)
            else:
                subprocess.Popen(cmd)

            self.safe_log(f"OS App Control: Launched process -> {exe}")
            return True
        except Exception as e:
            log_debug(f"launch_external_app error: {e}")
            self.safe_log(f"OS App Control Error launching: {target}")
            return False

    # --- CORNER HUD OVERLAY CONTROL ---
    # Implemented as a small frameless always-on-top pywebview window. Using the
    # same GUI toolkit as the main window avoids the Qt/pywebview main-thread
    # conflict that made the old PyQt overlay unstable.
    def toggle_overlay(self):
        # If it already exists, destroy it (toggle off).
        if self.hud_overlay is not None:
            try:
                self.hud_overlay.destroy()
            except Exception as e:
                log_debug(f"Overlay destroy error: {e}")
            self.hud_overlay = None
            self.safe_log("Corner HUD overlay closed.")
            return

        try:
            screen_w, screen_h = 1920, 1080
            try:
                screen_w, screen_h = pyautogui.size()
            except Exception:
                pass
            w, h = 170, 170
            x = screen_w - w - 30
            y = screen_h - h - 80

            self.hud_overlay = webview.create_window(
                "JARVIS Overlay",
                html=OVERLAY_HTML,
                js_api=JarvisAPI(self),
                width=w, height=h, x=x, y=y,
                frameless=True, on_top=True, easy_drag=True,
                transparent=True,
                background_color="#05070b",
                resizable=False,
            )
            self.safe_log("Corner HUD overlay opened.")
        except Exception as e:
            log_debug(f"Overlay create error: {e}\n{traceback.format_exc()}")
            self.speak("I could not open the corner overlay, Sir.")

    def run_js(self, script_str):
        if self.window and self.ui_ready:
            try:
                self.window.evaluate_js(script_str)
            except Exception as e:
                log_debug(f"JS Eval Error: {e}")

    def run_overlay_js(self, script_str):
        if self.hud_overlay is not None:
            try:
                self.hud_overlay.evaluate_js(script_str)
            except Exception as e:
                log_debug(f"Overlay JS Eval Error: {e}")

    def safe_log(self, text, is_user=False):
        safe_txt = json.dumps(str(text))
        self.run_js(f'appendLog({safe_txt}, {str(is_user).lower()})')

    def load_memory(self):
        with self.memory_lock:
            if os.path.exists(MEMORY_FILE):
                try:
                    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        data.setdefault("chat_history", [])      # recent rolling context
                        data.setdefault("full_transcript", [])   # EVERY message, timestamped, forever
                        data.setdefault("todos", [])
                        data.setdefault("scratchpad", "")
                        data.setdefault("custom_macros", {})
                        data.setdefault("contacts", {})
                        data.setdefault("reminders", [])
                        data.setdefault("facts", [])             # long-term "remember that ..." facts
                        # Merge saved contacts into the live address book.
                        for k, v in data.get("contacts", {}).items():
                            CONTACTS[k] = v
                        return data
                except Exception as e:
                    log_debug(f"Error loading memory file: {e}\n{traceback.format_exc()}")
            return {"chat_history": [], "full_transcript": [], "todos": ["System Online"],
                    "scratchpad": "", "custom_macros": {}, "contacts": {},
                    "reminders": [], "facts": []}

    def remember_message(self, role, content):
        """Append to the permanent transcript (kept forever) and rolling context."""
        entry = {"role": role, "content": content,
                 "ts": datetime.datetime.now().isoformat(timespec="seconds")}
        self.memory.setdefault("full_transcript", []).append(entry)
        # Keep the full transcript from growing without bound on disk but keep a lot.
        if len(self.memory["full_transcript"]) > 5000:
            self.memory["full_transcript"] = self.memory["full_transcript"][-5000:]

    def save_memory(self):
        with self.memory_lock:
            try:
                with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.memory, f, indent=4)
            except Exception as e:
                log_debug(f"Error saving memory file: {e}\n{traceback.format_exc()}")

    # --- SPOTIFY CONTROLLER ---
    def get_spotify_client(self, allow_browser=False):
        if not SPOTIPY_AVAILABLE:
            return None
        if self.sp is not None:
            return self.sp
        
        client_id = os.getenv("SPOTIPY_CLIENT_ID", SPOTIPY_CLIENT_ID)
        client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", SPOTIPY_CLIENT_SECRET)
        
        if not client_id or not client_secret or "YOUR_SPOTIPY" in client_id:
            return None
        
        try:
            scope = "user-read-currently-playing user-read-playback-state user-modify-playback-state"
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=SPOTIPY_REDIRECT_URI,
                scope=scope,
                open_browser=allow_browser,
                cache_path=SPOTIFY_CACHE_FILE
            )
            
            cached_token = auth_manager.get_cached_token()
            if cached_token:
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
                return self.sp

            if allow_browser:
                self.speak("Opening browser once to authorize Spotify API, Sir.")
                token_info = auth_manager.get_access_token(as_dict=False)
                if token_info:
                    self.sp = spotipy.Spotify(auth_manager=auth_manager)
                    return self.sp
            return None
        except Exception as e:
            log_debug(f"Spotify OAuth exception: {e}")
            return None

    def get_active_spotify_device(self, sp):
        try:
            devices_res = sp.devices()
            devices = devices_res.get("devices", []) if devices_res else []
            for d in devices:
                if d.get("is_active"):
                    return d["id"]
            if devices:
                return devices[0]["id"]

            self.speak("Launching Spotify desktop client, Sir.")
            self.launch_external_app("spotify")
            time.sleep(3.0)

            devices_res = sp.devices()
            devices = devices_res.get("devices", []) if devices_res else []
            if devices:
                return devices[0]["id"]
        except Exception as e:
            log_debug(f"Device acquisition error: {e}")
        return None

    def spotify_loop(self):
        init_thread_com()
        while True:
            try:
                sp = self.get_spotify_client(allow_browser=False)
                if sp:
                    current = sp.current_playback()
                    if current and current.get("item"):
                        item = current["item"]
                        track_name = item.get("name", "Unknown Track")
                        artists = ", ".join([a["name"] for a in item.get("artists", [])])
                        images = item.get("album", {}).get("images", [])
                        art_url = images[0]["url"] if images else ""
                        is_playing = current.get("is_playing", False)
                        
                        data = {
                            "configured": True,
                            "is_active": True,
                            "is_playing": is_playing,
                            "track_name": track_name,
                            "artist_name": artists,
                            "album_art": art_url
                        }
                        self.run_js(f"updateSpotifyUI({json.dumps(data)})")
                    else:
                        data = {"configured": True, "is_active": False}
                        self.run_js(f"updateSpotifyUI({json.dumps(data)})")
                else:
                    data = {"configured": False, "is_active": False}
                    self.run_js(f"updateSpotifyUI({json.dumps(data)})")
            except Exception as e:
                log_debug(f"Spotify loop poll exception: {e}")
            time.sleep(4)

    def spotify_play_track(self, query):
        sp = self.get_spotify_client(allow_browser=True)
        if not sp:
            self.speak("Spotify API is not authenticated.")
            return True

        device_id = self.get_active_spotify_device(sp)
        if not device_id:
            self.speak("Unable to detect active Spotify device.")
            return True

        try:
            results = sp.search(q=query, limit=1, type="track")
            tracks = results.get("tracks", {}).get("items", [])
            
            if tracks:
                track = tracks[0]
                sp.start_playback(device_id=device_id, uris=[track["uri"]])
                self.speak(f"Playing {track['name']} by {track['artists'][0]['name']} via Spotify API.")
            else:
                self.speak(f"Could not find {query} on Spotify.")
        except spotipy.SpotifyException as e:
            log_debug(f"Spotify Playback API Exception: {e}")
            if "PREMIUM_REQUIRED" in str(e):
                self.speak("Spotify API playback requires a Spotify Premium subscription, Sir.")
            else:
                self.speak("Spotify Web API rejected playback command.")
        except Exception as e:
            log_debug(f"Spotify play error: {e}")
            self.speak("Failed to start Spotify playback.")
        return True

    def spotify_toggle_play(self, force_play=False):
        sp = self.get_spotify_client(allow_browser=True)
        if not sp:
            self.speak("Spotify API is not authenticated.")
            return True

        device_id = self.get_active_spotify_device(sp)
        if not device_id:
            self.speak("No active Spotify device found.")
            return True

        try:
            current = sp.current_playback()
            if current and current.get("is_playing") and not force_play:
                sp.pause_playback(device_id=device_id)
                self.speak("Playback paused via Spotify API.")
            else:
                sp.start_playback(device_id=device_id)
                self.speak("Resuming playback via Spotify API.")
        except spotipy.SpotifyException as e:
            log_debug(f"Spotify toggle error: {e}")
            self.speak("Spotify API playback toggle failed.")
        return True

    def spotify_pause(self):
        sp = self.get_spotify_client(allow_browser=True)
        if not sp:
            self.speak("Spotify API is not authenticated.")
            return True
        try:
            sp.pause_playback()
            self.speak("Playback paused via Spotify API.")
        except spotipy.SpotifyException:
            self.speak("Failed to pause via Spotify API.")
        return True

    def spotify_next(self):
        sp = self.get_spotify_client(allow_browser=True)
        if not sp:
            self.speak("Spotify API is not authenticated.")
            return True
        try:
            sp.next_track()
            self.speak("Track skipped via Spotify API.")
        except spotipy.SpotifyException:
            self.speak("Failed to skip track via Spotify API.")
        return True

    def spotify_previous(self):
        sp = self.get_spotify_client(allow_browser=True)
        if not sp:
            self.speak("Spotify API is not authenticated.")
            return True
        try:
            sp.previous_track()
            self.speak("Playing previous track via Spotify API.")
        except spotipy.SpotifyException:
            self.speak("Failed to play previous track via Spotify API.")
        return True

    def spotify_announce_current(self):
        sp = self.get_spotify_client(allow_browser=True)
        if not sp:
            self.speak("Spotify API is not authenticated.")
            return True
        try:
            current = sp.current_playback()
            if current and current.get("item"):
                item = current["item"]
                name = item.get("name")
                artist = item.get("artists", [{}])[0].get("name")
                self.speak(f"Currently playing {name} by {artist}.")
            else:
                self.speak("Nothing is currently playing on Spotify, Sir.")
        except Exception as e:
            log_debug(f"Spotify current status error: {e}")
            self.speak("Unable to query Spotify API status.")
        return True

    def spotify_toggle_play_js(self):
        threading.Thread(target=self.spotify_toggle_play, daemon=True).start()

    def spotify_next_js(self):
        threading.Thread(target=self.spotify_next, daemon=True).start()

    def spotify_previous_js(self):
        threading.Thread(target=self.spotify_previous, daemon=True).start()

    # --- TTS WORKER ---
    @staticmethod
    def _score_voice(desc):
        """Rank a voice for 'JARVIS-ness': British male, deep, natural.
        Higher score = better. Negative = disqualified."""
        d = desc.lower()
        if any(bad in d for bad in ["female", "hazel", "zira", "susan", "linda", "heera"]):
            return -1
        score = 0
        # Premium modern British male voices ship with Windows 10/11.
        for name, pts in [("ryan", 60), ("george", 55), ("guy", 45), ("james", 40),
                          ("oliver", 40), ("thomas", 35), ("daniel", 35), ("brian", 30)]:
            if name in d:
                score += pts
        if any(k in d for k in ["en-gb", "united kingdom", "british", " gb ", "(gb)"]):
            score += 25
        if "uk" in d:
            score += 15
        if "male" in d:
            score += 10
        if "desktop" not in d:   # mobile/neural voices tend to be nicer
            score += 5
        return score

    def _tts_worker(self):
        init_thread_com()

        if HAS_PYTHONCOM:
            try:
                import win32com.client
                self.sapi_speaker = win32com.client.Dispatch("SAPI.SpVoice")
                self.sapi_speaker.Rate = max(-3, min(3, VOICE_RATE))
                # Pick the highest-scoring available voice rather than the first match.
                best_v, best_score = None, 0
                for v in self.sapi_speaker.GetVoices():
                    s = self._score_voice(v.GetDescription())
                    if s > best_score:
                        best_v, best_score = v, s
                if best_v is not None:
                    self.sapi_speaker.Voice = best_v
                    log_debug(f"Selected SAPI voice: {best_v.GetDescription()}")
            except Exception as e:
                log_debug(f"SAPI voice setup warning: {e}")

        pyttsx_engine = None
        if not self.sapi_speaker and TTS_AVAILABLE:
            try:
                pyttsx_engine = pyttsx3.init()
                pyttsx_engine.setProperty('rate', 178)   # measured, butler-like cadence
                best_id, best_score = None, 0
                for v in pyttsx_engine.getProperty('voices'):
                    s = self._score_voice(f"{v.id} {v.name}")
                    if s > best_score:
                        best_id, best_score = v.id, s
                if best_id:
                    pyttsx_engine.setProperty('voice', best_id)
            except Exception as e:
                log_debug(f"pyttsx3 setup warning: {e}")

        while True:
            try:
                text = self.speech_queue.get()
                if text:
                    self.is_speaking = True
                    self.run_js("updateSpeechAnimation(true)")
                    self.run_overlay_js("setState('speaking')")

                    if self.sapi_speaker:
                        self.sapi_speaker.Speak(text, 1)
                        while self.sapi_speaker.Status.RunningState == 2 and self.is_speaking:
                            time.sleep(0.04)
                    elif pyttsx_engine:
                        pyttsx_engine.say(text)
                        pyttsx_engine.runAndWait()
                    else:
                        clean_text = str(text).replace('"', '').replace("'", "")
                        cmd = (
                            'Add-Type -AssemblyName System.Speech; '
                            '$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                            '$synth.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Male, [System.Speech.Synthesis.VoiceAge]::Adult, 0, [System.Globalization.CultureInfo]::GetCultureInfo("en-GB")); '
                            '$synth.Rate = 0; '
                            f'$synth.Speak("{clean_text}");'
                        )
                        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                        self.current_tts_proc = subprocess.Popen(["powershell", "-Command", cmd], creationflags=creationflags)
                        while self.current_tts_proc and self.current_tts_proc.poll() is None and self.is_speaking:
                            time.sleep(0.04)
                        self.current_tts_proc = None

                    self.run_js("updateSpeechAnimation(false)")
                    self.run_overlay_js("setState('')")
                    time.sleep(0.05)
                    self.is_speaking = False
                self.speech_queue.task_done()
            except Exception as e:
                log_debug(f"TTS Worker Exception: {e}")
                self.run_js("updateSpeechAnimation(false)")
                self.is_speaking = False
                time.sleep(0.05)

    def speak(self, text):
        if not self.mic_muted:
            self.speech_queue.put(str(text))

    def stop_speech(self):
        self.is_speaking = False
        self.continuous_vision_active = False
        self.run_js("setAutoVisionState(false)")
        
        while not self.speech_queue.empty():
            try:
                self.speech_queue.get_nowait()
                self.speech_queue.task_done()
            except Exception:
                break
                
        if self.sapi_speaker:
            try:
                self.sapi_speaker.Speak("", 2)
            except Exception as e:
                log_debug(f"Error purging SAPI stream: {e}")

        if self.current_tts_proc and self.current_tts_proc.poll() is None:
            try:
                self.current_tts_proc.kill()
            except Exception:
                pass
            self.current_tts_proc = None

        self.run_js("updateSpeechAnimation(false)")

    def set_mic_mute(self, muted):
        self.mic_muted = bool(muted)

    def set_ui_ready(self):
        self.ui_ready = True
        def post_ready():
            time.sleep(0.2)
            # Apply the saved bottom-bar layout.
            bar_cfg = CONFIG.get("ui", {}).get("bottom_bar", {})
            self.run_js(f"applyBarConfig({json.dumps(bar_cfg)})")
            self.sync_todos_to_ui()
            # Restore the scratchpad contents.
            note = self.memory.get("scratchpad", "")
            if note:
                self.run_js(f"var s=document.getElementById('scratchpadInput'); if(s) s.value={json.dumps(note)};")
            for msg in self.memory.get("chat_history", [])[-10:]:
                self.safe_log(f"{msg['role'].upper()}: {msg['content']}")
            self.safe_log(
                "Ready. Try: 'create a PDF on black holes', 'show me how to tie a tie', "
                "'send an email to <name> about <topic>', 'open bbc.com in jarvis', "
                "'remember that ...', 'what's the weather in Paris'."
            )
            if CONFIG.get("assistant", {}).get("speak_greeting", True):
                self.speak(f"J.A.R.V.I.S. online and at your service, {USER_TITLE}.")
        threading.Thread(target=post_ready, daemon=True).start()

    def save_bar_config(self, cfg_json):
        try:
            cfg = json.loads(cfg_json) if isinstance(cfg_json, str) else dict(cfg_json)
            CONFIG.setdefault("ui", {})["bottom_bar"] = cfg
            save_config(CONFIG)
            self.safe_log("Control bar layout saved.")
        except Exception as e:
            log_debug(f"save_bar_config error: {e}")

    def restore_main(self):
        if self.window:
            try:
                self.window.restore()
                self.window.show()
            except Exception as e:
                log_debug(f"restore_main error: {e}")

    def telemetry_loop(self):
        init_thread_com()
        while True:
            try:
                if self.window and self.ui_ready:
                    cpu = psutil.cpu_percent(interval=None)
                    ram = psutil.virtual_memory().percent
                    self.run_js(f"updateTelemetryUI({cpu}, {ram})")
            except Exception:
                pass
            time.sleep(2)

    # --- VOICE LISTENER ---
    def voice_listener_loop(self):
        init_thread_com()
        if not SPEECH_AVAILABLE:
            return

        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.25
        recognizer.non_speaking_duration = 0.15
        recognizer.phrase_threshold = 0.1
        recognizer.dynamic_energy_threshold = False

        while True:
            if not self.ui_ready or self.mic_muted:
                time.sleep(0.1)
                continue

            # While JARVIS is speaking, keep a light ear open for an interrupt.
            if self.is_speaking:
                try:
                    with sr.Microphone() as source:
                        audio = recognizer.listen(source, timeout=1.5, phrase_time_limit=2.5)
                    heard = recognizer.recognize_google(audio).lower().strip()
                    if WAKE_WORD in heard or any(w in heard for w in ["stop", "quiet", "shut up", "enough", "cancel"]):
                        self.stop_speech()
                        self.safe_log("Speech interrupted by voice.", is_user=True)
                        # If they said "jarvis <command>", act on the trailing command.
                        if WAKE_WORD in heard:
                            trailing = heard.split(WAKE_WORD, 1)[1].strip()
                            if trailing:
                                self.process_command_backend(trailing)
                except Exception:
                    pass
                continue

            try:
                with sr.Microphone() as source:
                    audio = recognizer.listen(source, timeout=2.0, phrase_time_limit=4.5)

                try:
                    raw_text = recognizer.recognize_google(audio).lower().strip()
                    if WAKE_WORD in raw_text:
                        self.run_js("updateWakeTriggerUI(true)")
                        parts = raw_text.split(WAKE_WORD, 1)
                        cmd = parts[1].strip() if len(parts) > 1 else ""

                        if cmd:
                            time.sleep(0.1)
                            self.run_js("updateWakeTriggerUI(false)")
                            self.safe_log(f"Voice: \"{cmd}\"", is_user=True)
                            self.process_command_backend(cmd)
                        else:
                            self.run_js("updateListeningUI(true)")
                            self.speak("At your service, Sir.")
                            while self.is_speaking:
                                time.sleep(0.02)

                            try:
                                with sr.Microphone() as source2:
                                    followup_audio = recognizer.listen(source2, timeout=3.5, phrase_time_limit=5)
                                    followup_cmd = recognizer.recognize_google(followup_audio).lower().strip()

                                if followup_cmd:
                                    self.safe_log(f"Voice: \"{followup_cmd}\"", is_user=True)
                                    self.process_command_backend(followup_cmd)
                            except (sr.UnknownValueError, sr.WaitTimeoutError):
                                pass
                            finally:
                                self.run_js("updateListeningUI(false)")
                                self.run_js("updateWakeTriggerUI(false)")
                except (sr.UnknownValueError, sr.RequestError):
                    pass
            except sr.WaitTimeoutError:
                pass
            except Exception as e:
                log_debug(f"Voice listener exception: {e}")
                self.run_js("updateListeningUI(false)")
                self.run_js("updateWakeTriggerUI(false)")
                time.sleep(1.0)

    # --- EMAIL ENGINE ---
    def draft_email_with_ai(self, instruction):
        """Ask the local LLM to write an email body from a natural-language instruction.
        Returns (subject, body). Falls back to a simple template if the model is unavailable."""
        subject = None
        body = None
        try:
            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": EMAIL_WRITER_PROMPT},
                    {"role": "user", "content":
                        f"Write an email for this request: {instruction}\n"
                        "Respond as JSON with exactly two keys: \"subject\" and \"body\"."},
                ],
                "stream": False,
                "format": "json",
                "options": {"num_predict": 400, "temperature": 0.6},
            }
            res = requests.post(OLLAMA_URL, json=payload, timeout=45)
            if res.status_code == 200:
                content = res.json().get("message", {}).get("content", "").strip()
                try:
                    parsed = json.loads(content)
                    subject = (parsed.get("subject") or "").strip()
                    body = (parsed.get("body") or "").strip()
                except (json.JSONDecodeError, TypeError):
                    body = content
        except Exception as e:
            log_debug(f"AI email draft error: {e}")

        if not body:
            body = f"Hello,\n\n{instruction}\n\nBest regards"
        if not subject:
            # Derive a short subject from the instruction.
            subject = instruction.strip().capitalize()
            subject = (subject[:60] + "...") if len(subject) > 60 else subject
            if not subject:
                subject = "A quick note"
        return subject, body

    def compose_and_send_email(self, recipient=None, instruction=None,
                               subject=None, body=None, auto_send=None):
        """High-level entry point. Resolves the recipient, drafts the email with AI if
        needed, previews it as an in-app card, then dispatches via the configured mode."""
        if auto_send is None:
            auto_send = EMAIL_AUTO_SEND

        to_addr = resolve_contact(recipient, CONTACTS) if recipient else None
        if not to_addr:
            # Maybe the instruction itself contains an address.
            to_addr = extract_email_address(instruction or "")
        if not to_addr:
            to_addr = resolve_contact("me", CONTACTS)
            if to_addr:
                self.speak("No recipient was specified, so I'll address this to you, Sir.")

        if not to_addr:
            self.speak("I could not determine a recipient email address, Sir.")
            self.safe_log("Email aborted: no recipient resolved.")
            return False

        # Draft content if not fully provided.
        if not body:
            subject, body = self.draft_email_with_ai(instruction or "a brief message")
        elif not subject:
            subject = "A message from J.A.R.V.I.S."

        # Show a preview card in the HUD.
        preview = f"To: {to_addr}\nSubject: {subject}\n\n{body}"
        self.instantiate_card("Email Draft", "email_draft", preview)
        self.safe_log(f"Email drafted to {to_addr} | Subject: {subject}")

        if EMAIL_MODE == "smtp":
            return self.send_email_smtp(to_addr, subject, body)
        return self.send_email_browser(to_addr, subject, body, auto_send=auto_send)

    def send_email_browser(self, to_addr, subject, body, auto_send=True):
        """Open Gmail's compose window in Chrome with fields pre-filled, then optionally send.

        Uses Gmail's compose URL to place recipient/subject/body reliably (no fragile
        field-tabbing), then presses Ctrl+Enter to send, which is Gmail's send shortcut."""
        try:
            self.speak("Opening Gmail in Chrome to compose your email, Sir.")
            params = urllib.parse.urlencode({
                "view": "cm", "fs": "1",
                "to": to_addr, "su": subject, "body": body,
            })
            compose_url = "https://mail.google.com/mail/?" + params

            chrome = find_chrome_path()
            opened = False
            if chrome:
                try:
                    subprocess.Popen([chrome, "--new-window", compose_url])
                    opened = True
                except Exception as e:
                    log_debug(f"Chrome launch failed, falling back: {e}")
            if not opened:
                # Fall back to the default browser if Chrome isn't found.
                webbrowser.open(compose_url)

            if not auto_send:
                self.speak("I've drafted the email in Gmail. Review it and press Ctrl+Enter to send, Sir.")
                self.safe_log(f"Email prepared in browser for {to_addr} (manual send).")
                return True

            # Wait for Gmail compose to load, then send with Ctrl+Enter.
            time.sleep(6.0)
            focus_window_by_title("Gmail")
            # Click into the body area to ensure focus is inside the compose window.
            pyautogui.hotkey('ctrl', 'enter')
            time.sleep(0.5)
            self.speak("Email sent through Gmail, Sir.")
            self.safe_log(f"Email dispatched via Gmail (browser) to {to_addr}.")
            return True
        except Exception as e:
            log_debug(f"Browser email error: {e}\n{traceback.format_exc()}")
            self.speak("I was unable to complete the browser email flow, Sir.")
            self.safe_log(f"Browser email failed: {e}")
            return False

    def send_email_smtp(self, to_addr, subject, body):
        """Silent background send via SMTP (requires configured credentials)."""
        if not EMAIL_ADDRESS or EMAIL_ADDRESS == "YOUR_EMAIL@gmail.com":
            self.speak("SMTP email credentials have not been configured, Sir. Switching to browser mode.")
            self.safe_log("SMTP unconfigured; using browser fallback.")
            return self.send_email_browser(to_addr, subject, body, auto_send=EMAIL_AUTO_SEND)
        try:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_ADDRESS
            msg['To'] = to_addr
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_addr, msg.as_string())
            server.quit()

            self.speak("Email transmitted successfully, Sir.")
            self.safe_log(f"Email successfully sent to {to_addr}")
            return True
        except Exception as e:
            log_debug(f"SMTP transmission error: {e}")
            self.speak("Failed to transmit email via SMTP, Sir.")
            self.safe_log(f"SMTP Transmission Failed: {e}")
            return False

    # Backwards-compatible shim (older callers used send_email(subject, body)).
    def send_email(self, subject, body, to_addr=None):
        to_addr = to_addr or resolve_contact("me", CONTACTS)
        if EMAIL_MODE == "smtp":
            return self.send_email_smtp(to_addr, subject, body)
        return self.send_email_browser(to_addr, subject, body, auto_send=EMAIL_AUTO_SEND)

    # --- CONTINUOUS SCREEN ANALYSIS & AUTONOMOUS MOUSE AGENT ---
    def toggle_continuous_vision(self, active_state):
        self.continuous_vision_active = bool(active_state)
        if self.continuous_vision_active:
            self.speak("Continuous screen monitoring active, Sir.")
        else:
            self.speak("Continuous screen monitoring deactivated.")

    def _continuous_vision_worker(self):
        init_thread_com()
        screen_w, screen_h = pyautogui.size()

        while True:
            if not self.continuous_vision_active or self.is_speaking:
                time.sleep(0.5)
                continue

            try:
                screenshot = ImageGrab.grab(all_screens=True)
                screenshot.thumbnail((768, 768))
                buf = io.BytesIO()
                screenshot.save(buf, format='JPEG', quality=75)
                img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

                prompt = (
                    f"You are driving a Windows cursor on a screen with total resolution {screen_w}x{screen_h}. "
                    "Analyze the visual UI. If you see a key window or action needed, reply with EXACT format: "
                    "'ACTION: CLICK(x, y)' or 'ACTION: MOVE(x, y)' or 'ACTION: NONE'. "
                    "Example: ACTION: CLICK(500, 300)"
                )

                payload = {
                    "model": VISION_MODEL,
                    "prompt": prompt,
                    "images": [img_b64],
                    "stream": False
                }

                res = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=30)
                if res.status_code == 200:
                    resp_text = res.json().get("response", "")
                    
                    action_match = re.search(r'ACTION:\s*(CLICK|MOVE)\((\d+),\s*(\d+)\)', resp_text, re.IGNORECASE)
                    if action_match:
                        action_type = action_match.group(1).upper()
                        x = int(action_match.group(2))
                        y = int(action_match.group(3))

                        x = max(0, min(x, screen_w - 1))
                        y = max(0, min(y, screen_h - 1))

                        self.safe_log(f"Auto-Agent Action: {action_type} at ({x}, {y})")
                        pyautogui.moveTo(x, y, duration=0.25)
                        if action_type == "CLICK":
                            pyautogui.click()
            except Exception as e:
                log_debug(f"Continuous vision error: {e}")

            time.sleep(3.0)

    def capture_screen_vision(self, user_prompt="Describe what is visible on this screen in detail."):
        self.speak("Analyzing screen, Sir.")
        threading.Thread(target=self._process_screen_vision, args=(user_prompt,), daemon=True).start()

    def _process_screen_vision(self, prompt_text):
        try:
            self.safe_log("Vision System: Capturing screen frame...")
            screenshot = ImageGrab.grab(all_screens=True)
            
            screenshot.thumbnail((768, 768))
            buf = io.BytesIO()
            screenshot.save(buf, format='JPEG', quality=75)
            img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

            self.safe_log("Vision System: Sending image to Ollama...")

            clean_prompt = prompt_text
            for prefix in ["look at screen", "read screen", "what is on my screen", "see my screen", "and"]:
                if clean_prompt.lower().startswith(prefix):
                    clean_prompt = clean_prompt[len(prefix):].strip()

            if not clean_prompt or len(clean_prompt) < 3:
                clean_prompt = "Describe what is visible on this screen in detail."

            payload = {
                "model": VISION_MODEL,
                "prompt": clean_prompt,
                "images": [img_b64],
                "stream": False
            }

            res = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=120)

            if res.status_code == 200:
                answer = res.json().get("response", "Vision processing produced no result.")
                self.safe_log(f"Vision Analysis: {answer}")
                self.speak(answer)
            else:
                err_msg = f"Vision Error HTTP {res.status_code}: {res.text}"
                self.safe_log(err_msg)
                self.speak("Vision processing failed, Sir.")

        except requests.exceptions.Timeout:
            self.safe_log("Vision Error: Ollama inference timed out.")
            self.speak("Vision analysis timed out, Sir.")
        except Exception as e:
            log_debug(f"Vision error: {e}\n{traceback.format_exc()}")
            self.safe_log(f"Vision Error: {str(e)}")
            self.speak("An error occurred during vision processing, Sir.")

    # --- DOCUMENT WORKSPACE: generate -> display -> edit -> export ---
    def create_document(self, topic):
        """Generate a report on a topic, then DISPLAY it in the in-app editable viewer.
        The user can then say 'edit the document to ...' to revise it."""
        self.speak(f"Compiling a report on {topic}, Sir.")
        content = self._llm_generate_report(topic)
        self.current_doc = {"topic": topic, "content": content}
        self.show_document_editor(topic, content)
        self.speak("Your document is ready and displayed. You may ask me to edit it, or say 'export to PDF', Sir.")
        return True

    def _llm_generate_report(self, topic, extra_instruction=None):
        base = (f"Write a well-structured, informative report about: {topic}. "
                "Use clear paragraphs and, where useful, short headed sections. Plain text only.")
        if extra_instruction:
            base += f"\nAdditional instruction: {extra_instruction}"
        try:
            payload = {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": base}],
                "stream": False,
                "options": {"num_predict": 600, "temperature": 0.6, "num_ctx": 2048},
            }
            res = requests.post(OLLAMA_URL, json=payload, timeout=90)
            if res.status_code == 200:
                return res.json().get("message", {}).get("content", "").strip() or f"Report on {topic}."
        except Exception as e:
            log_debug(f"report gen error: {e}")
        return f"# {topic}\n\n(Local AI unavailable — this is a placeholder. Start Ollama to generate full content.)"

    def show_document_editor(self, topic, content):
        preview_html = self._render_document_html(topic, content, for_file=False)
        payload = json.dumps({"topic": topic, "content": content, "preview_html": preview_html})
        self.run_js(f"showDocumentEditor({payload})")

    def edit_document(self, instruction):
        """Revise the currently open document per a natural-language instruction."""
        if not getattr(self, "current_doc", None):
            self.speak("There is no document open to edit, Sir. Ask me to create one first.")
            return True
        self.speak("Revising the document now, Sir.")
        topic = self.current_doc["topic"]
        current = self.current_doc["content"]
        try:
            prompt = (f"Here is the current document:\n\n{current}\n\n"
                      f"Revise it according to this instruction: {instruction}\n"
                      "Return the FULL revised document as plain text, nothing else.")
            payload = {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 700, "temperature": 0.5, "num_ctx": 4096},
            }
            res = requests.post(OLLAMA_URL, json=payload, timeout=120)
            new_content = res.json().get("message", {}).get("content", "").strip()
            if new_content:
                self.current_doc["content"] = new_content
                self.show_document_editor(topic, new_content)
                self.speak("The document has been revised, Sir.")
        except Exception as e:
            log_debug(f"edit doc error: {e}")
            self.speak("I was unable to revise the document, Sir.")
        return True

    def update_document_from_ui(self, content):
        """Called when the user manually edits the doc text in the viewer."""
        if getattr(self, "current_doc", None):
            self.current_doc["content"] = content

    def edit_document_ui(self, instruction):
        """Non-blocking wrapper for the UI 'REVISE' button."""
        threading.Thread(target=self.edit_document, args=(instruction,), daemon=True).start()

    def export_current_document_pdf(self):
        if not getattr(self, "current_doc", None):
            self.speak("There is no open document to export, Sir.")
            return True
        path = self.generate_pdf(self.current_doc["topic"], self.current_doc["content"], open_after=True)
        if path:
            self.speak("Exported to PDF and opened for you, Sir.")
        return True

    def _render_document_html(self, topic, text_content, for_file=True):
        """Render a document as a beautifully styled 'Gemini-like' HTML page:
        gradient header, accent rules, styled headings, bullet cards and callouts."""
        import html as _html

        def esc(s):
            return _html.escape(s)

        # Parse simple markdown-ish structure into styled blocks.
        blocks = []
        lines = text_content.split('\n')
        i = 0
        while i < len(lines):
            raw = lines[i].rstrip()
            stripped = raw.strip()
            if not stripped:
                i += 1
                continue
            # Headings
            if stripped.startswith('### '):
                blocks.append(f'<h3>{esc(stripped[4:])}</h3>')
            elif stripped.startswith('## '):
                blocks.append(f'<h2>{esc(stripped[3:])}</h2>')
            elif stripped.startswith('# '):
                blocks.append(f'<h2>{esc(stripped[2:])}</h2>')
            elif (len(stripped) < 60 and stripped.endswith(':') and not stripped.startswith(('-', '*', '•'))):
                blocks.append(f'<h3>{esc(stripped.rstrip(":"))}</h3>')
            # Bullet groups
            elif stripped.startswith(('- ', '* ', '• ')):
                items = []
                while i < len(lines) and lines[i].strip().startswith(('- ', '* ', '• ')):
                    items.append(esc(lines[i].strip()[2:]))
                    i += 1
                lis = ''.join(f'<li>{it}</li>' for it in items)
                blocks.append(f'<ul>{lis}</ul>')
                continue
            # Numbered lists
            elif re.match(r'^\d+[\.\)]\s', stripped):
                items = []
                while i < len(lines) and re.match(r'^\d+[\.\)]\s', lines[i].strip()):
                    items.append(esc(re.sub(r'^\d+[\.\)]\s', '', lines[i].strip())))
                    i += 1
                lis = ''.join(f'<li>{it}</li>' for it in items)
                blocks.append(f'<ol>{lis}</ol>')
                continue
            else:
                blocks.append(f'<p>{esc(stripped)}</p>')
            i += 1

        body = '\n'.join(blocks)
        generated = time.strftime('%B %d, %Y  ·  %H:%M')
        full = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Segoe UI',system-ui,sans-serif; background:#0a0e17; color:#1a1f2e;
         padding:{'40px' if for_file else '0'}; }}
  .doc {{ max-width:820px; margin:0 auto; background:#ffffff; border-radius:18px; overflow:hidden;
          box-shadow:0 20px 60px rgba(0,0,0,0.45); border:1px solid rgba(0,229,255,0.25); }}
  .doc-header {{ background:linear-gradient(135deg,#0b1220 0%,#122a3a 50%,#0b1a2a 100%);
                 padding:34px 40px; position:relative; overflow:hidden; }}
  .doc-header::before {{ content:''; position:absolute; top:-40%; right:-10%; width:280px; height:280px;
                 background:radial-gradient(circle,rgba(0,229,255,0.35),transparent 70%); }}
  .doc-badge {{ display:inline-block; font-size:11px; letter-spacing:2px; color:#00e5ff;
                border:1px solid rgba(0,229,255,0.5); padding:4px 12px; border-radius:20px;
                text-transform:uppercase; margin-bottom:14px; font-weight:600; }}
  .doc-title {{ font-size:30px; font-weight:800; color:#fff; line-height:1.2;
                background:linear-gradient(90deg,#fff,#7fe9ff); -webkit-background-clip:text;
                -webkit-text-fill-color:transparent; }}
  .doc-meta {{ margin-top:10px; font-size:12px; color:#8aa4b8; letter-spacing:1px; }}
  .accent-rule {{ height:4px; background:linear-gradient(90deg,#00e5ff,#1ed760,transparent); }}
  .doc-body {{ padding:38px 44px 48px; line-height:1.75; font-size:15px; color:#232a3a; }}
  .doc-body h2 {{ font-size:21px; margin:26px 0 10px; color:#0b2a3a; padding-left:14px;
                  border-left:4px solid #00e5ff; }}
  .doc-body h3 {{ font-size:16px; margin:20px 0 8px; color:#0e7490; }}
  .doc-body p {{ margin:12px 0; }}
  .doc-body ul, .doc-body ol {{ margin:12px 0 12px 6px; padding-left:22px; }}
  .doc-body li {{ margin:7px 0; padding-left:6px; }}
  .doc-body ul li::marker {{ color:#00e5ff; }}
  .doc-body ol li::marker {{ color:#0e7490; font-weight:700; }}
  .doc-footer {{ padding:18px 44px; border-top:1px solid #e5eef2; font-size:11px; color:#93a3b3;
                 display:flex; justify-content:space-between; letter-spacing:1px; }}
</style></head><body>
  <div class="doc">
    <div class="doc-header">
      <div class="doc-badge">J.A.R.V.I.S. Intelligence Brief</div>
      <div class="doc-title">{esc(topic)}</div>
      <div class="doc-meta">Generated {generated}</div>
    </div>
    <div class="accent-rule"></div>
    <div class="doc-body">{body}</div>
    <div class="doc-footer"><span>J.A.R.V.I.S. ENTERPRISE HUD</span><span>Confidential · Auto-generated</span></div>
  </div>
</body></html>"""
        return full

    def generate_pdf(self, topic, text_content, open_after=True):
        """Produce a styled document. Prefers a designed PDF via reportlab, and
        always writes a matching styled HTML version (the 'Gemini-like' look)."""
        ts = int(time.time())
        safe_topic = re.sub(r'[^\w\- ]', '', topic)[:40].strip().replace(' ', '_') or "report"
        pdf_path = os.path.join(DOCS_DIR, f"JARVIS_{safe_topic}_{ts}.pdf")
        html_path = os.path.join(DOCS_DIR, f"JARVIS_{safe_topic}_{ts}.html")

        # Always write the beautiful HTML version.
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(self._render_document_html(topic, text_content, for_file=True))
        except Exception as e:
            log_debug(f"HTML doc write error: {e}")
            html_path = None

        final_path = html_path
        try:
            if REPORTLAB_AVAILABLE:
                from reportlab.lib.colors import HexColor
                from reportlab.lib.units import inch
                from reportlab.platypus import Table, TableStyle, HRFlowable, ListFlowable, ListItem
                doc = SimpleDocTemplate(pdf_path, pagesize=letter,
                                        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                                        leftMargin=0.9 * inch, rightMargin=0.9 * inch)
                styles = getSampleStyleSheet()
                title_style = ParagraphStyle('DTitle', parent=styles['Title'], fontSize=24,
                                             textColor=HexColor('#0b2a3a'), spaceAfter=2)
                badge_style = ParagraphStyle('DBadge', parent=styles['Normal'], fontSize=9,
                                             textColor=HexColor('#0e7490'), spaceAfter=6)
                meta_style = ParagraphStyle('DMeta', parent=styles['Normal'], fontSize=9,
                                            textColor=HexColor('#64748b'), spaceAfter=14)
                h2_style = ParagraphStyle('DH2', parent=styles['Heading2'], fontSize=15,
                                          textColor=HexColor('#0b2a3a'), spaceBefore=14, spaceAfter=6,
                                          borderColor=HexColor('#00b8d4'), borderWidth=0, leftIndent=8)
                h3_style = ParagraphStyle('DH3', parent=styles['Heading3'], fontSize=12,
                                          textColor=HexColor('#0e7490'), spaceBefore=10, spaceAfter=4)
                body_style = ParagraphStyle('DBody', parent=styles['Normal'], fontSize=10.5,
                                            leading=16, textColor=HexColor('#232a3a'), spaceAfter=6)

                story = [
                    Paragraph("J.A.R.V.I.S. INTELLIGENCE BRIEF", badge_style),
                    Paragraph(topic, title_style),
                    Paragraph(f"Generated {time.strftime('%B %d, %Y · %H:%M')}", meta_style),
                    HRFlowable(width="100%", thickness=3, color=HexColor('#00e5ff'), spaceAfter=14),
                ]

                def esc(s):
                    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

                lines = text_content.split('\n')
                i = 0
                while i < len(lines):
                    stripped = lines[i].strip()
                    if not stripped:
                        i += 1
                        continue
                    if stripped.startswith(('- ', '* ', '• ')):
                        items = []
                        while i < len(lines) and lines[i].strip().startswith(('- ', '* ', '• ')):
                            items.append(ListItem(Paragraph(esc(lines[i].strip()[2:]), body_style),
                                                  leftIndent=14))
                            i += 1
                        story.append(ListFlowable(items, bulletType='bullet',
                                                  bulletColor=HexColor('#00b8d4'), start='•'))
                        continue
                    if stripped.startswith('### '):
                        story.append(Paragraph(esc(stripped[4:]), h3_style))
                    elif stripped.startswith(('## ', '# ')):
                        story.append(Paragraph(esc(stripped.lstrip('# ')), h2_style))
                    elif len(stripped) < 60 and stripped.endswith(':'):
                        story.append(Paragraph(esc(stripped.rstrip(':')), h3_style))
                    else:
                        story.append(Paragraph(esc(stripped), body_style))
                    i += 1

                story.append(Spacer(1, 18))
                story.append(HRFlowable(width="100%", thickness=1, color=HexColor('#e5eef2')))
                story.append(Paragraph("J.A.R.V.I.S. Enterprise HUD · Confidential · Auto-generated", meta_style))
                doc.build(story)
                final_path = pdf_path
        except Exception as e:
            log_debug(f"Styled PDF error (using HTML instead): {e}")
            final_path = html_path

        if final_path:
            self.safe_log(f"Document generated: {final_path}")
            if open_after:
                self.launch_external_app(final_path)
        return final_path

    # --- IN-APP IMAGE / WEBSITE VIEWER ---
    def open_in_viewer(self, kind, src, title=None):
        """kind = 'image' | 'web'. Displays inside JARVIS's viewer widget."""
        payload = json.dumps({"kind": kind, "src": src, "title": title or src})
        self.run_js(f"openViewer({payload})")

    def show_image_for(self, query):
        """Fetch an image URL for a query and show it inside JARVIS.
        Great for 'show me how to tie a tie' style instruction images."""
        self.speak(f"Finding a visual for {query}, Sir.")
        url = self._search_image_url(query)
        if url:
            self.open_in_viewer("image", url, title=query)
            self.safe_log(f"Displaying image for: {query}")
        else:
            # Fall back to Google Images in the embedded browser view.
            q = urllib.parse.quote_plus(query)
            self.open_in_viewer("web", f"https://www.google.com/search?tbm=isch&q={q}", title=f"Images: {query}")
        return True

    def _search_image_url(self, query):
        """Use DuckDuckGo's image endpoint (no API key) to get a direct image URL."""
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            # Get vqd token.
            r = requests.post("https://duckduckgo.com/", data={"q": query}, headers=headers, timeout=15)
            m = re.search(r'vqd=([\d-]+)', r.text) or re.search(r'vqd="([\d-]+)"', r.text)
            if not m:
                return None
            vqd = m.group(1)
            api = "https://duckduckgo.com/i.js"
            params = {"l": "us-en", "o": "json", "q": query, "vqd": vqd, "f": ",,,", "p": "1"}
            res = requests.get(api, params=params, headers=headers, timeout=15)
            data = res.json()
            results = data.get("results", [])
            if results:
                return results[0].get("image")
        except Exception as e:
            log_debug(f"image search error: {e}")
        return None

    # --- NEW FEATURE: WEB / YOUTUBE / MAPS SEARCH IN BROWSER ---
    def web_search(self, query, engine="google"):
        q = urllib.parse.quote_plus(query)
        urls = {
            "google": f"https://www.google.com/search?q={q}",
            "youtube": f"https://www.youtube.com/results?search_query={q}",
            "maps": f"https://www.google.com/maps/search/{q}",
            "wikipedia": f"https://en.wikipedia.org/wiki/Special:Search?search={q}",
            "amazon": f"https://www.amazon.com/s?k={q}",
            "images": f"https://www.google.com/search?tbm=isch&q={q}",
        }
        url = urls.get(engine, urls["google"])
        chrome = find_chrome_path()
        try:
            if chrome:
                subprocess.Popen([chrome, "--new-tab", url])
            else:
                webbrowser.open(url)
            self.speak(f"Searching {engine} for {query}, Sir.")
            self.safe_log(f"Web search ({engine}): {query}")
            return True
        except Exception as e:
            log_debug(f"web_search error: {e}")
            self.speak("I was unable to open the browser search, Sir.")
            return False

    # --- NEW FEATURE: WEATHER (no API key, via open-meteo) ---
    def get_weather(self, city=None):
        city = (city or DEFAULT_CITY).strip()
        try:
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1}, timeout=15).json()
            results = geo.get("results")
            if not results:
                self.speak(f"I could not find a location called {city}, Sir.")
                return True
            loc = results[0]
            lat, lon = loc["latitude"], loc["longitude"]
            name = loc.get("name", city)
            wx = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon,
                        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"},
                timeout=15).json()
            cur = wx.get("current", {})
            temp = cur.get("temperature_2m")
            wind = cur.get("wind_speed_10m")
            humidity = cur.get("relative_humidity_2m")
            report = (f"Current weather in {name}: {temp} degrees, "
                      f"humidity {humidity} percent, wind {wind} kilometres per hour, Sir.")
            self.speak(report)
            self.safe_log(report)
            self.instantiate_card(f"Weather — {name}", "email_draft", report)
            return True
        except Exception as e:
            log_debug(f"weather error: {e}")
            self.speak("I was unable to retrieve the weather, Sir.")
            return True

    # --- NEW FEATURE: TIME & DATE ---
    def tell_time(self):
        now = datetime.datetime.now()
        self.speak(f"It is {now.strftime('%I:%M %p')}, Sir.")
        return True

    def tell_date(self):
        now = datetime.datetime.now()
        self.speak(f"Today is {now.strftime('%A, %B %d, %Y')}, Sir.")
        return True

    # --- NEW FEATURE: REMINDERS ---
    def add_reminder(self, text, delay_seconds):
        due = time.time() + delay_seconds
        self.memory.setdefault("reminders", []).append({"text": text, "due": due})
        self.save_memory()

        def fire():
            time.sleep(delay_seconds)
            self.speak(f"Reminder, Sir: {text}")
            self.safe_log(f"REMINDER FIRED: {text}")
            self.instantiate_card("Reminder", "email_draft", text)

        threading.Thread(target=fire, daemon=True).start()
        mins = max(1, int(delay_seconds // 60))
        self.speak(f"Reminder set for {mins} minute{'s' if mins != 1 else ''} from now, Sir.")
        return True

    # --- NEW FEATURE: SCREENSHOT TO DESKTOP ---
    def take_screenshot(self):
        try:
            path = os.path.join(DOCS_DIR, f"JARVIS_Screenshot_{int(time.time())}.png")
            img = ImageGrab.grab(all_screens=True)
            img.save(path)
            self.speak("Screenshot captured and saved, Sir.")
            self.safe_log(f"Screenshot saved: {path}")
            return True
        except Exception as e:
            log_debug(f"screenshot error: {e}")
            self.speak("Failed to capture the screenshot, Sir.")
            return False

    # --- NEW FEATURE: SYSTEM INFO / BATTERY ---
    def report_system_status(self):
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            parts = [f"CPU at {cpu} percent", f"memory at {ram} percent"]
            try:
                batt = psutil.sensors_battery()
                if batt is not None:
                    charging = "charging" if batt.power_plugged else "on battery"
                    parts.append(f"battery at {int(batt.percent)} percent, {charging}")
            except Exception:
                pass
            report = "System status: " + ", ".join(parts) + ", Sir."
            self.speak(report)
            self.safe_log(report)
            return True
        except Exception as e:
            log_debug(f"system status error: {e}")
            self.speak("Unable to read system telemetry, Sir.")
            return False

    # --- NEW FEATURE: LOCK / SLEEP PC ---
    def lock_pc(self):
        try:
            if sys.platform == "win32" and HAS_WIN32:
                ctypes.windll.user32.LockWorkStation()
            elif sys.platform == "win32":
                subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["pmset", "displaysleepnow"])
            else:
                subprocess.Popen(["loginctl", "lock-session"])
            self.speak("Locking the workstation, Sir.")
            return True
        except Exception as e:
            log_debug(f"lock error: {e}")
            self.speak("I was unable to lock the workstation, Sir.")
            return False

    # --- HARDWARE / POWER CONTROL ---
    def set_volume_level(self, percent):
        """Set system volume to an absolute percentage (Windows)."""
        percent = max(0, min(100, int(percent)))
        try:
            # Reset to 0 then step up (each vol key press ~2%).
            for _ in range(50):
                pyautogui.press("volumedown")
            for _ in range(int(percent / 2)):
                pyautogui.press("volumeup")
            self.speak(f"Volume set to about {percent} percent, Sir.")
        except Exception as e:
            log_debug(f"set volume error: {e}")
            self.speak("I could not set the volume, Sir.")
        return True

    def set_brightness(self, percent):
        """Set screen brightness (Windows via WMI/PowerShell)."""
        percent = max(0, min(100, int(percent)))
        try:
            if sys.platform == "win32":
                ps = ("(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                      f".WmiSetBrightness(1,{percent})")
                creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                subprocess.Popen(["powershell", "-Command", ps], creationflags=creationflags)
                self.speak(f"Brightness set to {percent} percent, Sir.")
            else:
                self.speak("Brightness control is only available on Windows, Sir.")
        except Exception as e:
            log_debug(f"brightness error: {e}")
            self.speak("I could not adjust brightness, Sir.")
        return True

    def sleep_display(self):
        try:
            if sys.platform == "win32":
                subprocess.Popen("powershell -Command \"(Add-Type '[DllImport(\\\"user32.dll\\\")]public static extern int SendMessage(int hWnd,int hMsg,int wParam,int lParam);' -Name a -Pass)::SendMessage(-1,0x0112,0xF170,2)\"", shell=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["pmset", "displaysleepnow"])
            else:
                subprocess.Popen(["xset", "dpms", "force", "off"])
            self.speak("Turning off the display, Sir.")
        except Exception as e:
            log_debug(f"display sleep error: {e}")
        return True

    def power_action(self, action):
        """action = 'sleep' | 'restart' | 'shutdown'. Confirmed by voice/text only."""
        try:
            if sys.platform == "win32":
                cmds = {
                    "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
                    "restart": "shutdown /r /t 5",
                    "shutdown": "shutdown /s /t 5",
                }
            elif sys.platform == "darwin":
                cmds = {"sleep": "pmset sleepnow", "restart": "sudo shutdown -r now",
                        "shutdown": "sudo shutdown -h now"}
            else:
                cmds = {"sleep": "systemctl suspend", "restart": "systemctl reboot",
                        "shutdown": "systemctl poweroff"}
            cmd = cmds.get(action)
            if not cmd:
                return True
            verb = {"sleep": "putting the system to sleep", "restart": "restarting the system",
                    "shutdown": "shutting the system down"}[action]
            self.speak(f"{verb.capitalize()} in five seconds, Sir.")
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            log_debug(f"power action error: {e}")
            self.speak("I could not perform that power action, Sir.")
        return True

    def toggle_wifi(self, on=True):
        try:
            if sys.platform == "win32":
                state = "enable" if on else "disable"
                # Try common adapter names.
                for name in ["Wi-Fi", "Wireless Network Connection", "WLAN"]:
                    subprocess.Popen(f'netsh interface set interface "{name}" {state}', shell=True)
                self.speak(f"Wi-Fi {'enabled' if on else 'disabled'}, Sir.")
            else:
                self.speak("Wi-Fi toggling is only wired up for Windows, Sir.")
        except Exception as e:
            log_debug(f"wifi toggle error: {e}")
            self.speak("I could not change the Wi-Fi state, Sir.")
        return True

    def media_control(self, action):
        keymap = {"play": "playpause", "pause": "playpause", "next": "nexttrack",
                  "previous": "prevtrack", "stop": "stop"}
        key = keymap.get(action, "playpause")
        try:
            pyautogui.press(key)
            self.speak(f"Media {action}, Sir.")
        except Exception as e:
            log_debug(f"media control error: {e}")
        return True

    # --- NEW FEATURE: CLIPBOARD READ-BACK ---
    def read_clipboard(self):
        if not HAS_CLIPBOARD:
            self.speak("Clipboard support is not installed, Sir. Install pyperclip to enable it.")
            return True
        try:
            content = pyperclip.paste()
            if content:
                snippet = content[:300]
                self.speak(f"Your clipboard contains: {snippet}")
            else:
                self.speak("Your clipboard is empty, Sir.")
            return True
        except Exception as e:
            log_debug(f"clipboard error: {e}")
            self.speak("Unable to read the clipboard, Sir.")
            return False

    # ==================================================================
    # 10 NEW FEATURES
    # ==================================================================

    # 1. Calculator / math evaluation
    def calculate(self, expression):
        try:
            expr = re.sub(r'[^0-9+\-*/().%\s]', '', expression)
            if not expr.strip():
                self.speak("I could not parse that calculation, Sir.")
                return True
            result = eval(expr, {"__builtins__": {}}, {})
            self.speak(f"That equals {result}, Sir.")
            self.safe_log(f"Calculation: {expr} = {result}")
        except Exception:
            self.speak("I was unable to compute that, Sir.")
        return True

    # 2. Wikipedia quick summary (spoken)
    def wiki_summary(self, topic):
        try:
            t = urllib.parse.quote(topic.strip().replace(" ", "_"))
            res = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}", timeout=15,
                               headers={"User-Agent": "JARVIS/1.0"})
            if res.status_code == 200:
                extract = res.json().get("extract")
                if extract:
                    self.speak(extract[:600])
                    self.instantiate_card(f"Wikipedia — {topic}", "email_draft", extract)
                    return True
            self.speak(f"I could not find a summary for {topic}, Sir.")
        except Exception as e:
            log_debug(f"wiki error: {e}")
            self.speak("Wikipedia lookup failed, Sir.")
        return True

    # 3. Latest news headlines
    def get_news(self, topic=None):
        try:
            url = "https://news.google.com/rss"
            if topic:
                url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(topic)}"
            res = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<title>(.*?)</title>', res.text)
            titles = [t for t in titles[1:6] if t]
            if titles:
                spoken = "Here are the top headlines, Sir. " + ". ".join(titles[:3])
                self.speak(spoken)
                self.instantiate_card("News Headlines", "carousel", titles)
            else:
                self.speak("I could not retrieve the news, Sir.")
        except Exception as e:
            log_debug(f"news error: {e}")
            self.speak("News retrieval failed, Sir.")
        return True

    # 4. Currency / crypto price
    def get_price(self, symbol):
        sym = symbol.lower().strip()
        crypto_map = {"bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum",
                      "eth": "ethereum", "dogecoin": "dogecoin", "doge": "dogecoin"}
        try:
            if sym in crypto_map:
                cid = crypto_map[sym]
                res = requests.get("https://api.coingecko.com/api/v3/simple/price",
                                   params={"ids": cid, "vs_currencies": "usd"}, timeout=15).json()
                price = res.get(cid, {}).get("usd")
                if price is not None:
                    self.speak(f"{symbol.title()} is currently {price} US dollars, Sir.")
                    return True
            self.speak(f"I could not fetch a price for {symbol}, Sir.")
        except Exception as e:
            log_debug(f"price error: {e}")
            self.speak("Price lookup failed, Sir.")
        return True

    # 5. Timer / countdown
    def start_timer(self, seconds, label="timer"):
        def run():
            time.sleep(seconds)
            self.speak(f"Your {label} has finished, Sir.")
            try:
                for _ in range(3):
                    pyautogui.press("volumeup")
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
        mins = seconds / 60
        self.speak(f"Timer set for {int(mins)} minute{'s' if int(mins) != 1 else ''}, Sir."
                   if mins >= 1 else f"Timer set for {seconds} seconds, Sir.")
        return True

    # 6. Remember an arbitrary fact long-term
    def remember_fact(self, fact):
        self.memory.setdefault("facts", []).append(fact.strip())
        self.save_memory()
        self.speak("I'll remember that, Sir.")
        self.safe_log(f"Fact stored: {fact}")
        return True

    def recall_facts(self):
        facts = self.memory.get("facts", [])
        if facts:
            self.speak("Here is what I remember, Sir. " + ". ".join(facts[-8:]))
            self.instantiate_card("Things I Remember", "carousel", facts)
        else:
            self.speak("I have no stored facts yet, Sir.")
        return True

    # 7. Open a specific website inside JARVIS
    def open_website_in_app(self, url):
        u = url.strip()
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        self.open_in_viewer("web", u, title=u)
        self.speak("Opening that inside the viewer, Sir.")
        return True

    # 8. Type text into whatever app is focused (dictation)
    def type_text(self, text):
        try:
            time.sleep(0.4)
            pyautogui.write(text, interval=0.01)
            self.speak("Typed, Sir.")
        except Exception as e:
            log_debug(f"type error: {e}")
            self.speak("I was unable to type that, Sir.")
        return True

    # 9. Empty recycle bin / free memory hint (system housekeeping)
    def system_cleanup(self):
        try:
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x00000001)
                except Exception:
                    pass
            self.speak("I've cleared the recycle bin, Sir.")
        except Exception as e:
            log_debug(f"cleanup error: {e}")
            self.speak("Cleanup could not complete, Sir.")
        return True

    # 10. Flip a coin / roll a die / random pick
    def random_choice(self, cmd):
        import random
        if "coin" in cmd:
            self.speak(f"{random.choice(['Heads', 'Tails'])}, Sir.")
        elif "dice" in cmd or "die" in cmd:
            self.speak(f"You rolled a {random.randint(1, 6)}, Sir.")
        else:
            m = re.search(r'between (.+?) and (.+)', cmd)
            if m:
                self.speak(f"I'd choose {random.choice([m.group(1).strip(), m.group(2).strip()])}, Sir.")
            else:
                self.speak(f"A random number: {random.randint(1, 100)}, Sir.")
        return True

    def _parse_email_command(self, cmd_text):
        """Extract (recipient, instruction, auto_send) from a natural email command.

        Handles forms like:
          'send an email to John about the meeting tomorrow'
          'email sarah@work.com saying I'll be late'
          'draft an email to my boss regarding the report' (auto_send=False for 'draft')
        """
        text = cmd_text.strip()
        low = text.lower()

        # 'draft'/'prepare'/'write' imply review-before-send; 'send' implies auto-send.
        auto_send = EMAIL_AUTO_SEND
        if any(w in low for w in ["draft", "prepare", "compose", "write an email", "write email"]):
            auto_send = False
        if "send" in low:
            auto_send = EMAIL_AUTO_SEND

        recipient = None
        instruction = None

        # Pattern: "... to <recipient> <connector> <instruction>"
        m = re.search(
            r'\bto\s+(.+?)\s+(about|regarding|saying|telling (?:them|him|her)|that says?|re:|with|informing (?:them|him|her))\s+(.+)',
            text, re.IGNORECASE)
        if m:
            recipient = m.group(1).strip()
            instruction = m.group(3).strip()
        else:
            # Pattern: "... to <recipient>" with the rest (or nothing) as instruction
            m2 = re.search(r'\bto\s+(\S+@\S+|[A-Za-z][\w .\'-]*)', text, re.IGNORECASE)
            if m2:
                recipient = m2.group(1).strip()
            # Pattern: connector without explicit "to"
            m3 = re.search(r'\b(about|regarding|saying|that says?|re:)\s+(.+)', text, re.IGNORECASE)
            if m3:
                instruction = m3.group(2).strip()

        # "email me ..." -> recipient is self
        if re.search(r'\bemail me\b', low):
            recipient = recipient or "me"
            if not instruction:
                instruction = re.sub(r'.*\bemail me\b\s*(about|regarding)?\s*', '', text, flags=re.IGNORECASE).strip()

        # Fallback: any raw address in the text is the recipient (e.g. "email bob@x.com saying hi").
        if not recipient:
            addr = extract_email_address(text)
            if addr:
                recipient = addr

        # Fallback: "email <name> about/saying ..." with no "to".
        if not recipient:
            mn = re.search(r'\bemail\s+([A-Za-z][\w\'-]*)\b', text, re.IGNORECASE)
            if mn and mn.group(1).lower() not in ("me", "a", "an", "the"):
                recipient = mn.group(1).strip()

        # Clean common trailing/leading noise from recipient.
        if recipient:
            recipient = re.sub(r'^(my|the)\s+', '', recipient, flags=re.IGNORECASE).strip()
            recipient = recipient.strip(" .,")

        # If we still have no instruction, use whatever follows the verb.
        if not instruction:
            stripped = re.sub(
                r'^(please\s+)?(send|write|compose|draft|prepare)\s+(an?\s+)?email\s*',
                '', text, flags=re.IGNORECASE).strip()
            if recipient:
                stripped = re.sub(r'^to\s+' + re.escape(recipient), '', stripped, flags=re.IGNORECASE).strip()
            instruction = stripped or "a brief, friendly message"

        return recipient, instruction, auto_send

    # --- PC & APP AUTOMATION ENGINE ---
    def execute_pc_automation(self, cmd_text):
        cmd = cmd_text.lower().strip()

        # In-App Dynamic Cards Internal Execution Demos
        if "carousel" in cmd or "social media" in cmd:
            carousel_items = ["Post #1: Tech Insights", "Post #2: AI Trends", "Post #3: Python Automation"]
            self.instantiate_card("Social Media Carousel", "carousel", carousel_items)
            self.speak("Retrieved social media carousel cards, Sir.")
            return True

        if "check bugs" in cmd or "check code" in cmd or "debug code" in cmd:
            bug_report = "File: main.py\nLine 42: NullPointerException potential.\nRecommendation: Add safety check for 'user_id'."
            self.instantiate_card("Code Audit Report", "code_bug", bug_report)
            self.speak("Instantiated code bug analysis card.")
            return True

        # --- DOCUMENT WORKSPACE ---
        # Edit the open document: "edit the document to ...", "change the doc ..."
        edit_doc = re.search(r'(?:edit|change|revise|update|rewrite)\s+(?:the\s+)?(?:document|doc|report|pdf)\s+(?:to|so that|and)?\s*(.+)', cmd)
        if edit_doc:
            return self.edit_document(edit_doc.group(1).strip())
        if any(k in cmd for k in ["export to pdf", "export pdf", "export the document", "save as pdf", "save the document", "download the pdf"]):
            return self.export_current_document_pdf()
        # Create a document/PDF/report on a subject.
        make_doc = re.search(r'(?:create|make|generate|write|draft)\s+(?:me\s+)?(?:a\s+)?(?:pdf|document|report|doc)\s+(?:on|about|for|regarding)\s+(.+)', cmd)
        if make_doc:
            return self.create_document(make_doc.group(1).strip())

        # --- IMAGE / VISUAL INSTRUCTIONS ---
        img_req = re.search(r'(?:show me (?:a picture|an image|a photo|a diagram|images?|how to|instructions? (?:on|for)|what)|display (?:an? )?image (?:of|for)?|picture of|image of|diagram of)\s+(.+)', cmd)
        if img_req and "screen" not in cmd:
            query = img_req.group(1).strip()
            query = re.sub(r'^(of|for|to)\s+', '', query)
            return self.show_image_for(query)

        # --- OPEN A WEBSITE INSIDE JARVIS ---
        site_req = re.search(r'(?:open|show|load|display|browse)\s+(\S+\.\S+|\S+\s*\.\s*com|https?://\S+)\s*(?:in|inside|within)?\s*(?:jarvis|the viewer|here)?', cmd)
        if site_req and any(x in cmd for x in [".com", ".org", ".net", ".io", "http", "website", "in jarvis", "in the viewer"]):
            return self.open_website_in_app(site_req.group(1).strip())

        # --- NEW FEATURES ROUTING ---
        calc = re.search(r'(?:calculate|what(?:\'s| is)|compute|how much is)\s+(.+)', cmd)
        if calc and re.search(r'\d[\s\d+\-*/().%]*[+\-*/]', calc.group(1)):
            return self.calculate(calc.group(1))
        # Wikipedia only on an explicit request, so normal "what is X" still goes to the AI.
        wiki = re.search(r'(?:wikipedia (?:summary|entry|page) (?:of|for|on)|wikipedia|look up)\s+(.+)', cmd)
        if wiki and "screen" not in cmd:
            topic = re.sub(r'\bon wikipedia\b', '', wiki.group(1)).strip()
            return self.wiki_summary(topic)
        if "news" in cmd:
            ntopic = re.search(r'news (?:about|on|for)\s+(.+)', cmd)
            return self.get_news(ntopic.group(1).strip() if ntopic else None)
        price = re.search(r'(?:price of|how much is)\s+(bitcoin|btc|ethereum|eth|dogecoin|doge)\b', cmd)
        if price:
            return self.get_price(price.group(1))
        timer = re.search(r'set (?:a )?timer for (\d+)\s*(second|minute|hour)s?', cmd)
        if timer:
            mult = {"second": 1, "minute": 60, "hour": 3600}[timer.group(2)]
            return self.start_timer(int(timer.group(1)) * mult, "timer")
        remember = re.search(r'(?:remember|note|keep in mind|don\'t forget)\s+that\s+(.+)', cmd)
        if remember:
            return self.remember_fact(remember.group(1).strip())
        if any(k in cmd for k in ["what do you remember", "recall facts", "what do you know about me"]):
            return self.recall_facts()
        if any(k in cmd for k in ["flip a coin", "roll a dice", "roll a die", "pick between", "choose between", "random number"]):
            return self.random_choice(cmd)
        if any(k in cmd for k in ["empty recycle bin", "empty the recycle bin", "clean up", "system cleanup", "empty trash"]):
            return self.system_cleanup()

        # --- Time & date ---
        if any(kw in cmd for kw in ["what time", "what's the time", "current time", "tell me the time"]):
            return self.tell_time()
        if any(kw in cmd for kw in ["what day", "what's the date", "what is the date", "today's date", "what date"]):
            return self.tell_date()

        # --- System status / battery ---
        if any(kw in cmd for kw in ["system status", "system info", "cpu usage", "battery level",
                                    "battery status", "how much battery", "system telemetry"]):
            return self.report_system_status()

        # --- Lock PC ---
        if any(kw in cmd for kw in ["lock pc", "lock the pc", "lock computer", "lock the computer",
                                    "lock workstation", "lock the screen", "lock my pc", "lock my computer"]):
            return self.lock_pc()

        # --- Hardware / power control ---
        vol_set = re.search(r'(?:set )?volume (?:to |at )?(\d{1,3})\s*(?:percent|%)?', cmd)
        if vol_set and "brightness" not in cmd:
            return self.set_volume_level(int(vol_set.group(1)))
        bri = re.search(r'(?:set )?brightness (?:to |at )?(\d{1,3})\s*(?:percent|%)?', cmd)
        if bri:
            return self.set_brightness(int(bri.group(1)))
        if any(k in cmd for k in ["turn off the display", "turn off screen", "turn off the monitor", "sleep the display", "sleep display"]):
            return self.sleep_display()
        if any(k in cmd for k in ["go to sleep", "sleep the pc", "sleep the computer", "put the pc to sleep", "put the computer to sleep"]):
            return self.power_action("sleep")
        if any(k in cmd for k in ["restart the pc", "restart the computer", "reboot the pc", "reboot the computer", "restart my computer"]):
            return self.power_action("restart")
        if any(k in cmd for k in ["shutdown the pc", "shut down the pc", "shutdown the computer", "shut down the computer", "power off the pc", "turn off the computer", "turn off the pc"]):
            return self.power_action("shutdown")
        if any(k in cmd for k in ["turn on wifi", "enable wifi", "turn wifi on", "enable wi-fi", "turn on wi-fi"]):
            return self.toggle_wifi(True)
        if any(k in cmd for k in ["turn off wifi", "disable wifi", "turn wifi off", "disable wi-fi", "turn off wi-fi"]):
            return self.toggle_wifi(False)
        if any(k in cmd for k in ["play media", "pause media", "media play", "media pause", "play pause"]):
            return self.media_control("play")
        if any(k in cmd for k in ["next media", "media next"]):
            return self.media_control("next")

        # --- Screenshot ---
        if any(kw in cmd for kw in ["take a screenshot", "take screenshot", "capture screen", "screenshot"]):
            return self.take_screenshot()

        # --- Clipboard read ---
        if any(kw in cmd for kw in ["read clipboard", "what's in my clipboard", "read my clipboard"]):
            return self.read_clipboard()

        # --- Weather ---
        wmatch = re.search(r'weather(?:\s+(?:in|for|at)\s+(.+))?', cmd)
        if wmatch:
            city = wmatch.group(1).strip() if wmatch.group(1) else None
            return self.get_weather(city)

        # --- Reminders: "remind me to X in N minutes/seconds/hours" ---
        rmatch = re.search(r'remind me to (.+?) in (\d+)\s*(second|minute|hour)s?', cmd)
        if rmatch:
            task = rmatch.group(1).strip()
            amount = int(rmatch.group(2))
            unit = rmatch.group(3)
            mult = {"second": 1, "minute": 60, "hour": 3600}[unit]
            return self.add_reminder(task, amount * mult)

        # --- Web searches (before generic 'open') ---
        ymatch = re.search(r'(?:search|play|find|look up|pull up)\s+(.+?)\s+on youtube', cmd)
        if ymatch:
            return self.web_search(ymatch.group(1).strip(), engine="youtube")
        if "on youtube" in cmd or cmd.startswith("youtube "):
            q = cmd.replace("on youtube", "").replace("youtube", "").strip()
            return self.web_search(q or "trending", engine="youtube")

        gmatch = re.search(r'(?:google|search (?:for|up)?|look up|search)\s+(.+)', cmd)
        if gmatch and not any(k in cmd for k in ["screen", "clipboard", "youtube", "reading", "wikipedia", "wiki"]):
            query = gmatch.group(1).strip()
            query = re.sub(r'\bon google\b', '', query).strip()
            if query:
                return self.web_search(query, engine="google")

        mmatch = re.search(r'(?:directions to|map of|navigate to|where is)\s+(.+)', cmd)
        if mmatch:
            return self.web_search(mmatch.group(1).strip(), engine="maps")

        if any(kw in cmd for kw in ["start reading screen", "enable screen monitoring", "auto screen mode", "start screen ai"]):
            self.toggle_continuous_vision(True)
            self.run_js("setAutoVisionState(true)")
            return True

        if any(kw in cmd for kw in ["stop reading screen", "disable screen monitoring", "stop screen ai"]):
            self.toggle_continuous_vision(False)
            self.run_js("setAutoVisionState(false)")
            return True

        if "volume up" in cmd or "increase volume" in cmd:
            pyautogui.press("volumeup", presses=5)
            self.speak("Volume increased, Sir.")
            return True

        if "volume down" in cmd or "decrease volume" in cmd:
            pyautogui.press("volumedown", presses=5)
            self.speak("Volume decreased, Sir.")
            return True

        if "mute system" in cmd or "mute audio" in cmd or "mute volume" in cmd:
            pyautogui.press("volumemute")
            self.speak("System audio toggled, Sir.")
            return True

        if "play " in cmd and ("on spotify" in cmd or "song" in cmd or "track" in cmd or cmd.startswith("play ")):
            song_query = cmd_text
            for prefix in ["play on spotify", "play song", "play track", "play"]:
                if song_query.lower().startswith(prefix):
                    song_query = song_query[len(prefix):].strip()
                    break
            song_query = song_query.replace("on spotify", "").strip()

            if song_query and song_query.lower() not in ["music", "spotify", "pause"]:
                return self.spotify_play_track(song_query)
            elif song_query.lower() in ["music", "spotify"] or cmd.strip() == "play":
                return self.spotify_toggle_play(force_play=True)

        if any(kw in cmd for kw in ["pause spotify", "pause music", "stop music", "pause song"]):
            return self.spotify_pause()

        if any(kw in cmd for kw in ["resume spotify", "resume music", "unpause music"]):
            return self.spotify_toggle_play(force_play=True)

        if any(kw in cmd for kw in ["next song", "skip song", "next track", "skip track"]):
            return self.spotify_next()

        if any(kw in cmd for kw in ["previous song", "last song", "previous track"]):
            return self.spotify_previous()

        if "what is playing" in cmd or "what's playing" in cmd or "current song" in cmd:
            return self.spotify_announce_current()

        open_match = re.search(r'\b(open|launch|start|focus)\s+(.+)', cmd)
        if open_match and not any(k in cmd for k in ["spotify", "pdf", "email", "file", "card"]):
            target_app = open_match.group(2).strip()
            self.speak(f"Launching {target_app}, Sir.")
            return self.launch_external_app(target_app)

        if any(kw in cmd for kw in ["close app", "close window", "close program"]):
            pyautogui.hotkey('alt', 'f4')
            self.speak("Closing active window.")
            return True

        if any(kw in cmd for kw in ["close tab"]):
            pyautogui.hotkey('ctrl', 'w')
            self.speak("Closing active tab.")
            return True

        if any(kw in cmd for kw in ["switch window", "alt tab", "switch app"]):
            pyautogui.hotkey('alt', 'tab')
            self.speak("Switched window.")
            return True

        if any(kw in cmd for kw in ["minimize window", "minimize app"]):
            pyautogui.hotkey('win', 'down')
            self.speak("Minimized window.")
            return True

        if any(kw in cmd for kw in ["maximize window", "maximize app"]):
            pyautogui.hotkey('win', 'up')
            self.speak("Maximized window.")
            return True

        if any(w in cmd for w in ["look at screen", "read screen", "what is on my screen", "see my screen"]):
            self.capture_screen_vision(cmd)
            return True

        if any(kw in cmd for kw in ["send email", "send an email", "send a email",
                                    "write an email", "write email", "compose email",
                                    "compose an email", "draft email", "draft an email",
                                    "email me", "prepare email"]):
            recipient, instruction, auto_send = self._parse_email_command(cmd_text)
            self.speak("Preparing your email now, Sir.")
            threading.Thread(
                target=self.compose_and_send_email,
                kwargs={"recipient": recipient, "instruction": instruction, "auto_send": auto_send},
                daemon=True,
            ).start()
            return True

        # Save a contact: "remember email for John as john@x.com"
        contact_match = re.search(
            r'(?:remember|save|store)\s+(?:the\s+)?email\s+(?:for|of)\s+(.+?)\s+(?:as|is|=)\s+(\S+@\S+)',
            cmd, re.IGNORECASE)
        if contact_match:
            name = contact_match.group(1).strip()
            addr = extract_email_address(contact_match.group(2))
            if name and addr:
                self.memory.setdefault("contacts", {})[name.lower()] = addr
                CONTACTS[name.lower()] = addr
                self.save_memory()
                self.speak(f"Saved {name}'s email address, Sir.")
                self.safe_log(f"Contact saved: {name} -> {addr}")
            return True

        if "double click" in cmd:
            pyautogui.doubleClick()
            self.speak("Double click executed.")
            return True

        if "right click" in cmd:
            pyautogui.rightClick()
            self.speak("Right click executed.")
            return True

        if "click at" in cmd or "move mouse to" in cmd:
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                pyautogui.moveTo(x, y, duration=0.2)
                if "click" in cmd: pyautogui.click()
                self.speak(f"Executed cursor movement to {x}, {y}.")
                return True

        if "click" in cmd:
            pyautogui.click()
            self.speak("Click executed.")
            return True

        if "scroll down" in cmd:
            pyautogui.scroll(-500)
            self.speak("Scrolled down.")
            return True

        if "scroll up" in cmd:
            pyautogui.scroll(500)
            self.speak("Scrolled up.")
            return True

        if cmd.startswith("type "):
            text_to_type = cmd_text[5:]
            pyautogui.write(text_to_type, interval=0.01)
            self.speak("Typing complete.")
            return True

        if "press enter" in cmd:
            pyautogui.press('enter')
            return True

        if "copy to clipboard" in cmd or "copy selection" in cmd:
            pyautogui.hotkey('ctrl', 'c')
            self.speak("Copied to system clipboard.")
            return True

        if "generate pdf on" in cmd or "make a pdf about" in cmd or "generate pdf about" in cmd:
            topic = (cmd.replace("generate pdf on", "").replace("generate pdf about", "")
                        .replace("make a pdf about", "").strip())
            return self.create_document(topic)

        if "when i say" in cmd and "run" in cmd:
            try:
                parts = cmd.replace("when i say", "").split("run")
                keyword = parts[0].strip()
                action = parts[1].strip()
                self.memory["custom_macros"][keyword] = action
                self.save_memory()
                self.speak(f"Macro saved for {keyword}.")
                return True
            except Exception:
                pass

        for kw, act in self.memory.get("custom_macros", {}).items():
            if kw in cmd:
                self.speak(f"Executing macro for {kw}.")
                self.process_command_backend(act)
                return True

        return False

    def handle_text_command(self, cmd):
        # Barge-in: if JARVIS is currently speaking, stop immediately so the
        # new command takes over without waiting for him to finish.
        if self.is_speaking or not self.speech_queue.empty():
            self.stop_speech()
        self.safe_log(f"> {cmd}", is_user=True)
        threading.Thread(target=self.process_command_backend, args=(cmd,), daemon=True).start()

    def _build_context_messages(self, cmd):
        """Assemble the system prompt + remembered facts + rolling recent context."""
        system = SYSTEM_PROMPT
        facts = self.memory.get("facts", [])
        if facts:
            system += "\n\nThings you must remember about the user:\n- " + "\n- ".join(facts[-25:])
        chat_history = self.memory.get("chat_history", [])
        # Keep the last ~10 turns for coherence without slowing inference too much.
        recent = chat_history[-10:]
        return [{"role": "system", "content": system}] + recent

    def process_command_backend(self, cmd):
        if not cmd:
            return
        cmd_lower = cmd.lower()

        # Close the JARVIS app itself — but NOT when the user means the whole PC.
        pc_power = any(p in cmd_lower for p in ["the pc", "the computer", "my pc", "my computer", "the system"])
        if (("exit jarvis" in cmd_lower)
                or ("shutdown jarvis" in cmd_lower) or ("shut down jarvis" in cmd_lower)
                or ("close jarvis" in cmd_lower)
                or ("shutdown" in cmd_lower and "core" in cmd_lower)
                or ("shutdown" in cmd_lower and not pc_power and "restart" not in cmd_lower)):
            self.speak("Deactivating system core. Goodbye, Sir.")
            time.sleep(1.0)
            self.close_app()
            return

        # Log every user message to the permanent transcript before anything else.
        self.remember_message("user", cmd)

        if self.execute_pc_automation(cmd):
            self.save_memory()
            return

        chat_history = self.memory.setdefault("chat_history", [])
        chat_history.append({"role": "user", "content": cmd})
        messages = self._build_context_messages(cmd)

        try:
            payload = {
                "model": MODEL_NAME,
                "messages": messages,
                "stream": True,
                "options": {
                    "num_predict": 120,
                    "temperature": 0.6,
                    "top_k": 30,
                    "num_ctx": 2048,
                },
            }
            # Stream tokens AND speak each sentence the moment it completes, so
            # JARVIS starts talking almost immediately instead of after the whole reply.
            reply_parts = []
            speak_buffer = ""
            spoke_anything = False
            with requests.post(OLLAMA_URL, json=payload, timeout=45, stream=True) as res:
                if res.status_code != 200:
                    self.speak("Local neural model connection refused, Sir.")
                    return
                for line in res.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        reply_parts.append(piece)
                        speak_buffer += piece
                        # Flush complete sentences to the speech queue as they arrive.
                        while True:
                            m = re.search(r'[.!?](\s|$)', speak_buffer)
                            if not m:
                                break
                            end = m.end()
                            sentence = speak_buffer[:end].strip()
                            speak_buffer = speak_buffer[end:]
                            if sentence:
                                self.speak(sentence)
                                spoke_anything = True
                    if chunk.get("done"):
                        break

            # Speak any trailing partial sentence.
            if speak_buffer.strip():
                self.speak(speak_buffer.strip())
                spoke_anything = True

            reply = "".join(reply_parts).strip() or "I could not process the request, Sir."
            if not spoke_anything:
                self.speak(reply)
            chat_history.append({"role": "assistant", "content": reply})
            self.remember_message("assistant", reply)
            # Trim rolling context; the full transcript is preserved separately.
            self.memory["chat_history"] = chat_history[-40:]
            self.save_memory()
            self.safe_log(f"JARVIS: {reply}")
        except requests.exceptions.ConnectionError:
            log_debug("Ollama server not reached.")
            self.speak("Ollama is not running. Please start Ollama in the background, Sir.")
        except requests.exceptions.Timeout:
            log_debug("Ollama inference timed out.")
            self.speak("Local AI inference timed out. Please try a shorter query, Sir.")
        except Exception as e:
            log_debug(f"Ollama call exception: {e}")
            self.speak("Error communicating with local AI model, Sir.")

    def save_scratchpad_note(self, text):
        self.memory["scratchpad"] = text
        self.save_memory()

    def add_todo(self, task):
        self.memory["todos"].append(task)
        self.save_memory()
        self.sync_todos_to_ui()

    def remove_todo(self, index):
        if 0 <= index < len(self.memory.get("todos", [])):
            self.memory["todos"].pop(index)
            self.save_memory()
            self.sync_todos_to_ui()

    def sync_todos_to_ui(self):
        todos_json = json.dumps(self.memory.get("todos", []))
        self.run_js(f"renderTodoList({todos_json})")

    def close_app(self):
        if self.tray_icon:
            try: self.tray_icon.stop()
            except Exception: pass
        if self.window:
            try: self.window.destroy()
            except Exception: pass
        os._exit(0)


class JarvisAPI:
    def __init__(self, app):
        self._app = app

    def set_ui_ready(self):
        self._app.set_ui_ready()

    def handle_text_command(self, cmd):
        self._app.handle_text_command(cmd)

    def set_mic_mute(self, muted):
        self._app.set_mic_mute(muted)

    def toggle_continuous_vision(self, active):
        self._app.toggle_continuous_vision(active)

    def capture_screen_vision(self):
        self._app.capture_screen_vision()

    def stop_speech(self):
        self._app.stop_speech()

    def close_app(self):
        self._app.close_app()

    def spotify_toggle_play_js(self):
        self._app.spotify_toggle_play_js()

    def spotify_next_js(self):
        self._app.spotify_next_js()

    def spotify_previous_js(self):
        self._app.spotify_previous_js()

    def save_scratchpad_note(self, text):
        self._app.save_scratchpad_note(text)

    def add_todo(self, task):
        self._app.add_todo(task)

    def remove_todo(self, index):
        if isinstance(index, str):
            try:
                index = int(index)
            except ValueError:
                return
        self._app.remove_todo(index)

    def launch_external_app(self, target, args=None):
        return self._app.launch_external_app(target, args)

    def toggle_overlay(self):
        self._app.toggle_overlay()

    def take_screenshot(self):
        threading.Thread(target=self._app.take_screenshot, daemon=True).start()

    def report_system_status(self):
        threading.Thread(target=self._app.report_system_status, daemon=True).start()

    # --- Document workspace ---
    def edit_document_ui(self, instruction):
        self._app.edit_document_ui(instruction)

    def update_document_from_ui(self, content):
        self._app.update_document_from_ui(content)

    def export_current_document_pdf(self):
        threading.Thread(target=self._app.export_current_document_pdf, daemon=True).start()

    # --- Viewer ---
    def open_viewer(self, kind, src, title=None):
        self._app.open_in_viewer(kind, src, title)

    # --- Bottom bar customisation ---
    def save_bar_config(self, cfg_json):
        self._app.save_bar_config(cfg_json)

    # --- Overlay restore ---
    def restore_main(self):
        self._app.restore_main()


def main():
    log_debug("Starting J.A.R.V.I.S. Autonomous Core Launcher...")
    try:
        app = JarvisApp()
        api = JarvisAPI(app)

        app.window = webview.create_window(
            title="J.A.R.V.I.S. Visual HUD System",
            html=HTML_UI,
            js_api=api,
            width=1280,
            height=768,
            min_size=(800, 600),
            background_color="#030712"
        )
        webview.start(debug=False)
    except Exception as e:
        log_debug(f"Fatal GUI initialization error: {traceback.format_exc()}")
        print(f"Fatal system error: {e}")

if __name__ == "__main__":
    main()