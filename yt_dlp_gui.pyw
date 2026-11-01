"""yt-dlp GUI - a Tkinter front end for yt-dlp on Windows.

Double-click this .pyw file to run it without a console window, or build a
standalone .exe with build.bat. Settings are saved to
%APPDATA%\\yt-dlp-gui\\settings.json; downloaded tools (yt-dlp, ffmpeg, Deno)
go to a "bin" folder next to the app.
"""
import importlib.util
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import zipfile
from pathlib import Path
from tkinter import filedialog, ttk

APP_NAME = "yt-dlp GUI"
FROZEN = getattr(sys, "frozen", False)  # True when running as the PyInstaller .exe
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else sys.argv[0]))
BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)  # files packed inside the .exe
SETTINGS_FILE = os.path.join(
    os.environ.get("APPDATA", str(Path.home())), "yt-dlp-gui", "settings.json"
)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Portable builds of everything yt-dlp needs, fetched by the Setup tab
COMPONENTS = {
    "yt-dlp": {
        "url": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        "files": ["yt-dlp.exe"],
        "desc": "The downloader itself (~25 MB)",
    },
    "ffmpeg": {
        "url": "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
               "ffmpeg-master-latest-win64-gpl.zip",
        "files": ["ffmpeg.exe", "ffprobe.exe"],
        "desc": "Merges video + audio, converts audio, embeds subs and art (~200 MB download)",
    },
    "Deno": {
        "url": "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
        "files": ["deno.exe"],
        "desc": "JavaScript runtime yt-dlp needs for YouTube (~45 MB)",
    },
}


def pick_tools_dir():
    """A portable "bin" folder next to the app, or %LOCALAPPDATA% if that isn't writable."""
    for d in (os.path.join(APP_DIR, "bin"),
              os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home())), "yt-dlp-gui", "bin")):
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-test")
            open(probe, "w").close()
            os.remove(probe)
            return d
        except OSError:
            continue
    return APP_DIR


TOOLS_DIR = pick_tools_dir()


def find_tool(name):
    for d in (TOOLS_DIR, APP_DIR):
        p = os.path.join(d, name + ".exe")
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def seed_bundled_ytdlp():
    """The .exe build carries yt-dlp.exe; copy it out once so it can be updated in place."""
    src = os.path.join(BUNDLE_DIR, "yt-dlp.exe")
    dest = os.path.join(TOOLS_DIR, "yt-dlp.exe")
    if FROZEN and os.path.isfile(src) and not os.path.isfile(dest):
        try:
            shutil.copy2(src, dest)
        except OSError:
            pass

PROGRESS_RE = re.compile(r"^\[download\]\s+(\d+(?:\.\d+)?)%")
ITEM_RE = re.compile(r"^\[download\] Downloading (?:item|video) (\d+) of (\d+)")

TEMPLATES = [
    "%(title)s [%(id)s].%(ext)s",
    "%(title)s.%(ext)s",
    "%(uploader)s/%(title)s.%(ext)s",
    "%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s",
    "%(upload_date>%Y-%m-%d)s - %(title)s.%(ext)s",
]
RESOLUTIONS = ["Best", "2160", "1440", "1080", "720", "480", "360", "240"]
CONTAINERS = ["Any", "mp4", "mkv", "webm"]
AUDIO_FORMATS = ["best", "mp3", "m4a", "opus", "flac", "wav", "aac", "vorbis"]
AUDIO_QUALITIES = ["0 (best VBR)", "2", "5 (default)", "9 (worst VBR)",
                   "320K", "256K", "192K", "128K"]
SUB_FORMATS = ["(keep original)", "srt", "vtt", "ass", "lrc"]
BROWSERS = ["None", "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"]
PLAYLIST_MODES = ["Auto", "Single video only", "Whole playlist"]
SB_MODES = ["Off", "Mark as chapters", "Remove segments"]
SB_CATEGORIES = ["sponsor", "intro", "outro", "selfpromo", "interaction",
                 "preview", "music_offtopic", "filler"]

DEFAULTS = {
    # General
    "output_dir": str(Path.home() / "Downloads"),
    "template": TEMPLATES[0],
    "restrict_filenames": False,
    "no_overwrites": False,
    "no_mtime": True,
    # Format
    "mode": "video",
    "max_height": "Best",
    "container": "mp4",
    "prefer_compat": True,
    "audio_format": "mp3",
    "audio_quality": AUDIO_QUALITIES[0],
    "custom_format": "bv*+ba/b",
    "sections": "",
    "split_chapters": False,
    # Subtitles
    "write_subs": False,
    "auto_subs": False,
    "sub_langs": "en.*",
    "embed_subs": False,
    "sub_format": SUB_FORMATS[0],
    # Post-processing
    "embed_metadata": True,
    "embed_thumbnail": False,
    "embed_chapters": False,
    "write_thumbnail": False,
    "write_description": False,
    "write_info_json": False,
    "sb_mode": "Off",
    **{f"sb_{c}": c == "sponsor" for c in SB_CATEGORIES},
    # Playlist
    "playlist_mode": "Auto",
    "playlist_items": "",
    "use_archive": False,
    "archive_file": str(Path.home() / "Downloads" / "yt-dlp-archive.txt"),
    # Network
    "rate_limit": "",
    "retries": "10",
    "fragments": "1",
    "sleep": "",
    "proxy": "",
    # Authentication
    "cookies_browser": "None",
    "cookies_file": "",
    "username": "",
    "password": "",
    "twofactor": "",
    "video_password": "",
    "use_netrc": False,
    # Advanced / setup
    "ytdlp_path": "",
    "ffmpeg_path": "",
    "extra_args": "",
    "advanced_mode": False,
    "log_open": True,
}
SIMPLE_TABS = ("General", "Format", "Setup")
SECRET_KEYS = ("password", "twofactor", "video_password")  # never written to settings.json
SECRET_FLAGS = ("-p", "--password", "-2", "--twofactor", "--video-password")
# Options on tabs that simple mode hides; the header counts any that differ from their defaults
ADVANCED_KEYS = ("write_subs", "auto_subs", "embed_subs", "sub_format",
                 "embed_metadata", "embed_thumbnail", "embed_chapters", "write_thumbnail",
                 "write_description", "write_info_json", "sb_mode",
                 "playlist_mode", "playlist_items", "use_archive", "sections", "split_chapters",
                 "rate_limit", "retries", "fragments", "sleep", "proxy",
                 "cookies_browser", "cookies_file", "username", "password", "twofactor",
                 "video_password", "use_netrc", "extra_args")
FLAG_RE = re.compile(r"\s*\(-[^)]*\)")  # " (--write-subs)", " (-x)" ...


def first_token(s):
    return s.split()[0] if s.split() else s


# ------------------------------------------------------------------ theme
BG = "#17181c"
SURFACE = "#22242a"
FIELD = "#2a2d34"
HOVER = "#33363f"
BORDER = "#3a3d46"
FG = "#e8e9ed"
MUTED = "#9096a3"
DISABLED = "#5c616c"
ACCENT = "#ff4f5e"          # highlights: ticks, focus, selected tab, progress
PRIMARY = "#d13644"         # filled primary button; white text on it is 4.9:1
PRIMARY_HOVER = "#c02f3c"
PRIMARY_PRESSED = "#a92935"
BUTTON = "#2c2f36"
BUTTON_EDGE = "#555a66"
FIELD_EDGE = "#6b7080"      # 3:1 against the tab surface, so empty fields stay visible
LOG_ERR = "#ff7b72"
LOG_INFO = "#79b8ff"
LOG_OK = "#7ee787"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI Semibold", 10)
MONO = ("Cascadia Mono", 9) if sys.getwindowsversion().build >= 22000 else ("Consolas", 10)

# 20px checkbox / radio indicators and 40x22 switch (antialiased PNGs, base64)
ICONS = {
    "cb_off": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAB/0lEQVR42tWVsWsUQRTGv/d2JsdeTDiSIBLlMN4hV4iVhYWSSsw/cP+ASAqtLKxCmDuClYWVV6TwH9g2RSzFxsJKLETUhJAEEQlHvNtld2bnWdwFVs0ZE67xgylm4PvxZt7HG6AgESFjjMI/yhijRISKZ4WNEEACAM3mo7A8P11Ct3s8qVJBvH+YRtGz5HcvFQ/uPXw8PxVW2kqpu97LORFPf1RAJEQszNRzzr38kXTNi+dP948YZIzhdrsly8srF8Lp8hsOVHXr8wdYZ8FEEPm1OCLAi0ArjYVaAz53O8lhfHN9/clXY1qkADBATpfba0Ggqq9fbaYHB98nmBmQEY9HgPcee7vb2e3Fpaoul9YAug+YgABQs/lg8uLlS5+2vnw8//7dW4TlSRLxf20IESOJ+3Lt+g0sXLn6bW97tx5Fnb4CIFqLBhA6Z4k5EBEPETmhxx7MAZyzBCAcMoQBQOsJAeCJCKPveZwEAw/8kAEuZhBnVNHLGLP+IyARyVkhRS8DgLUZAeBBVE7TGzqKFw8ZUADIWrIAEqX0lPc5iBjAycH2PodSWgAkQwaxMSaIok7PWrtRqzdoZnYuS5K+ZFkqWTpiZakkSV9mZueyWr1B1tqNKOr0jDGBGpQiZOOVVaWCO7cWl04zHEp57nZsnK4Opk3Lj398jXvAYtxfwE9EcTxIAcYfpgAAAABJRU5ErkJggg==",
    "cb_hover": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACN0lEQVR42tWVzUocQRSFz63qHrvEMf4GYmAWgQh5g+xig2kZ8AncxqU7tzHtz9rdPIO7EQKGzoyBmo1M8gTCLLLIQgLCKM7EaqetulmEkY6KjsFNzvJCfdyqe88pICdmJq21hwGltfaYmfI1Lw8jIgZwub29rSYmXgydnp7eChobG0O7/f0iDENz7ewfYL+ws1OfmZ6Z2FBKLVhrR6xzRNc6YCKWQvBLOdvdb7ypHR+1YyI66jO8OI4FAK5Wk2dPpiabJETp095HGGMgpQQz/9UdEcFaC6XU+HxUXn76fDKqVpPXAH7GcSy8ubk5QUSXyf7BlhCitLXx4aLVOiz4vn8DlodmWYavzWZvLd4sDY+ObhHRstZaemEY2kqlMhKo4cVaLeFW67AwOTVNzto7ByKkRKt1WKjXE15YKC9WKpWRMAx/eQC40xn3wVCpMeT7Pjtr4Zy7d8q+7yM1hsBQnc64D4AFAARBj4nIiVve7C4xM4SUICIXBD0GAJFfGzwAlqMiv4sCj6z/CEhEDKKHE4jQt90VME0LxMzCWQt6AJSI4KwFM4s0LVDfy1QsnmQgmECpYpZlEFLefzUpkWUZAqUYBFMsnmQAyNNayzAMu8n+wV4Uld99azYHtt7s7KteFJWHUnO+t7Ky0tVae16j0XDMTLu7n9f8QvD2fbxZ+lJPBgkHzEflIefcj/OzszVmpvX1dUf/Gl9Syq4xpnZ81I6XlqKr+KJbAhaDBuzq6uqNgMVjfwG/ATsdTDTmKc7VAAAAAElFTkSuQmCC",
    "cb_on": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACW0lEQVR42q2VO2gUYRDHf/Pt4x5JfCTxWaRRiGCKFAp2h40phIBCQBC0UEijooWoWJwawcpXlT4gGK8wSEBik9bWQhDUQsEHMYiJl9vcvsbiS87N3XFaZGBh99vv+838Z2dmIWOKipZKLv9pWiq5ikp2Tf7CEAEF0LErBcKfuY40v7culYdB81nJLujoqb24hdsoI2jaTZpK1mnDtzGKmCrCHHFQlhdPv64zRMEAyonTu1HvNZ4/QByCahtWkx7Xhyj8jERHeP7kOyCGUtkIKImZwPcHCIM6aaKoKpq2vwQlTZV6UMf3B0jMhIBSKhtREMbGugjzHxBnJ5rSITQwBmo1yOdtqGJAkwX81f1UKis2uprnoRQ6ghqwAA4NW8lJYp0rBWqeJ6AGANdTIN1wWJrYrgu/lmDkKNy9AZfOQxyv70vXGJh2JYSR7GZwHFhahuEhGD8DqcLHT1mnjRvTIksEVgLYthXCyEb2uwpDB+DWVSgWYHoGpqYhn1urhkxWNj4JrNbh8DA8moDRY/DlGwzugzvXYEsPzM3D1DPo77ORNllrmyUJ7NkFfb0wfhZ29MPBQejptrAHk1AotETWDqikCl1FmHkJy1W4eA5OHrdv37yF+5NWsu2rpkrPAuNIcBwrX9VKezUPC4tw8zL8WIR7j23OjIE0bSko4kgAXAWhGEWETgD0NGT3bod37+HCdftxwhA8rx1MEQKKUaRrredIpVJFmcX3BTQElDhW8jmlFihJoniebTcrT60UDfF9QZmVSqVKqez8ezhIc5Y6D4fNH1+bPWDZ7F/AHxdQNJMUbZMEAAAAAElFTkSuQmCC",
    "cb_dis": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAABmUlEQVR42tVVsWobQRB9b/Z8xzkXSRAwJgFVqdwZNyIQtsyP+D9C8C+5SXkEghrj7qpUgQRjCCjJWcedb/elsIRzKoQRSpEHC8uw83Z23s4MMAS99wmeiNVZDgg29gKA2WyWN02WAQsAkw2aB1uet+18Pm82ffm34fT0zUuXZh/M+E5CIYmEBhEIFEmRqGPUx9C176+vP39fcxCAAdDZ2dtjJsncnE3r3z+hGDdfM6Q1Q/F8jBjiV/X97Orq0w0AJt57K8uyV2IXztn09uZb23VtSnJr/iRheVd3R8evpj3sAsC5994RAE9O/LPDEb8s6/posfgB5xKuUrIFRAi9JpMXOCyK2+Uvva6q8s4AyLlwACGXxIfI9ASNBZKQRAi5c+EAgAwAzJwIRuwIgtHMCStB1jcSO+PR17Bn/FeE1O40j74GADEGaqX4TpJAFmPgmpAhuHsQDUlJ2lJyw88iCSQFognB3QOgee9dVZV1lC6L0ZhpmnUh9Ioxbl0h9ErTrCtGY0bpsqrKel16e20O/6R97bXBYt8j4A8W0AcyP/2hkAAAAABJRU5ErkJggg==",
    "cb_dis_on": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACmElEQVR42q2VTWsUSRjHf0/120yPmoRhl1XceAloRnAxl8S99GUht70NCPpdgvhZFsW+iQjB/QC5+3bJjKCQWZY9JZPp6erqevYwmXGSTKIHC4p+uqj+1fNW/4ZTQyXLspDvHJO9KvNrcsZWgG6327Q2TS6DxfGozPO8OPutzC/88efDG4kJn4Buo3rFey+CnPJAUTXGKCJDkN3Su52/Xz4/mDIEMIBubz/6JWjoXhwlq85ZVPVMAKexIkIYxtiq/FyPZWt3969/ADFZtmMANUn9NI6TVWuL0nuvk+EvmKree7W2KOM4WTVJ/RTQLNsxAki3220dl+G+iPlZ1XOJa4gIzjmCIJhUUQyq/t9W4tbyPD82gI5GUYTSvAw0D2u325PA9aQOSnM0iiJADUAYRgr4y2DGGKy1XL9xnc2tTe6s38F7Py2ZP2FgFrWQiExPn71ba1lZWWF9fR3nHMOjIfK1AWaGWeSNc44oik48EFzlWFpe4v7GfZrNJvv7+/R6PYwxzJ17HigCdV3Tbrd58PsDbv56k2JUcG3pGhsbG6RpSr/f51PvE41GY2Fqzl0zVaWZNknTlE6nQ6PRYHl5mVarRb/f5+P7jwRhcColFwFVFcIw5MvnL1S2onO3w9raGggMBgM+vP9AGIaLO30e6FwlmK/hR1HE4GDAeDzm3m/3GBdj3r19RxAE5wo2TZ1zlUyBkqZVdVyGBXB16mqcxBweHrK3t4evPd57gmBhqIpQpGlVTa9ekOf5UNFXURwLqJ0wJyJQu3pm64Q2N9VGcSyKvsrzfJhlO8G3xWEmTPNicbE4BNOdvd7bo1u3774QZQn4CfDe+xKlRHX2VPVjESkQ+c97n1utH795/WwwL18/VGD50b+A/wFa/4IdEXqtTgAAAABJRU5ErkJggg==",
    "rb_off": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACqUlEQVR42rVVzUuUYRD/zfO8H7u2uyrksn3RGhYkUmlFUBRSty6dXjruoYPWRfAgdHraDgVSQkEvWpcWvPUPdKm06FJRELKFSnopdNtDubvuvh/7TIe2jFJRsIE5zMzzm4GZ38wDrCFKKaGUMlbxG0opgU0IOY4jV0xHZgZUS2ZAtQAr/sYb+ge8is0A0N9/tcuOxQekIU+x5hQAkKCFelh/6ZVLd0ZHb079jfk7ITEz+vr6DKtp57Adabri+55V/LqAUuk7ACAeb8b2thQsy/a92rLrL38ZGhsbC4kIv5I2EjIpdY0mJiZEV/fZJ9tisTP5qbeYn5sNPa8q/ijMth3V6fYOo7OrB5Vy+fnUu6fnent7dTZ7jQFi+bMfeem6rj5/IXM7Fk9cfPPqRTA780EIIaRhmCSl8Vu1rovFhc+8XCmH6fb9+5pbU4lbw9cfO05e5vN5lkop4bquvnR56GA8lnj48cN7np3Jy2i0iVadGBEsy6JicVGAoXekdp3oPNTzaDz3oKCUEgKAAIBoJD4YhIGcn5tmy7JJa70mDbTWsCyb5uemOQgDGY3EBxshQQAok1F2S9KaKix82ffm9Qu2LFsw8/rcIoLve/rY8dOUTO389K3gd+VyWU8A4CAoRMFILi19IzBow4xlUAOTDIJCFABvivEbEQGATDNZBaGQSLQwCLzxnQI3MAXTTFYBkFBKyVwuW6uH+llbMkW2HdHrDeTPwdh2RLclU1QP9bNcLltTSkkBQANAtVYaMQ2znm4/QL7vsRBrd0MIAd/3ON1+gEzDrFdrpZFfdeTk5CQ7jiPHcw8KR46ebN29J31yuVIOi8VFIaUkIQSI6LcyMzyvxnvTHeHh7hNGpVK+e//ejXHHcaTrunrLV+9/HYetO19bfmCx1V/AD/5zdIijHZ4OAAAAAElFTkSuQmCC",
    "rb_hover": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACz0lEQVR42rWVT0hUURTGv3Pv480MzPQ0/wUjRC6CnHHdQqg3+G/jogizqYXMxkWbqIXraRsklNHCzdCizCcyQgVZxjyCFi2qhZPujMCBtHnWawbGnu++00ZDzULBDtzFPdzzu5fDd74L/CWYWRQKBW13vlAoaMwscIAgy7Lktr3M5fN1uXy+DsDv/OYZ+qN416uIiBgAHk/PJpuaGq/peqhTcXAMACSJL573883Xr+U7ly70FXfX7AAyMwHA+Pi41tqWvGUY9Vd/uN/1hY9FlErLAIB4vBXtiSSOGHWe6367v7xUHBkeHvYBYAtK22CUzWbF6c6+V/UNDWemJh/h1dwL33VdsXUZEbFhGEFXd682MHgZ3xzn9ds3s13ZbDYAwETEEgASiYRMJpPB9ZGbt5uamwfv37u78ezJjJBSynA4TKFQiEKhEOm6ThsbG+LD+3dcLpf9s2aqLWY0Hjl18sTzRCIhp6ammJhZEFHwYOLpqbYTx+dn8tOYnHgojjY0kFIKzLyz6USQUmLNcXgwfSU4d/4Clj597hhK9y8ysxC2bQsAaGlpvFGpVuXcy1mOxmJ7wjbbA6UUorEYzb2c5Uq1KltaGm8AgG3bQpimqXK5XFjX9NRCcZ5d1xWapu0J2w7VNA2u64qF4jzrmp7K5XJh0zSVICKu1fQIgOZSaZmYA9qvYJkDKpWWCUBzraZHiOhgit9PCGamSMSrAViNx1uZSPC+R4oEx+OtDGA1EvFqzEzCtm2ZyWTWPd8rtCc7yDCMwPd9ENE/QATf92EYRtCe7CDP9wqZTGbdtm0pTNMMAGBlpTwai0ZVd08fVSsVllLuCd2STbVS4e6ePopFo2plpTwKAKZpBoKIAsuy5FC6f9FxnLGBi2mZ6ur11xyHlVIQQuxYSimsOQ6nunr9gYtp6TjO2FC6f9GyLElEwaGP3v8xh8O0r0M3WBz2F/AL+iqwvaXnQAsAAAAASUVORK5CYII=",
    "rb_on": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACmklEQVR42rWVP4jcRRTHP983c7u/3VOiEILNiaiFQqwUFAK3AQvBImKx2ESihQQFUVMIVj+2EgwkSoJiIbiYbgvhCiGFJCcEFLQyoBFPJDYSAh7R3O5v9zfzUoxniMl1l9fM8P58hjfz3hvYQbyuzQeDeJt+MIhe17ZTnG4LADEcmiaTBODDYaDfvxeAra2/b9FPJlngOwIdtO3gLx7ejy+9hfwA7g8Ub/2J6wJafKQvz1z8f8wtQIeif/JoZGX2AQpvEEKHnCDn4mQGFiClOZ4+5o/qXX74tAWxDdV/sLoW588b9z34NZ3eKvMZ4C0yw1QOzu54zqBIp4L59Bs2Lz/LwYOZ0cgFXi53ODSNRpk9K8fp9lZppgvAsRBpGmPzmti8JprGsBABp5ku6PZW2bNyXKNRZjg0ADm1iVH2519+nE74EXfADUlc34LHHoVnniopf/s9/PwrLPfB3UEZCebpCX31xU9ObZEBxjqZyDFiDCyaFgvin+tw6Dl4/RUIoQBfegE++RzWzsI9yyInJ3YjOR0DXmOAyUEcOdLlr3yREB/Gs9M0xiMPwan3C6hNZY3/gt98DzZ+h243IxOp/Y37bT/jcWMC5+qih7MPd2ESzRwOPA3u0LYFFEPZuxdbMweTcBfOPq4uejcfZRfFHMTepSniCpKT3el24MJ3pWRjLCm3qeylYut2ShlJjrjC3qWpg4xBHTQez4BzhCA8Z6oKLm3A6c8gpZspp1R0lzagqsBzJgQB5zQezxjUIbJOaYOWE1j7KpjI2Vnui7Wz8MvGncsmZwcTbZtoOQHAOlnbja7JJPmhwyep+m8zmy6QImZiNisPACXNqiow95aqt8Rs60OtnXlnm7HrrXd3hsNdGV+7NWDZ7S/gBlIkdzKudFwWAAAAAElFTkSuQmCC",
    "rb_dis": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACHUlEQVR42rVVwUobURQ9996ZZEYTnSYQuiu4K1iC6E7wCf2Gof/QTaGLboPbQhdF8AO66Dd0U3QC3SlZVMyuaxFNxSRmknnv3S6UEqy1EdKzu5d7Drz7zjsP+CtabIwJ7nZvei3GI0BpmspULc2mSZpNkwD43b+doT/I99QKABsbZpWF34BpUxVPAYAIp/D6zTv/8fAwO77LuStIALC+vh6Aq+8lkNeqvpTnI9hiAgAIwhKiKAYRT5x1e/D9d0dHR/aWr9OChFaLzMEBD0b4GoalrcvLC1wP+9Y5x1NzKiJ+YbEaJEkdRTFpV2K8zLa3PXZ2FIDK7UIEe3u+1nj2oVQqvzo/Py2GgysmghAxTQNQHucjLYqJrVaXV65zu3T6+dMXpKng5EQJAAPwa2tbz4Ny8H3Qv8TV1U9mFnro5rx3urT0xFeqCezYvuh02l0AzMYYBgAu8VtAZTjo67/EAIBZaDjoK6BywwWMMUwAyBhTHuZ0PB7nK72LM2XmmXzmvfe1eoPK5ejHYqSrWZaNGYD2ei6GomGLgu7z1kOetUVBUDR6PRcD0Ec5fhYwAKrVZATCWRCGOm3SGaBBGCoIZ7WajAAQG2Mky7Jcgf0oiklY/KxqwuKjKCYF9rMsy40xMn/bAPBIU+l02l3r7O5yUpd4oWK9d3r/6RXeO40XKnY5qYt1drfTaXdxExZ+7k/vv4XD3OJr7gGLeX8BvwBW+DZvFd/qYwAAAABJRU5ErkJggg==",
    "rb_dis_on": "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAC0UlEQVR42rVVPYhcVRT+vnPuvjcT1myU3afipglOYUiqwDIo5C3YxWKxeKQRBivBRkhh+9hWMGDAAdFmIE3ShBT5aSQ7AWGbVIaxszGivh0XZnYx83PvPRZvV2SNhTCe7px7fjjf/e53gX+xsiwlz3N3Mp7nuSvLUvAfjEVR6LFTFIVubXXObG11zpyMA+A/il/gGwC89/4HF2D4hGbvGOy1+pC/GvkdiC/u3bn59GTNyYYEDJcufeRefePwMxH5WFWTEAMsxjpBBCqKEMIsxtj97eflT588+coftbG/N2RZltzZ2ZFTp1//Nk0bl2ezKQDzAIVkXWFmgEWALklSTKeTx3+Mf3l3c3Mzbm9vGwDTYzy63W48f7H9edo8dXU6ncxJCikaY6T3niEEkqSICgDz3vu00Twnkp7+5usvHxZFoYPBwAiUAmzHK1euvsUl972ZATABQO89VlZWsJatAQD2qj2MRiM4545WZCQJm/uL9+/f+gEoRfIcNQWU15xzCkQjSe891s+uY6O9gVarhVarhY32BtbPrsN7jxqGaM45hfJaTSkIAbDT6aR7+7OnonouxmAxRll+aRntdhtmhnh0KSICktjd3cXhwSFEJIooYwg/rr2SXOj1elMBYMPhvGmGzMxIkiEEZFkGVUWMESRBEjFGqCqyLEMIASRpZjRDNhzOm8dYLdQEAFdXl56TqOqBZqqKqqoQQoCIwMxgZhARhBBQVRVUFVavZCSq1dWl5wAoeV5qr9ebAPZIVQlYVFWMR2MMBgOAQJIkSJIEIDAYDDAejaGqOMolYI96vd4kz0t1/T5qxINd995/CAjNzJxzfPbTMxyMD15Im5rkQu99QLDrANDvIyrQt6Io9O7d29WbrfMvp43m297PPUlRVU4mEwyHQ+z/vo/5fP4XB83Mp42G87PpjQf3bt2sid2NC396/5s4LEy+Fi6wWPQX8CdCp6SyFCK60QAAAABJRU5ErkJggg==",
    "sw_off": "iVBORw0KGgoAAAANSUhEUgAAACgAAAAWCAYAAACyjt6wAAAErklEQVR42tWXXWwUVRTH/+femd3tzrbb/aAFpNhuS6GVipBIJDyUB4WQoIbYLfGZxIhBQp+NLAvxRR/aoAFeiIlvbDVglCbii0SMmGiMlBYo3Q/KZ0u77W53dtqduff6wJc1gLXLA5xkksnN5Px/53/m3pxLeEREowne29sp/rHEd+6M+S1rTNkVJUIZoVsuVVFRQ8eOxXMAxBM0AQD0OLjW1lbXps2dOzwuz5sgrITCEqkkFFRZgARSjBhAuAWFyzOlme9+Op04Pjg4WHoU5ByxWCzG4vG43LX7ox0VRtU+znlrwcwjO3EHZmEajLEH3yqlFgwppYRh+BAM1aCy0g8hxKBl5g8c+eKT4/cZ/g1I0WiC9fZ2ig/27u82DN/eO2O3MHTpgjM6eoNsu0SMcQIAIQQYY3C53AAUoBZspeJcU4FAWDWvWq3V1C6FaRZ6Dvfs77rnpATuteu+tbv27Ouu8lfvHej/w7l8sZ8JKVhjUzMaIisQDAahAExls0ink8ikk+BcA+dswW4qpeA4DgDIphUtsm3Nei2fm+w5cujAfUhB7e0x7cyZuPP+no/fqar0fz144U+7//zvWiAYoje2bEN9QwSccwhx13XGGaQQSKeSOP3D95ixLOi6vmBIortNtKyiWtWyxlndtk7PT+c6jh46+E17e0yjWCzGstmsTq7agXx+svGXn3+UXsPH3nq7A8vqlqNYNKHUw0RKKRAAr2Hg+rURfHuyF6XZ2Tn/50JBbbskN2x8nfn9gaQqjb4UDAZtFo/HpUCgw+3xNA5d6heOEGzzlm1YVrcchUIBRAyMMRARiOjuO2MoFApYVrccm7dsgxDiQQHlBbGhS+eF2+NpFAh0xONxyQBAd+nbzcK0Gh29qZqamlHfEIFpmuCcPzYV5xymaaIh0oj6hghmZ2fLglRKQdM0TE5OqEI+p3SXvh0AWLSrq4IYb5mYGCPbtlkksuKJYI8CrY80QUpZtotEBCEcNjExRsR4S7Srq4LZ4+NeBbW4aBbAGKPqQHDeYkQEKSUC1UFwzss6Gx9aCSoWTSioxfb4uJfhGQ+mh8NFAt32Gj5IKdXUZBaMze9sU0qBMYbJqezT2ygE5fUaINBtPRwust7ubktJcTEUqlG6rstU6gqEEPPOJ4RAJjU876L+q2DONRkK1SglxcXe7m6LAYBdsk8YvkqqrV1Kw8NDyKRTMAzjiaBCCBiGgXQqiUw6BbfbXRYgEcFxHAQCIfJV+cku2ScAgMdiMTZrTV4WyvOur7IqdONaWo6MZGjJkhcQXrQItl2aI6yUApSC4fPh+rUR9J06CfWUdrCUQr6ybgPXND1JYnz31q1bFQc28UTiU3vtqxtvBIPhTiWlM5JJspGRDAUCIYTCi+B2u8EYB+caXG4XGGNIDl9B36mTmLEsaJpWFhgRwbKKqnllm4hEmnnRMt87/Pln/cAmzq9ePSOj0QT/6ssPB15e+1p13YuRjUQkbt28joGBv2hs9DZmZmZgl2aRy00hk0ri3K9n8du5s1AK0HVtjtD/eQDAcRxIKWXzytWybc16fTqf6zl66GB3NJrgfX27xfMxbj0PA+vzMfI/i5emvwF+z71QApIX7wAAAABJRU5ErkJggg==",
    "sw_off_hover": "iVBORw0KGgoAAAANSUhEUgAAACgAAAAWCAYAAACyjt6wAAAE+UlEQVR42s1XXUxURxg938zdhb0LCIUFliVCosDaplBAYjB9oLZpm1SoID+1jdEHaWxiuxgerGlSYlPbJi3RxqSN5aGpTX9YtbS0Ju2LQtqKKGgFhRVjUhN3UVxAurDL7r13pg8sVI0/FGLC93Rz5975zsyZ831nCPcIt9vNa2trjdte8Rb3r8s0/6gMhYKERYTFokpTSjLV1744AcB4QE4AAN0PXE1NjXlLfUNdfFx8ORjlQUq7YRgz/8hFICRIzjlANAwhLwUmAz9/1bK/9fDhw5F7gbwDoJSSEZE40n68Li3N9i5nyuPDwz4MXfJgZGQEisJnvgMghVgwRsMwYLOlIjfPCYcjE4bQB27cuPledcW61lkMdwOk6IDxy29/7EtOTmno7+9D+08/6P3n/6JgMEhc4QQAuqaDcQ5VVQFISCHvwcM8NpJIxsTEypUrc2TFho1KfkEBRv3+/etfeHqnlJJHQUolSisjIqP9WMc+my2t4btvD+k/th1hmqYrpaVrUVJSggxHJgDA5/Oit6cH586dhclkgqIoEAvbTRJCwDM4gMHBAbG+/GXx2uatDe3HOkBEO2fppqamE8qePc/obe3HN9odjiOt33+jfX3oSyUrK5t2vOlCUVEROOeInr+5596eHhw48CkCk5OIjYlZKEgwxgAAY6OjsqZuk77p1c2mYa+3urJi3dGmphMKLyvLRlVVlTkzO6fN4/E81nLwM7LbM9jbu99BYWEhAoEAwuEwdF2HpmkIh8PQNA0rc3LgdDpxuvsUwpEIeDTR/w0pZxSnqipdvNBPOTl5lG7PKC4ufOJgKDRo8M7OTrn9rd2vJCYmbWv54nPD5/Xy3VFw42NjUBQFjDEQEYgIjDEwxhCcmsLy7Gw4Mhzo7DgOxWQG5MLlTUTQNY38fr/x7HPPJ5ti4z1vvL61jwFAnGqtHB72yf6+87K0dC0Ki4owcesWFJPpvhMqJhMmxsdRvHo1CguLEZyamqNrISGEQExsLK5cuSy9vmsyTrVWAgBrbnZbmMJXDQ15KBQMspKSEnDO51XqZPRMFhcXQwgBokXVcDDGEA5PsyGPhxjnq5qb3RZmNodUAtJvjoyAc04OR+a8kxERhBDIcDhm1CwlFhtSSvL7b4KAdLM5pDIs8WCRiCUogeu21FQYhiG93mtgjM2p62EKZIzB5/VC13WwRVI8W8BTUmyQwPVIxBJkjY21IaEbg7m5TmlRVXHmzBkYhjGv5kDRttXb2zvvRT1UKDGxItfplMIwBhsba0MMACaDU212ewY9mV9AXV0nce7sWSxLTISuafedTNc0LEtKinaVXqhW64KL9ZxApqexYkUOOTIyaTI41QYAvKmpiUVCE5eSktM3paXZk0/++bvo7++jvDwnlmdlIRQKze2MlBJCCEgpkZSUhIsXL6D5k49hCLkoemdrrKZpYlv9dh4XH3/l8sDpHWvWrJEMKGMulyscmPhnV35+ASqrqo2rV/+WH324F93d3bBarUhISICqqlBVFQkJCbBarTjV1YUP9r6PwOQkTIqyYHpnm8DY6Kgsr9hg5Bc8hcDExC6XyxUGyhjd7gHbj3XsS7M7bjcL7BGaBQghEJ6ehgRmzYJyw+fdX/FS2X9mYanbrSVvWJW7ViXcbjevrljXWlNT07alvqEuJcVWnpKa+sgs/9j42B2Wn4iMB95Jltql6V93cNEsydtEYgAAAABJRU5ErkJggg==",
    "sw_on": "iVBORw0KGgoAAAANSUhEUgAAACgAAAAWCAYAAACyjt6wAAADzklEQVR42tWXTWhdRRTHf2dm7kteeElqVIxiiVGJtEIp6UeSmjaRrBWKpkXqogtXRbFKoQjFoHQrrSsRXeqiRsmim25Ck7Zp0ighCLZU0tZStAGxTfJe8/HuzBwXL/2wJGjyEJo/3MUwM/f85sz/zpwrLCHt7rbS2xsebN+4MV075RIt+FQoQzmX6Dqfyvr1tdMPx3iwfVeyHNx3GzdmXs49s9eKec2rvqTo00EVRMoCBFWHAHLTiVwOGk/+UvjjxJ6LF4tLQf4jmNJjhE/i+NauvZXOflxh7EZFSaPiNSKArgJJELDmLh8KJMaQiIEYWYjh4rwPn27+qf/EXYaHAUW7u4309obxbV3Hci45GFSZC8GDSinCajKnICWwWLiDhoAkrtSTehVr1OZyWmmssygF749v/rH/g8VMRkDlwW0d29Z1rD5TefDP4oJX1Igsvn2VEmOIaYoWU2p2tlKzs43KFxsBmJ+4xszZYfJnR9DERZtk4hMucZPF+ePN9yGDnO7ocK8ODvrxlq43cib5fjb4NIKTJfy5UrgwN497rJaGo0eofmX7kuPyQ6NcP3IUf3tKXVXWZzFJIaZvbr7Q/8Ppjg5nn9u/n69+z2bSrPZZkbpijGLKzBwiqPe4mmqe/+Izcs2b0BBB4z0fEiMoVDQ8S65lC9P9g+Jn58U5Jz7qlg+zTV+efL09CMDPrV37akzyze00DUbEUq6MId6Z5YWvP6d6ezPqPeLc0i5d7MuPjnHlnfeRqmxYZ52dicW3N430f2sANMhuVVQQLZdNjCHmC9TsaivBhbAsHIA4h4ZA9fZmana1oYWCYoxqkN0A5nxrd1ZhQ1GjIJiysyeChkBtZ/uKp9Z2thNDMEWiKGw439qdNTbcqgLqveqSB/fKj2FFkoSKxoZ7wP9lUQAVjQ1IkogPEaDehltV5Wfsf5YJtm4WmHSlVZTvQRE0TVm4dv3+F/vvaQdg4dp1NE3VlW6dyWDrZs2Okd45gUsZMYoSKX+PEWuZHji34qnTA+cw1sYMRgUu7RjpnTMAYrVPBNHFm6Usvhgx1TlmzgyTHx1DrEW9X36894i15EfHmDkzjORyQowiVvsAbE9Pj2m+cPNyvsK/VWXd48UYo5RdsZSMPzM4RK5lK5mnnlz2oBZnmb30K78d/IjoQ6xKElsI/kr2r+Tduvf2qekcGDBNE6cWED1sRbAiQcv1oiomSfBTM1w9cIj80ChiDWItYkzpsRaxhvzQKFcPHMJPTaurSIItlS6HmyZOLXQODJhHv1hYE+XWWihY10bJ/yj+NP0NtilyyFLjdgMAAAAASUVORK5CYII=",
    "sw_on_hover": "iVBORw0KGgoAAAANSUhEUgAAACgAAAAWCAYAAACyjt6wAAAD00lEQVR42tWXT2xUVRTGf+fc96btTCelpZq6MCEpRikJ6KKhVGCgSxIXRAsxGFeyMBoCC2Vno0s3EDduXBldQDUs3LBqKa3tEBZaTAvENiFxIYmlHZzOtJ337j0uhhqCJQGmJvAlb3Hz7p/vft/3Ts4TNsCFwUF3dHjYPzju+eNe21KlahWfCg0g5yJrz2Vl9uW2ew+f8eB4HfIochd6ejJdTR3HItxbPoRXgZdSC8gGa54EBhaJAvzpVG+l+J/urC2ePzo7W9uIpDy0WAXCyK79x5qdfNYk2mNAYgFv1ggvRF39NDMMiFSJRcAH1izMrnr7YuD6+Pl1Dg8TFBscVBke9pdf33c2q+6UN2PN+9RARETsaZUTQYB0uYJ5j8RRXYwkMXHOotacNauLFKgGf+7grxOnbXDQyfBwAEwetHVkV//ZFzLNp+7W1lIDFal78fSqKSFJCLWErYV+thbeJPdKNwCV3+e5O/Yzi2OTEEfBZeLQ4eLor9rquYHrk6fXOclooRAdGhtLR3b1v52PMj9U0jQxIWo0a6KKX1khbt9Cz5ef07Fv74bzFiemmP10iNpSyaKWljQrEpfT5J2B65M/jhYKkQwxpHu2X41bssszzU67V70PjSqHCJamRK2t7P7mK/I7X8N8qKdc7t/bDBDEKeWZm0x/cJKkXA7ZpiZdSf38SrV159W5PYkAjOzuP97mMt/9nSZeRBwNQlRJlyu88e3XtPf1Ykn6b/b+81Xff7dUvMYv73+I5rK+TZ0r+dp7A9OT3yuABDliYAq2KeTKZToP7a+TS/0jyQFIHGGpp72vl85D+/HlZcM5kyBHAHSyr68FYUdiQaxRawFUCKmnc+DAk13XoHPgAOa91iwIwo7Jvr4WrZR8VqDL1+uTNEwwGJqJyXZvq++m8liXQiDbvQ2JY/EhINBVKfls44r9z9DcFlc1uOPqBdU2xeJaQnX+dn23YI+lOgbV+dtYkphTxeBObouran+xuIJxIxY1MQubYnHkWBi58mSBEVgYuYI4FzKihnGjv1hcUQBTuyggYRMyaCEQ5fMsjI6zVLyGRA5L0kfPT1IkciwVr7EwOo7Ltwrei6ldBHBDDGlXfuFWcKvvNju3NQ0hiIg0Xm6Eu6MTtO/tpanrxXqhXjfIDEKo1+3IUZ65yW8ffULwacjGsVtO/fxqNf/x9sXjpgcLl/Xw3KU1I5yJRBHEW6NZNEPjmKRUYvrESRYnphCniHOIav1xDnHK4sQU0ydOUiuVzGUy3gFGOHN47tLawcJlffabheei3XoeGtbno+V/Fn+a/gEP+Wc6iPfj8AAAAABJRU5ErkJggg==",
}

def apply_dark_theme(win):
    s = ttk.Style(win)
    s.theme_use("clam")  # the only built-in theme that honours custom colours everywhere
    win.configure(bg=BG)

    s.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER,
                lightcolor=BORDER, darkcolor=BORDER, troughcolor=SURFACE, focuscolor=ACCENT,
                selectbackground=ACCENT, selectforeground="#ffffff", insertcolor=FG,
                arrowcolor=FG, font=FONT)
    s.map(".", foreground=[("disabled", DISABLED)])

    s.configure("TLabel", background=BG, foreground=FG)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Title.TLabel", font=("Segoe UI Semibold", 17))
    s.configure("TLabelframe", background=BG, bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                relief="solid", borderwidth=1)
    s.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=FONT_BOLD)

    # Buttons: the clam focus ring (focuscolor) marks keyboard focus; the edge marks the button itself
    s.configure("TButton", background=BUTTON, foreground=FG, padding=(14, 6), relief="flat",
                bordercolor=BUTTON_EDGE, lightcolor=BUTTON, darkcolor=BUTTON, focusthickness=1)
    s.map("TButton",
          background=[("disabled", BG), ("pressed", BORDER), ("active", HOVER)],
          bordercolor=[("disabled", BORDER), ("focus", ACCENT)],
          lightcolor=[("disabled", BG), ("active", HOVER)],
          darkcolor=[("disabled", BG), ("active", HOVER)])
    s.configure("Accent.TButton", background=PRIMARY, foreground="#ffffff", font=FONT_BOLD,
                padding=(22, 7), bordercolor=PRIMARY, lightcolor=PRIMARY, darkcolor=PRIMARY,
                focuscolor="#ffffff")
    primary = [("disabled", SURFACE), ("pressed", PRIMARY_PRESSED), ("active", PRIMARY_HOVER)]
    s.map("Accent.TButton", background=primary, lightcolor=primary, darkcolor=primary,
          bordercolor=[("disabled", BORDER), ("focus", "#ffffff"), ("pressed", PRIMARY_PRESSED),
                       ("active", PRIMARY_HOVER)],
          foreground=[("disabled", DISABLED)])
    # Quiet text-only buttons (Clear log, Show, the log toggle)
    for name, bg in (("Link.TButton", BG), ("Tab.Link.TButton", SURFACE)):
        s.configure(name, background=bg, foreground=MUTED, padding=(6, 2), relief="flat",
                    bordercolor=bg, lightcolor=bg, darkcolor=bg)
        s.map(name, foreground=[("active", FG)], background=[("active", bg), ("pressed", bg)],
              bordercolor=[("focus", ACCENT)], lightcolor=[("active", bg)], darkcolor=[("active", bg)])
    s.configure("Header.Link.TButton", foreground=FG, font=FONT_BOLD, padding=(0, 2))
    s.configure("Accent.Link.TButton", foreground=ACCENT)

    field = dict(fieldbackground=FIELD, bordercolor=FIELD_EDGE, lightcolor=FIELD, darkcolor=FIELD, padding=5)
    s.configure("TEntry", **field)
    s.map("TEntry", bordercolor=[("disabled", BORDER), ("focus", ACCENT)], lightcolor=[("focus", FIELD)],
          fieldbackground=[("disabled", BG)])
    s.configure("TCombobox", background=FIELD, arrowsize=14, **field)
    s.map("TCombobox",
          fieldbackground=[("disabled", BG), ("readonly", FIELD)],
          background=[("disabled", BG), ("active", HOVER), ("readonly", FIELD)],
          foreground=[("disabled", DISABLED), ("readonly", FG)],
          selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
          bordercolor=[("disabled", BORDER), ("focus", ACCENT)], arrowcolor=[("disabled", DISABLED)])
    win.option_add("*TCombobox*Listbox.background", FIELD)
    win.option_add("*TCombobox*Listbox.foreground", FG)
    win.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    win.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    win.option_add("*TCombobox*Listbox.font", FONT)
    win.option_add("*TCombobox*Listbox.relief", "flat")

    # Replace clam's tiny indicators with image elements; keep references so Tk doesn't drop them
    win._icons = {k: tk.PhotoImage(master=win, data=v) for k, v in ICONS.items()}
    i = win._icons
    for w, p in (("Checkbutton", "cb"), ("Radiobutton", "rb")):
        s.element_create(f"Modern.{w}.indicator", "image", i[f"{p}_off"],
                         ("disabled", "selected", i[f"{p}_dis_on"]), ("disabled", i[f"{p}_dis"]),
                         ("selected", i[f"{p}_on"]), ("active", i[f"{p}_hover"]),
                         width=28, sticky="w")  # extra width = gap before the label
        s.layout("T" + w, [(f"{w}.padding", {"sticky": "nswe", "children": [
            (f"Modern.{w}.indicator", {"side": "left", "sticky": ""}),
            (f"{w}.focus", {"side": "left", "sticky": "w", "children": [
                (f"{w}.label", {"sticky": "nswe"})]})]})])
        s.configure("T" + w, background=BG, foreground=FG, padding=(0, 3))
        s.map("T" + w, background=[("active", BG)])
    s.element_create("Switch.indicator", "image", i["sw_off"],
                     ("selected", "active", i["sw_on_hover"]), ("selected", i["sw_on"]),
                     ("active", i["sw_off_hover"]), width=50, sticky="w")
    s.layout("Switch.TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Switch.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})]})])

    s.configure("TNotebook", background=BG, bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(16, 7),
                bordercolor=BG, lightcolor=BG, darkcolor=BG, font=FONT)
    s.map("TNotebook.Tab",
          background=[("selected", SURFACE), ("active", SURFACE)],
          foreground=[("selected", FG), ("active", FG)],
          lightcolor=[("selected", ACCENT)], bordercolor=[("selected", BORDER)])
    s.configure("Tab.TFrame", background=SURFACE)
    for w in ("TLabel", "Muted.TLabel", "TCheckbutton", "TRadiobutton", "TLabelframe", "TFrame"):
        # widgets inside a tab sit on the slightly lighter tab surface
        s.configure("Tab." + w, background=SURFACE)
    s.configure("Tab.TLabelframe.Label", background=SURFACE, foreground=MUTED, font=FONT_BOLD)
    s.configure("Tab.TLabelframe", bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE)
    s.configure("Tab.Muted.TLabel", foreground=MUTED)
    for w in ("Tab.TCheckbutton", "Tab.TRadiobutton"):
        s.map(w, background=[("active", SURFACE)])

    s.configure("Horizontal.TProgressbar", troughcolor=SURFACE, background=ACCENT, bordercolor=SURFACE,
                lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)
    s.configure("Vertical.TScrollbar", background=SURFACE, troughcolor=FIELD, bordercolor=FIELD,
                lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=MUTED, gripcount=0)
    s.map("Vertical.TScrollbar", background=[("active", HOVER)])


def mask_secrets(cmd):
    """Copy of an argument list with passwords replaced, for display and logging."""
    out = list(cmd)
    for i in range(len(out) - 1):
        if out[i] in SECRET_FLAGS:
            out[i + 1] = "********"
    return out


def style_text(widget, **kw):
    opts = dict(background=FIELD, foreground=FG, insertbackground=FG, selectbackground=ACCENT,
                selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1,
                highlightbackground=FIELD_EDGE, highlightcolor=ACCENT, padx=8, pady=6)
    widget.configure(**{**opts, **kw})


def dark_title_bar(win):
    """Ask Windows 10 (20H1+) / 11 to draw a dark title bar for this window."""
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new id, then pre-20H1 id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        win.withdraw()  # force the frame to repaint with the new colour
        win.deiconify()
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        scale = self.winfo_fpixels("1i") / 96  # Windows display scaling, e.g. 1.25
        w = min(int(940 * scale), self.winfo_screenwidth() - 40)
        h = min(int(860 * scale), self.winfo_screenheight() - 80)
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth() - w) // 2}+{max(0, (self.winfo_screenheight() - h) // 2 - 20)}")
        self.minsize(int(760 * scale), int(600 * scale))

        try:
            self.iconbitmap(default=os.path.join(BUNDLE_DIR, "icon.ico"))
        except tk.TclError:
            pass
        seed_bundled_ytdlp()

        self.scale = scale
        self.v = {}
        self.tabs = {}
        self._flag_widgets = []
        self.comp_vars = {}
        self.comp_status = {}
        self.proc = None
        self.busy = False
        self.cancelled = False
        self.q = queue.Queue()

        saved = self._load_settings()
        for key, default in DEFAULTS.items():
            value = saved.get(key, default)
            if isinstance(default, bool):
                self.v[key] = tk.BooleanVar(value=bool(value))
            else:
                self.v[key] = tk.StringVar(value=str(value))

        apply_dark_theme(self)
        self._build_ui()
        dark_title_bar(self)
        for var in self.v.values():
            var.trace_add("write", lambda *_: self._on_change())
        self._apply_mode()
        self._on_change()
        self._refresh_components()
        self._check_tools()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        header = ttk.Frame(self, padding=(16, 12, 16, 0))
        header.pack(fill="x")
        mark = tk.Canvas(header, width=int(30 * self.scale), height=int(30 * self.scale),
                         background=BG, highlightthickness=0)
        self._draw_mark(mark, int(30 * self.scale))
        mark.pack(side="left", padx=(0, 10))
        ttk.Label(header, text="yt-dlp downloader", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="Paste links, pick options, download.", style="Muted.TLabel").pack(
            side="left", padx=(14, 0), pady=(6, 0))
        ttk.Checkbutton(header, text="Advanced mode", variable=self.v["advanced_mode"], style="Switch.TCheckbutton",
                        command=self._apply_mode).pack(side="right", pady=(4, 0))
        self.adv_show = ttk.Button(header, text="Show", style="Accent.Link.TButton",
                                   command=lambda: (self.v["advanced_mode"].set(True), self._apply_mode()))
        self.adv_note = ttk.Label(header, style="Muted.TLabel")

        root = self.root_frame = ttk.Frame(self, padding=(16, 10, 16, 14))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)

        # Links
        urlf = ttk.LabelFrame(root, text="Links", padding=8)
        urlf.grid(row=0, column=0, sticky="nsew")
        urlf.columnconfigure(0, weight=1)
        self.urls = tk.Text(urlf, height=3, wrap="none", undo=True)
        style_text(self.urls, font=MONO)
        self.urls.grid(row=0, column=0, sticky="nsew")
        self.urls.bind("<KeyRelease>", lambda e: self._on_change())
        self.urls.bind("<<Paste>>", lambda e: self.after(10, self._on_change))
        self.url_hint = tk.Label(self.urls, text="Paste one or more links, one per line…",
                                 background=FIELD, foreground=MUTED, font=FONT, cursor="xterm")
        self.url_hint.bind("<Button-1>", lambda e: self.urls.focus_set())
        ub = ttk.Frame(urlf)
        ub.grid(row=0, column=1, sticky="n", padx=(6, 0))
        ttk.Button(ub, text="Paste", command=self._paste).pack(fill="x")
        ttk.Button(ub, text="Clear", command=self._clear_urls).pack(fill="x", pady=(4, 0))

        # Option tabs: fixed-height, scrollable panes so the log keeps its room
        self.tab_height = int(285 * self.scale)
        self._scrollers = []
        nb = self.nb = ttk.Notebook(root)
        nb.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self._tab_general(nb)
        self._tab_format(nb)
        self._tab_subtitles(nb)
        self._tab_post(nb)
        self._tab_playlist(nb)
        self._tab_network(nb)
        self._tab_auth(nb)
        self._tab_advanced(nb)
        self._tab_setup(nb)
        self.bind_all("<MouseWheel>", self._on_wheel)

        # Command preview
        cmdf = self.cmd_frame = ttk.LabelFrame(root, text="Command preview", padding=8)
        cmdf.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        cmdf.columnconfigure(0, weight=1)
        self.cmd_preview = tk.Text(cmdf, height=3, wrap="word")
        style_text(self.cmd_preview, font=MONO, foreground=LOG_INFO)
        self.cmd_preview.grid(row=0, column=0, sticky="ew")
        self.cmd_preview.configure(state="disabled")
        ttk.Button(cmdf, text="Copy", command=self._copy_cmd).grid(row=0, column=1, sticky="n", padx=(6, 0))

        # Action buttons
        bar = ttk.Frame(root)
        bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.btn_download = ttk.Button(bar, text="Download", style="Accent.TButton", command=self._download)
        self.btn_download.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="Stop", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(6, 0))
        self.btn_formats = ttk.Button(bar, text="List formats", command=self._list_formats)
        self.btn_formats.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Open folder", command=self._open_folder).pack(side="left", padx=(6, 0))

        # Status + progress (the bar appears once something runs)
        pf = ttk.Frame(root)
        pf.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        pf.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(pf, textvariable=self.status, anchor="w", style="Muted.TLabel").grid(row=0, column=0, sticky="ew")
        self.progress = ttk.Progressbar(pf, maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.progress.grid_remove()

        # Log: collapsible, expandable to the full window, opens by itself when a job starts
        logf = ttk.Frame(root)
        logf.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(1, weight=1)
        self.log_toggle = ttk.Button(logf, style="Header.Link.TButton", command=self._toggle_log)
        self.log_toggle.grid(row=0, column=0, sticky="w")
        self.log_expand = ttk.Button(logf, text="Expand", style="Link.TButton", command=self._toggle_log_expand)
        self.log_expand.grid(row=0, column=1, sticky="e")
        ttk.Button(logf, text="Clear", style="Link.TButton", command=self._clear_log).grid(row=0, column=2, sticky="e")
        self.log_body = ttk.Frame(logf)
        self.log_body.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        self.log_body.columnconfigure(0, weight=1)
        self.log_body.rowconfigure(0, weight=1)
        self.log = tk.Text(self.log_body, height=5, wrap="word", state="disabled")
        style_text(self.log, font=MONO)
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self.log_body, orient="vertical", command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        self.log.tag_configure("err", foreground=LOG_ERR)
        self.log.tag_configure("info", foreground=LOG_INFO)
        self.log.tag_configure("ok", foreground=LOG_OK)
        self._log_expanded = False
        self._live_line = False
        self._show_log(self.v["log_open"].get())

    @staticmethod
    def _draw_mark(c, size):
        """App mark: rounded red square with a white download arrow (matches icon.ico)."""
        r, s = size * 0.22, size - 1
        c.create_polygon(r, 0, s - r, 0, s, 0, s, r, s, s - r, s, s, s - r, s, r, s, 0, s, 0, s - r, 0, r, 0, 0,
                         smooth=True, fill=PRIMARY, outline=PRIMARY)
        w = max(2, size // 11)
        c.create_line(size / 2, size * 0.2, size / 2, size * 0.62, fill="white", width=w, capstyle="round")
        c.create_line(size * 0.32, size * 0.46, size / 2, size * 0.64, size * 0.68, size * 0.46,
                      fill="white", width=w, capstyle="round", joinstyle="round")
        c.create_line(size * 0.28, size * 0.8, size * 0.72, size * 0.8, fill="white", width=w, capstyle="round")

    def _show_log(self, open_):
        self.v["log_open"].set(open_)
        self.log_toggle.configure(text=("▾  Log" if open_ else "▸  Log"))
        if open_:
            self.log_body.grid()
            self.log_expand.grid()
        else:
            if self._log_expanded:
                self._toggle_log_expand()
            self.log_body.grid_remove()
            self.log_expand.grid_remove()
        # whichever of tabs / log is open takes the spare height
        self.root_frame.rowconfigure(1, weight=0 if open_ else 1)
        self.root_frame.rowconfigure(5, weight=1 if open_ else 0)

    def _toggle_log(self):
        self._show_log(not self.v["log_open"].get())

    def _toggle_log_expand(self):
        self._log_expanded = not self._log_expanded
        if self._log_expanded:
            self.nb.grid_remove()
            self.cmd_frame.grid_remove()
        else:
            self.nb.grid()
            self._apply_mode()  # restores the command preview if advanced
        self.log_expand.configure(text="Shrink" if self._log_expanded else "Expand")
        self.log.see("end")

    def _tab(self, nb, title):
        outer = ttk.Frame(nb, style="Tab.TFrame")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, background=SURFACE, highlightthickness=0, borderwidth=0,
                           height=self.tab_height, yscrollincrement=int(20 * self.scale))
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        f = ttk.Frame(canvas, padding=14, style="Tab.TFrame")
        f.columnconfigure(1, weight=1)
        item = canvas.create_window(0, 0, window=f, anchor="nw")

        def sync(_=None):
            canvas.itemconfigure(item, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, 0, f.winfo_reqheight()))
            if f.winfo_reqheight() > canvas.winfo_height() + 1:
                sb.grid(row=0, column=1, sticky="ns")
            else:
                sb.grid_remove()
                canvas.yview_moveto(0)
        f.bind("<Configure>", sync)
        canvas.bind("<Configure>", sync)
        self._scrollers.append(canvas)
        nb.add(outer, text=title)
        self.tabs[title] = outer
        return f

    def _on_wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and not isinstance(w, tk.Text):  # text boxes scroll themselves
            if w in self._scrollers:
                if w.yview() != (0.0, 1.0):
                    w.yview_scroll(int(-e.delta / 120), "units")
                return
            w = w.master

    def _flagged(self, widget, text):
        """Remember widgets whose text ends in a yt-dlp flag, so simple mode can hide the flag."""
        if FLAG_RE.search(text):
            self._flag_widgets.append((widget, text))
        return widget

    def _entry(self, parent, label, key, row, browse=None, width=None, hint=None, show=None):
        self._flagged(ttk.Label(parent, text=label, style="Tab.TLabel"), label).grid(
            row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        extra = {"show": show} if show else {}
        if hint:  # short entry with its hint alongside, saving a row
            holder = ttk.Frame(parent, style="Tab.TFrame")
            holder.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
            e = ttk.Entry(holder, textvariable=self.v[key], width=width or 30, **extra)
            e.pack(side="left")
            ttk.Label(holder, text=hint, style="Tab.Muted.TLabel").pack(side="left", padx=(10, 0))
            return e
        e = ttk.Entry(parent, textvariable=self.v[key], width=width or 50, **extra)
        e.grid(row=row, column=1, sticky="ew" if not width else "w", pady=3)
        if browse:
            ttk.Button(parent, text="Browse…", command=browse).grid(row=row, column=2, padx=(6, 0))
        return e

    def _combo(self, parent, label, key, values, row, readonly=True, width=22, hint=None):
        self._flagged(ttk.Label(parent, text=label, style="Tab.TLabel"), label).grid(
            row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        holder = parent
        if hint:
            holder = ttk.Frame(parent, style="Tab.TFrame")
            holder.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        c = ttk.Combobox(holder, textvariable=self.v[key], values=values, width=width,
                         state="readonly" if readonly else "normal")
        if hint:
            c.pack(side="left")
            ttk.Label(holder, text=hint, style="Tab.Muted.TLabel").pack(side="left", padx=(10, 0))
        else:
            c.grid(row=row, column=1, sticky="w" if width else "ew", pady=3)
        return c

    def _check(self, parent, text, key, row, col=0, span=3):
        c = ttk.Checkbutton(parent, text=text, variable=self.v[key], style="Tab.TCheckbutton")
        c.grid(row=row, column=col, columnspan=span, sticky="w", pady=2)
        return self._flagged(c, text)

    def _hint(self, parent, text, row):
        ttk.Label(parent, text=text, style="Tab.Muted.TLabel").grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(0, 4))

    def _tab_general(self, nb):
        f = self._tab(nb, "General")
        self._entry(f, "Output folder", "output_dir", 0, browse=self._browse_output)
        self._combo(f, "Filename template", "template", TEMPLATES, 1, readonly=False, width=None)
        self._hint(f, "Uses yt-dlp output template fields, e.g. %(title)s, %(id)s, %(ext)s", 2)
        self._check(f, "Restrict filenames to ASCII, no spaces (--restrict-filenames)", "restrict_filenames", 3)
        self._check(f, "Do not overwrite existing files (--no-overwrites)", "no_overwrites", 4)
        self._check(f, "Use download time as file date, not upload date (--no-mtime)", "no_mtime", 5)

    def _tab_format(self, nb):
        f = self._tab(nb, "Format")
        mf = ttk.Frame(f, style="Tab.TFrame")
        mf.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        for text, val in (("Video + audio", "video"), ("Audio only", "audio"), ("Custom format string", "custom")):
            ttk.Radiobutton(mf, style="Tab.TRadiobutton", text=text, value=val, variable=self.v["mode"]).pack(side="left", padx=(0, 14))

        cols = ttk.Frame(f, style="Tab.TFrame")
        cols.grid(row=1, column=0, columnspan=3, sticky="ew")
        cols.columnconfigure((0, 1), weight=1, uniform="fmt")

        vf = ttk.LabelFrame(cols, text="Video", padding=8, style="Tab.TLabelframe")
        vf.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        vf.columnconfigure(1, weight=1)
        self.video_widgets = [
            self._combo(vf, "Max resolution", "max_height", RESOLUTIONS, 0, width=12),
            self._combo(vf, "Container", "container", CONTAINERS, 1, width=12),
            self._check(vf, "Prefer H.264 / AAC (most compatible)", "prefer_compat", 2),
        ]

        af = self._flagged(ttk.LabelFrame(cols, text="Audio only (-x)", padding=8, style="Tab.TLabelframe"),
                           "Audio only (-x)")
        af.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        af.columnconfigure(1, weight=1)
        self.audio_widgets = [
            self._combo(af, "Audio format", "audio_format", AUDIO_FORMATS, 0, width=14),
            self._combo(af, "Audio quality", "audio_quality", AUDIO_QUALITIES, 1, readonly=False, width=14),
        ]

        cf = self._flagged(ttk.LabelFrame(f, text="Custom (-f)", padding=8, style="Tab.TLabelframe"),
                           "Custom (-f)")
        cf.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        cf.columnconfigure(1, weight=1)
        self.custom_widgets = [self._entry(cf, "Format string", "custom_format", 0)]
        ttk.Label(cf, text="Tip: use \"List formats\" to see format IDs, e.g. 137+140",
                  style="Tab.Muted.TLabel").grid(row=1, column=1, sticky="w")

    def _tab_subtitles(self, nb):
        f = self._tab(nb, "Subtitles")
        self._check(f, "Download subtitles (--write-subs)", "write_subs", 0)
        self._check(f, "Download auto-generated subtitles (--write-auto-subs)", "auto_subs", 1)
        self._check(f, "Embed subtitles into video (--embed-subs)", "embed_subs", 2)
        self._entry(f, "Languages", "sub_langs", 3, width=20,
                    hint="Comma-separated, regex allowed: en.*,de  or  all,-live_chat")
        self._combo(f, "Convert to", "sub_format", SUB_FORMATS, 5)

    def _tab_post(self, nb):
        f = self._tab(nb, "Post-processing")
        f.columnconfigure((0, 1), weight=1, uniform="post")
        self._check(f, "Embed metadata (--embed-metadata)", "embed_metadata", 0, span=1)
        self._check(f, "Embed thumbnail as cover art (--embed-thumbnail)", "embed_thumbnail", 1, span=1)
        self._check(f, "Embed chapters (--embed-chapters)", "embed_chapters", 2, span=1)
        self._check(f, "Save thumbnail image (--write-thumbnail)", "write_thumbnail", 0, col=1, span=1)
        self._check(f, "Save description file (--write-description)", "write_description", 1, col=1, span=1)
        self._check(f, "Save metadata .info.json (--write-info-json)", "write_info_json", 2, col=1, span=1)

        sf = ttk.LabelFrame(f, text="SponsorBlock (YouTube)", padding=8, style="Tab.TLabelframe")
        sf.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        sf.columnconfigure(1, weight=1)
        self._combo(sf, "Mode", "sb_mode", SB_MODES, 0)
        cats = ttk.Frame(sf, style="Tab.TFrame")
        cats.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        for i, c in enumerate(SB_CATEGORIES):
            ttk.Checkbutton(cats, style="Tab.TCheckbutton", text=c, variable=self.v[f"sb_{c}"]).grid(
                row=i // 4, column=i % 4, sticky="w", padx=(0, 16))

    def _tab_playlist(self, nb):
        f = self._tab(nb, "Playlist & clips")
        self._combo(f, "When URL is in a playlist", "playlist_mode", PLAYLIST_MODES, 0)
        self._entry(f, "Items to download (-I)", "playlist_items", 1, width=20,
                    hint="e.g. 1-5,8,10:  or  -3:: (last three). Blank = all.")
        self._check(f, "Skip videos already downloaded, using an archive file (--download-archive)",
                    "use_archive", 2)
        self._entry(f, "Archive file", "archive_file", 3, browse=self._browse_archive)
        self._entry(f, "Download only section", "sections", 4, width=20,
                    hint="e.g. *1:00-2:30 (needs ffmpeg). Blank = full video.")
        self._check(f, "Split into separate files by chapter (--split-chapters)", "split_chapters", 5)

    def _tab_network(self, nb):
        f = self._tab(nb, "Network")
        self._entry(f, "Rate limit (-r)", "rate_limit", 0, width=14, hint="e.g. 2M or 500K. Blank = unlimited.")
        self._entry(f, "Retries (-R)", "retries", 2, width=14)
        self._entry(f, "Concurrent fragments (-N)", "fragments", 3, width=14)
        self._entry(f, "Sleep between downloads, sec", "sleep", 4, width=14)
        self._entry(f, "Proxy (--proxy)", "proxy", 5)
        self._hint(f, "e.g. socks5://127.0.0.1:1080 or http://user:pass@host:port", 6)

    def _tab_auth(self, nb):
        f = self._tab(nb, "Authentication")
        self._combo(f, "Cookies from browser", "cookies_browser", BROWSERS, 0, width=14,
                    hint="Easiest for age-restricted or members-only videos. Close the browser if it fails.")
        self._entry(f, "Cookies file (--cookies)", "cookies_file", 1, browse=self._browse_cookies)
        self._hint(f, "A Netscape-format cookies.txt exported from your browser.", 2)
        self._entry(f, "Username (-u)", "username", 3, width=30)
        self._entry(f, "Password (-p)", "password", 4, width=30, show="\u2022",
                    hint="Not saved between sessions.")
        self._entry(f, "2FA code (-2)", "twofactor", 5, width=12, show="\u2022")
        self._entry(f, "Video password", "video_password", 6, width=30, show="\u2022",
                    hint="For password-protected videos, e.g. Vimeo.")
        self._check(f, "Read logins from a .netrc file (--netrc)", "use_netrc", 7)

    def _tab_advanced(self, nb):
        f = self._tab(nb, "Advanced")
        self._entry(f, "Extra arguments", "extra_args", 0)
        self._hint(f, "Passed to yt-dlp as-is, e.g.  --limit-rate 1M --no-part", 1)
        ttk.Button(f, text="Reset all options\u2026", command=self._reset).grid(
            row=2, column=0, sticky="w", pady=(14, 0))

    def _tab_setup(self, nb):
        f = self._tab(nb, "Setup")
        f.columnconfigure(1, weight=0)
        f.columnconfigure(2, weight=1)
        loc = ttk.Frame(f, style="Tab.TFrame")
        loc.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        ttk.Button(loc, text="Open folder", command=lambda: os.startfile(TOOLS_DIR)).pack(side="right")
        ttk.Label(loc, text=f"Components are stored in  {TOOLS_DIR}", style="Tab.Muted.TLabel",
                  wraplength=int(self.winfo_fpixels("1i") * 6.5)).pack(side="left", fill="x", expand=True)

        for i, name in enumerate(COMPONENTS, start=1):
            self.comp_vars[name] = tk.BooleanVar(value=False)
            ttk.Checkbutton(f, text=name, variable=self.comp_vars[name], style="Tab.TCheckbutton").grid(
                row=i, column=0, sticky="w", pady=2, padx=(0, 14))
            self.comp_status[name] = ttk.Label(f, style="Tab.TLabel", width=12)
            self.comp_status[name].grid(row=i, column=1, sticky="w")
            ttk.Label(f, text=COMPONENTS[name]["desc"], style="Tab.Muted.TLabel").grid(
                row=i, column=2, columnspan=2, sticky="w")

        btns = ttk.Frame(f, style="Tab.TFrame")
        btns.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 10))
        self.btn_get = ttk.Button(btns, text="Install selected", command=self._download_components)
        self.btn_get.pack(side="left")
        ttk.Label(btns, text="Ticked items are fetched (or updated) from their official GitHub releases.",
                  style="Tab.Muted.TLabel").pack(side="left", padx=(12, 0))

        own = ttk.LabelFrame(f, text="Or use your own copies", padding=8, style="Tab.TLabelframe")
        own.grid(row=5, column=0, columnspan=4, sticky="ew")
        own.columnconfigure(1, weight=1)
        self._entry(own, "yt-dlp.exe", "ytdlp_path", 0, browse=self._browse_ytdlp)
        self._entry(own, "ffmpeg folder", "ffmpeg_path", 1, browse=self._browse_ffmpeg)
        ttk.Label(own, text="Leave blank to use the components above (or anything on PATH).",
                  style="Tab.Muted.TLabel").grid(row=2, column=1, sticky="w")

    def _apply_mode(self):
        advanced = self.v["advanced_mode"].get()
        for title, frame in self.tabs.items():
            if advanced or title in SIMPLE_TABS:
                self.nb.add(frame)  # re-adding a hidden tab restores it in place
            else:
                self.nb.hide(frame)
        if advanced and not self._log_expanded:
            self.cmd_frame.grid()
        else:
            self.cmd_frame.grid_remove()
        if advanced:
            self.btn_formats.pack(side="left", padx=(6, 0), after=self.btn_stop)
        else:
            self.btn_formats.pack_forget()
        for widget, text in self._flag_widgets:  # flags are jargon outside advanced mode
            widget.configure(text=text if advanced else FLAG_RE.sub("", text))
        self._update_adv_note()

    def _update_adv_note(self):
        changed = sum(str(self.v[k].get()) != str(DEFAULTS[k]) for k in ADVANCED_KEYS)
        self.adv_note.pack_forget()
        self.adv_show.pack_forget()
        if changed and not self.v["advanced_mode"].get():
            self.adv_show.pack(side="right", padx=(0, 16), pady=(4, 0))
            self.adv_note.configure(text=f"{changed} advanced setting{'s' if changed > 1 else ''} active \u00b7")
            self.adv_note.pack(side="right", pady=(4, 0))

    def _refresh_components(self):
        found = {"yt-dlp": self._resolve_ytdlp(), "ffmpeg": self._ffmpeg_found(), "Deno": find_tool("deno")}
        for name, label in self.comp_status.items():
            ok = bool(found[name])
            label.configure(text="Installed" if ok else "Missing", foreground=LOG_OK if ok else LOG_ERR)
            self.comp_vars[name].set(not ok)  # pre-tick whatever is missing
        return found

    # ------------------------------------------------------------ helpers
    def _get_urls(self):
        return [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]

    def _val(self, key):
        v = self.v[key].get()
        return v.strip() if isinstance(v, str) else v

    def _resolve_ytdlp(self):
        p = self._val("ytdlp_path")
        if p:
            return [p]
        found = find_tool("yt-dlp")
        if found:
            return [found]
        if not FROZEN:  # running from source: fall back to an installed Python module
            importlib.invalidate_caches()
            if importlib.util.find_spec("yt_dlp"):
                return [sys.executable, "-m", "yt_dlp"]
        return None

    def _ffmpeg_found(self):
        own = self._val("ffmpeg_path")
        if own:
            return own if os.path.exists(own) else None
        return find_tool("ffmpeg")

    def _common_args(self):
        """Options that also apply to listing formats (auth, network, tools)."""
        a = []
        if self._val("ffmpeg_path"):
            a += ["--ffmpeg-location", self._val("ffmpeg_path")]
        if self._val("proxy"):
            a += ["--proxy", self._val("proxy")]
        if self._val("cookies_browser") not in ("", "None"):
            a += ["--cookies-from-browser", self._val("cookies_browser")]
        if self._val("cookies_file"):
            a += ["--cookies", self._val("cookies_file")]
        for key, flag in (("username", "-u"), ("password", "-p"), ("twofactor", "-2"),
                          ("video_password", "--video-password")):
            if self._val(key):
                a += [flag, self._val(key)]
        if self._val("use_netrc"):
            a.append("--netrc")
        return a

    def _download_args(self):
        a = ["-P", self._val("output_dir") or ".", "-o", self._val("template") or TEMPLATES[0]]
        if self._val("restrict_filenames"):
            a.append("--restrict-filenames")
        if self._val("no_overwrites"):
            a.append("--no-overwrites")
        if self._val("no_mtime"):
            a.append("--no-mtime")

        mode = self._val("mode")
        if mode == "video":
            h = self._val("max_height")
            if h and h != "Best":
                a += ["-f", f"bv*[height<={h}]+ba/b[height<={h}]"]
            else:
                a += ["-f", "bv*+ba/b"]
            if self._val("prefer_compat"):
                a += ["-S", "vcodec:h264,res,acodec:m4a"]
            if self._val("container") not in ("", "Any"):
                a += ["--merge-output-format", self._val("container")]
        elif mode == "audio":
            a += ["-f", "ba/b", "-x", "--audio-format", self._val("audio_format")]
            q = first_token(self._val("audio_quality"))
            if q:
                a += ["--audio-quality", q]
        elif self._val("custom_format"):
            a += ["-f", self._val("custom_format")]

        if self._val("sections"):
            a += ["--download-sections", self._val("sections")]
        if self._val("split_chapters"):
            a.append("--split-chapters")

        if self._val("write_subs"):
            a.append("--write-subs")
        if self._val("auto_subs"):
            a.append("--write-auto-subs")
        if self._val("embed_subs"):
            a.append("--embed-subs")
        if (self._val("write_subs") or self._val("auto_subs") or self._val("embed_subs")) \
                and self._val("sub_langs"):
            a += ["--sub-langs", self._val("sub_langs")]
        if self._val("sub_format") in SUB_FORMATS[1:]:
            a += ["--convert-subs", self._val("sub_format")]

        for key, flag in (("embed_metadata", "--embed-metadata"),
                          ("embed_thumbnail", "--embed-thumbnail"),
                          ("embed_chapters", "--embed-chapters"),
                          ("write_thumbnail", "--write-thumbnail"),
                          ("write_description", "--write-description"),
                          ("write_info_json", "--write-info-json")):
            if self._val(key):
                a.append(flag)

        sb = self._val("sb_mode")
        cats = [c for c in SB_CATEGORIES if self._val(f"sb_{c}")]
        if sb != "Off" and cats:
            flag = "--sponsorblock-mark" if sb == "Mark as chapters" else "--sponsorblock-remove"
            a += [flag, ",".join(cats)]

        pm = self._val("playlist_mode")
        if pm == "Single video only":
            a.append("--no-playlist")
        elif pm == "Whole playlist":
            a.append("--yes-playlist")
        if self._val("playlist_items"):
            a += ["-I", self._val("playlist_items")]
        if self._val("use_archive") and self._val("archive_file"):
            a += ["--download-archive", self._val("archive_file")]

        if self._val("rate_limit"):
            a += ["-r", self._val("rate_limit")]
        if self._val("retries") and self._val("retries") != "10":
            a += ["-R", self._val("retries")]
        if self._val("fragments") and self._val("fragments") != "1":
            a += ["-N", self._val("fragments")]
        if self._val("sleep"):
            a += ["--sleep-interval", self._val("sleep")]

        a += self._common_args()
        a += self._extra_args()
        return a

    def _extra_args(self):
        raw = self._val("extra_args")
        if not raw:
            return []
        try:
            # posix=False keeps Windows backslashes intact; strip the quotes it leaves behind
            return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
                    for t in shlex.split(raw, posix=False)]
        except ValueError:
            return raw.split()

    def _build_cmd(self):
        exe = self._resolve_ytdlp() or ["yt-dlp"]
        return exe + self._download_args() + ["--"] + self._get_urls()

    def _on_change(self):
        mode = self.v["mode"].get()
        for group, active in ((self.video_widgets, mode == "video"),
                              (self.audio_widgets, mode == "audio"),
                              (self.custom_widgets, mode == "custom")):
            for w in group:
                if isinstance(w, ttk.Combobox):
                    on_state = "normal" if w is self.audio_widgets[1] else "readonly"
                    w.configure(state=on_state if active else "disabled")
                else:
                    w.configure(state="normal" if active else "disabled")
        urls = self._get_urls()
        if urls:
            self.url_hint.place_forget()
        else:
            self.url_hint.place(x=8, y=5)
        if not self.busy:
            self.btn_download.configure(state="normal" if urls else "disabled")
            self.btn_formats.configure(state="normal" if urls else "disabled")
        self._update_adv_note()
        cmd = subprocess.list2cmdline(mask_secrets(self._build_cmd()))
        self.cmd_preview.configure(state="normal")
        self.cmd_preview.delete("1.0", "end")
        self.cmd_preview.insert("1.0", cmd)
        self.cmd_preview.configure(state="disabled")

    def _log(self, text, tag=None, live=False):
        """Append a line. A live line (download progress) is overwritten by the next live line."""
        follow = self.log.yview()[1] >= 0.999  # only auto-scroll if the user is at the bottom
        self.log.configure(state="normal")
        if live and self._live_line:
            self.log.delete("end-1c linestart", "end-1c")
        elif self.log.index("end-1c") != "1.0":
            self.log.insert("end", "\n")
        self.log.insert("end", text, tag)
        self._live_line = live
        self.log.configure(state="disabled")
        if follow:
            self.log.see("end")

    def _check_tools(self):
        found = self._refresh_components()
        if found["yt-dlp"]:
            self._log(f"Using yt-dlp: {' '.join(found['yt-dlp'])}", "info")
        missing = [n for n, ok in found.items() if not ok]
        if missing:
            self._log(f"Missing: {', '.join(missing)}. Open the Setup tab and click "
                      "\"Install selected\". (ffmpeg is needed for merging and conversion, "
                      "Deno for YouTube.)", "err")
            self.nb.select(self.tabs["Setup"])

    # ------------------------------------------------------------ actions
    def _start(self, label, target, *args):
        if self.busy:
            return
        self.busy = True
        self.cancelled = False
        self.progress["value"] = 0
        self.progress.grid()
        self.status.set(label + "\u2026")
        if not self.v["log_open"].get():
            self._show_log(True)
        self._set_running(True)
        threading.Thread(target=target, args=args, daemon=True).start()

    def _run(self, cmd, label):
        if not self.busy:
            self._log("> " + subprocess.list2cmdline(mask_secrets(cmd)), "info")
        self._start(label, self._worker, cmd)

    def _worker(self, cmd):
        # Put our bin folder first so yt-dlp finds the bundled ffmpeg and Deno
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   PATH=os.pathsep.join([TOOLS_DIR, APP_DIR, os.environ.get("PATH", "")]))
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", env=env,
                creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            self.q.put(("err", f"Could not start {cmd[0]}: {e}"))
            self.q.put(("done", -1))
            return
        for line in self.proc.stdout:
            self.q.put(("line", line.rstrip("\r\n")))
        self.q.put(("done", self.proc.wait()))

    def _components_worker(self, names):
        rc = 0
        for name in names:
            if self.cancelled:
                break
            try:
                self._fetch_component(name, COMPONENTS[name])
            except (OSError, ValueError, zipfile.BadZipFile) as e:
                self.q.put(("err", f"{name}: download failed: {e}"))
                rc = 1
        self.q.put(("refresh", None))
        self.q.put(("done", rc))

    def _fetch_component(self, name, info):
        url = info["url"]
        self.q.put(("line", f"Downloading {name} from {url}"))
        tmp = os.path.join(TOOLS_DIR, f"{name}.download")
        try:
            with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as fh:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while chunk := r.read(1 << 16):
                    if self.cancelled:
                        raise OSError("cancelled")
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        self.q.put(("progress", (done * 100 / total,
                                                 f"{name}: {done / 1e6:.1f} of {total / 1e6:.1f} MB")))
            if url.endswith(".zip"):
                with zipfile.ZipFile(tmp) as z:
                    for member in z.namelist():
                        base = os.path.basename(member)
                        if base in info["files"]:
                            dest = os.path.join(TOOLS_DIR, base)
                            with z.open(member) as src, open(dest + ".new", "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            os.replace(dest + ".new", dest)
                            self.q.put(("line", f"Installed {dest}"))
            else:
                dest = os.path.join(TOOLS_DIR, info["files"][0])
                os.replace(tmp, dest)
                self.q.put(("line", f"Installed {dest}"))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self._handle_line(payload)
                elif kind == "progress":
                    value, text = payload
                    self.progress["value"] = value
                    self.status.set(text)
                elif kind == "refresh":
                    self._refresh_components()
                elif kind == "err":
                    self._log(payload, "err")
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle_line(self, line):
        if not line.strip():
            return
        m = PROGRESS_RE.match(line)
        if m:
            self.progress["value"] = float(m.group(1))
            self.status.set(self._item_prefix + line[len("[download]"):].strip())
            self._log(line, live=True)  # one line that updates in place
            return
        m = ITEM_RE.match(line)
        if m:
            self._item_prefix = f"Item {m.group(1)}/{m.group(2)}: "
        tag = "err" if line.startswith("ERROR") else None
        self._log(line, tag)

    _item_prefix = ""

    def _finish(self, rc):
        self.proc = None
        self.busy = False
        self._item_prefix = ""
        self._set_running(False)
        if self.cancelled:
            self.status.set("Stopped")
            self._log("Stopped by user.", "err")
        elif rc == 0:
            self.progress["value"] = 100
            self.status.set("Finished")
            self._log("Done.", "ok")
        else:
            self.status.set(f"Finished with errors (exit code {rc})")
            self._log(f"Process exited with code {rc}.", "err")
        self._on_change()

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        for b in (self.btn_download, self.btn_formats, self.btn_get):
            b.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")

    def _require_ytdlp(self):
        exe = self._resolve_ytdlp()
        if not exe:
            self._dialog("yt-dlp was not found.\n\nOn the Setup tab, tick yt-dlp and click "
                         "\"Install selected\", or browse to your own yt-dlp.exe.")
            self.nb.select(self.tabs["Setup"])
        return exe

    def _download(self):
        urls = self._get_urls()
        if not urls:
            return
        if not self._require_ytdlp():
            return
        out = self._val("output_dir")
        if out:
            os.makedirs(out, exist_ok=True)
        self._save_settings()
        self._run(self._build_cmd(), "Downloading")

    def _list_formats(self):
        urls = self._get_urls()
        if not urls:
            return
        exe = self._require_ytdlp()
        if exe:
            self._run(exe + ["-F", "--no-playlist"] + self._common_args() + ["--", urls[0]],
                      "Listing formats")

    def _download_components(self):
        names = [n for n, var in self.comp_vars.items() if var.get()]
        if not names:
            self._dialog("Tick at least one component to install.")
            return
        self._start("Downloading " + ", ".join(names), self._components_worker, names)

    def _stop(self):
        self.cancelled = True
        if self.proc:
            # /T also kills child processes such as ffmpeg
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)

    def _open_folder(self):
        out = self._val("output_dir")
        if out and os.path.isdir(out):
            os.startfile(out)
        else:
            self._dialog("The output folder doesn't exist yet. It's created by the first download.")

    def _reset(self):
        if not self._dialog("Reset every option to its default?", ("Reset", "Cancel")):
            return
        for key, default in DEFAULTS.items():
            self.v[key].set(default)

    def _paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        existing = self.urls.get("1.0", "end").strip()
        self.urls.insert("end", ("\n" if existing else "") + text.strip())
        self._on_change()

    def _dialog(self, message, buttons=("OK",)):
        """Small dark modal dialog. Returns True if the first button was chosen."""
        top = tk.Toplevel(self, background=SURFACE, padx=22, pady=18)
        top.title(APP_NAME)
        top.transient(self)
        top.resizable(False, False)
        result = {"ok": False}
        ttk.Label(top, text=message, style="Tab.TLabel", wraplength=int(380 * self.scale),
                  justify="left").pack(anchor="w")
        row = ttk.Frame(top, style="Tab.TFrame")
        row.pack(anchor="e", pady=(18, 0))

        def close(ok):
            result["ok"] = ok
            top.destroy()
        for i, text in enumerate(buttons):
            b = ttk.Button(row, text=text, style="Accent.TButton" if i == 0 and len(buttons) > 1 else "TButton",
                           command=lambda ok=(i == 0): close(ok))
            b.pack(side="left", padx=(0 if i == 0 else 8, 0))
            if i == len(buttons) - 1:
                b.focus_set()  # safest choice gets keyboard focus
        top.bind("<Escape>", lambda e: close(False))
        dark_title_bar(top)
        top.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - top.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{x}+{y}")
        top.grab_set()
        self.wait_window(top)
        return result["ok"]

    def _clear_urls(self):
        self.urls.delete("1.0", "end")
        self._on_change()

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._live_line = False

    def _copy_cmd(self):
        self.clipboard_clear()
        self.clipboard_append(self.cmd_preview.get("1.0", "end").strip())
        self.status.set("Command copied to clipboard")

    # ------------------------------------------------------------ browse
    def _browse_output(self):
        d = filedialog.askdirectory(initialdir=self._val("output_dir") or str(Path.home()))
        if d:
            self.v["output_dir"].set(os.path.normpath(d))

    def _browse_file(self, key, title, types, save=False):
        fn = filedialog.asksaveasfilename if save else filedialog.askopenfilename
        p = fn(title=title, filetypes=types)
        if p:
            self.v[key].set(os.path.normpath(p))

    def _browse_archive(self):
        self._browse_file("archive_file", "Archive file", [("Text files", "*.txt"), ("All files", "*.*")],
                          save=True)

    def _browse_cookies(self):
        self._browse_file("cookies_file", "Cookies file", [("Text files", "*.txt"), ("All files", "*.*")])

    def _browse_ytdlp(self):
        self._browse_file("ytdlp_path", "yt-dlp executable", [("yt-dlp", "yt-dlp*.exe"), ("Programs", "*.exe")])
        self._refresh_components()

    def _browse_ffmpeg(self):
        d = filedialog.askdirectory(title="Folder containing ffmpeg.exe")
        if d:
            self.v["ffmpeg_path"].set(os.path.normpath(d))
            self._refresh_components()

    # ------------------------------------------------------------ settings
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        data = {k: v.get() for k, v in self.v.items() if k not in SECRET_KEYS}
        try:
            os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError as e:
            self._log(f"Could not save settings: {e}", "err")

    def _on_close(self):
        if self.busy and not self._dialog("A download is running. Stop it and quit?", ("Stop and quit", "Keep running")):
            return
        self._stop()
        self._save_settings()
        self.destroy()


def main():
    try:  # crisp text on high-DPI screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
