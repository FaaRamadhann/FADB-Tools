#!/usr/bin/env python3
"""
ADB & Fastboot Tools by Faa Ramadhan
Merged full edition + Multi Flash (Batch) window

Requirements (recommended):
    pip install ttkbootstrap
    adb & fastboot in PATH
"""

import os
import sys
import shutil
import subprocess
import threading
import queue
import time
import zipfile
import tempfile
import logging
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox, colorchooser

# Light Blue Sea — default theme
LIGHT_BLUE_SEA_THEME = {
    "bg": "#e2f1f7",
    "fg": "#0d2b3a",
    "button": "#bfe3f0",
    "accent": "#0096c7",
    "terminal_bg": "#f3fafc",
    "terminal_fg": "#005f73",
    "mode": "light"
}

# warna default kalau belum ada config
DEFAULT_THEME = LIGHT_BLUE_SEA_THEME.copy()


def _darken(hex_color, factor=0.82):
    """Gelapkan warna hex (untuk hover/active state)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = max(0, min(255, int(r * factor)))
        g = max(0, min(255, int(g * factor)))
        b = max(0, min(255, int(b * factor)))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color

# === THEME CONFIG ===
import json
from config_manager import load_config, save_config

try:
    import fcc
    FCC_AVAILABLE = True
except Exception:
    fcc = None
    FCC_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
APP_VERSION = "4.0.0"
current_theme = load_config(CONFIG_PATH) or DEFAULT_THEME.copy()

# try ttkbootstrap for nicer dark theme, fallback to ttk
# importing ttkbootstrap.constants can trigger static-analysis/linter errors in some environments
# so only import the main package and avoid wildcard import of constants.
tb = None
try:
    import importlib.util
    if importlib.util.find_spec("ttkbootstrap") is not None:
        import importlib
        tb = importlib.import_module("ttkbootstrap")
        BOOTSTRAP_AVAILABLE = True
    else:
        BOOTSTRAP_AVAILABLE = False
except Exception:
    tb = None
    BOOTSTRAP_AVAILABLE = False

# ---------------- Anti-kedip console (Windows) ----------------
# App jalan tanpa console via pythonw.exe / fadb.vbs. Tanpa ini, setiap
# subprocess.run/Popen ke adb.exe & fastboot.exe (program console) akan
# membuka jendela console baru yang langsung hilang = "kedip-kedip".
# Berlaku global untuk proses ini saja; kode pemanggil tidak perlu diubah.
if os.name == "nt":
    _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _real_run, _real_Popen = subprocess.run, subprocess.Popen

    def _run_no_window(*args, **kwargs):
        kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
        return _real_run(*args, **kwargs)

    class _Popen_no_window(_real_Popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
            super().__init__(*args, **kwargs)

    subprocess.run = _run_no_window
    subprocess.Popen = _Popen_no_window
# ---------------- Config ----------------
# path tools: "auto" (dari PATH) atau path manual via Settings
ADB = "adb"
FASTBOOT = "fastboot"
SCRCPY = "scrcpy"


def resolve_tool(name, manual_path):
    """Pakai manual path kalau file-nya valid, selain itu fallback ke nama (PATH)."""
    if manual_path and os.path.isfile(manual_path):
        return manual_path
    return name


def apply_tool_paths(cfg=None):
    """Terapkan path tools dari config ke global. Dipanggil saat start + save Settings."""
    global ADB, FASTBOOT, SCRCPY
    cfg = cfg if isinstance(cfg, dict) else {}
    ADB = resolve_tool("adb", (cfg.get("adb_path") or "").strip())
    FASTBOOT = resolve_tool("fastboot", (cfg.get("fastboot_path") or "").strip())
    SCRCPY = resolve_tool("scrcpy", (cfg.get("scrcpy_path") or "").strip())


apply_tool_paths(current_theme)
DEVICE_POLL_INTERVAL = 1500  # ms
PRESET_DEBLOAT = [
    "com.miui.analytics",
    "com.xiaomi.account",
]
UNLOCK_COMMANDS = [
    [FASTBOOT, "flashing", "unlock"],
    [FASTBOOT, "oem", "unlock"],
    [FASTBOOT, "oem", "unlock-go"],
]
PARTITION_HINTS = {
    'boot': ['boot.img', 'boot.img.gz'],
    'recovery': ['recovery.img'],
    'system': ['system.img', 'system_new.img', 'system.raw.img', 'system_ext4.img'],
    'vbmeta': ['vbmeta.img'],
    'vendor': ['vendor.img'],
    'odm': ['odm.img'],
    'product': ['product.img'],
    'vendor_boot': ['vendor_boot.img'],
    'dtbo': ['dtbo.img'],
}
# ----------------------------------------

# ===============================
# COMMAND-LINE PARSING (multi-arg + quotes + device-side shell)
# ===============================

def split_cmdline(cmdline):
    """Pecah baris perintah ala shell Windows: grouping quotes tetap jalan,
    tapi backslash path (C:\\...) tidak dimakan seperti shlex POSIX."""
    import shlex
    try:
        parts = shlex.split(cmdline, posix=False)
    except Exception:
        return None
    out = []
    for p in parts:
        if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
            p = p[1:-1]
        out.append(p)
    return out


def build_adb_command(cmdline):
    """Bangun argv adb dari ketikan user (boleh pakai/tanpa prefix 'adb').

    Remote command sesudah 'shell' (termasuk via -s/-d/-e) dilewatkan UTUH
    sebagai SATU argumen agar quotes, pipe, dan su -c "..." diproses
    device-side dengan benar. Kembalikan None kalau sintaks quotes rusak.
    """
    import re
    line = (cmdline or "").strip()
    m = re.match(r"(?i)^adb(?:\s+(.*))?$", line, re.DOTALL)
    if m:
        if m.group(1) is None:
            return [ADB]
        rest = m.group(1).strip()
    else:
        rest = line
    m2 = re.match(r"(?i)^(-s\s+\S+|-d|-e)\s+shell\s+(.*)$", rest, re.DOTALL)
    if m2:
        sel = split_cmdline(m2.group(1))
        if sel is None:
            return None
        return [ADB] + sel + ["shell", m2.group(2)]
    m3 = re.match(r"(?i)^shell\s+(.*)$", rest, re.DOTALL)
    if m3:
        return [ADB, "shell", m3.group(1)]
    if not rest:
        return [ADB]
    parts = split_cmdline(rest)
    if parts is None:
        return None
    return [ADB] + parts


def build_fastboot_command(cmdline):
    """Bangun argv fastboot dari ketikan user (boleh pakai/tanpa prefix).
    Kembalikan None kalau sintaks quotes rusak."""
    import re
    line = (cmdline or "").strip()
    m = re.match(r"(?i)^fastboot(?:\s+(.*))?$", line, re.DOTALL)
    if m:
        if m.group(1) is None:
            return [FASTBOOT]
        rest = m.group(1).strip()
    else:
        rest = line
    if not rest:
        return [FASTBOOT]
    parts = split_cmdline(rest)
    if parts is None:
        return None
    return [FASTBOOT] + parts

# ===============================
# SLOT AWARE + PREFLASH SAFETY
# ===============================

def fastboot_getvar(var):
    try:
        p = subprocess.run(
            [FASTBOOT, "getvar", var],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=4
        )
        return (p.stdout + p.stderr).strip()
    except Exception as e:
        return f"ERROR:{e}"

def is_ab_device():
    return "yes" in fastboot_getvar("has-slot:boot").lower()

def get_current_slot():
    out = fastboot_getvar("current-slot").lower()
    if ": a" in out: return "a"
    if ": b" in out: return "b"
    return None

def get_inactive_slot():
    cur = get_current_slot()
    if cur == "a": return "b"
    if cur == "b": return "a"
    return None

def check_battery(min_percent=30):
    try:
        res = subprocess.run(
            [ADB, "shell", "dumpsys", "battery"],
            stdout=subprocess.PIPE,
            text=True,
            timeout=4
        )
        for ln in res.stdout.splitlines():
            if "level" in ln:
                lvl = int(ln.split(":")[-1].strip())
                return lvl >= min_percent, lvl
    except Exception:
        pass
    return False, -1

def check_partition_exists(part):
    out = fastboot_getvar(f"partition-size:{part}")
    return "partition-size" in out.lower() and "0x0" not in out.lower()

def check_image_size_fit(part, img_path):
    try:
        img_size = os.path.getsize(img_path)
        out = fastboot_getvar(f"partition-size:{part}")
        part_size = int(out.split(":")[-1].strip(), 16)
        return img_size <= part_size
    except Exception:
        return False

def preflash_safety_guard(flash_plan, min_battery=30):
    ok, lvl = check_battery(min_battery)
    if not ok:
        return False, f"Battery too low ({lvl}%)"

    ab = is_ab_device()
    cur = get_current_slot() if ab else None

    for part, img, slot in flash_plan:
        real = f"{part}_{slot}" if slot else part

        if not os.path.exists(img):
            return False, f"Image not found: {img}"

        if not check_partition_exists(real):
            return False, f"Partition missing: {real}"

        if not check_image_size_fit(real, img):
            return False, f"Image too large: {real}"

        if ab and slot == cur:
            return False, f"Blocked: active slot {cur}"

    return True, "OK"
# ----------------------------------------

# ===============================
# FLASH SIMULATION (DRY-RUN+)
# ===============================

def calculate_flash_risk(part):
    risk = 1
    if part in ("boot", "vendor_boot", "dtbo"):
        risk += 2
    if part in ("vbmeta",):
        risk += 3
    if part in ("super", "system", "vendor", "product"):
        risk += 4
    return risk

def estimate_flash_time(img_path):
    try:
        size_mb = os.path.getsize(img_path) / (1024 * 1024)
        return round(size_mb / 40, 1)  # ~40MB/s fastboot average
    except Exception:
        return 0.0

def simulate_flash(plan, ab, inactive):
    """
    plan: list[(part, img, slot)]
    """
    total_risk = 0
    total_time = 0.0

    output_q.put("\n=== FLASH SIMULATION (DRY-RUN+) ===\n")

    for part, img, slot in plan:
        target = f"{part}_{slot}" if slot else part
        risk = calculate_flash_risk(part)
        t = estimate_flash_time(img)

        total_risk += risk
        total_time += t

        output_q.put(f"[SIM] fastboot flash {target} {img}\n")
        output_q.put(f"      ↳ risk: {risk} | est: {t}s\n")

    level = "LOW"
    if total_risk >= 10:
        level = "MEDIUM"
    if total_risk >= 18:
        level = "HIGH"

    output_q.put(f"\n[SIM SUMMARY] risk={level} ({total_risk}) | est total={round(total_time,1)}s\n")
    output_q.put("=== END SIMULATION ===\n\n")
# ----------------------------------------

# ===============================
# BOOT IMAGE TOOLKIT
# ===============================

def find_magiskboot():
    for cmd in ("magiskboot", "magiskboot.exe"):
        if shutil.which(cmd):
            return cmd
    return None

def unpack_boot(img_path, workdir):
    mb = find_magiskboot()
    if not mb:
        return False, "magiskboot not found"

    shutil.copy(img_path, os.path.join(workdir, "boot.img"))
    cmd = [mb, "unpack", "boot.img"]
    p = subprocess.run(cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode == 0, p.stdout

def detect_magisk(workdir):
    # Heuristic: ramdisk contains magisk files
    for root, dirs, files in os.walk(workdir):
        for f in files:
            if "magisk" in f.lower():
                return True
    return False

def repack_boot(workdir):
    mb = find_magiskboot()
    if not mb:
        return False, "magiskboot not found"

    cmd = [mb, "repack", "boot.img"]
    p = subprocess.run(cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    new_img = os.path.join(workdir, "new-boot.img")
    if p.returncode == 0 and os.path.exists(new_img):
        return True, new_img
    return False, p.stdout
# ----------------------------------------

# ===============================
# ADB PERMISSION MANAGER
# ===============================

def adb_shell(cmd, timeout=6):
    try:
        p = subprocess.run(
            [ADB, "shell"] + cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return -1, str(e)

def list_packages():
    code, out = adb_shell(["pm", "list", "packages"])
    if code != 0:
        return []
    return [ln.split(":")[-1].strip() for ln in out.splitlines() if ln.startswith("package:")]

def list_permissions(pkg):
    code, out = adb_shell(["dumpsys", "package", pkg])
    if code != 0:
        return []

    perms = []
    capture = False
    for ln in out.splitlines():
        l = ln.strip()
        if l.startswith("grantedPermissions:"):
            capture = True
            continue
        if capture:
            if not l or l.endswith(":"):
                break
            perms.append(l)
    return perms

def is_restricted_permission(perm):
    # common restricted perms
    restricted = (
        "android.permission.WRITE_SECURE_SETTINGS",
        "android.permission.DUMP",
        "android.permission.READ_LOGS",
    )
    return perm in restricted

def grant_permission(pkg, perm):
    if is_restricted_permission(perm):
        return False, "Restricted permission"

    code, out = adb_shell(["pm", "grant", pkg, perm])
    if code != 0:
        return False, out
    return True, "Granted"

def revoke_permission(pkg, perm):
    code, out = adb_shell(["pm", "revoke", pkg, perm])
    if code != 0:
        return False, out
    return True, "Revoked"
# ----------------------------------------

# ===============================
# ADB PERMISSION MANAGER
# ===============================

def permission_manager_demo():
    pkgs = list_packages()
    output_q.put(f"[PERM] Found {len(pkgs)} packages\n")

    if not pkgs:
        output_q.put("[PERM] No packages or adb error\n")
        return

    pkg = pkgs[0]  # demo target
    output_q.put(f"[PERM] Inspecting: {pkg}\n")

    perms = list_permissions(pkg)
    for p in perms[:10]:
        output_q.put(f"  - {p}\n")
# ----------------------------------------

# Internal
output_q = queue.Queue()
proc_list_lock = threading.Lock()
current_procs = []  # list of subprocess.Popen objects being managed

def timestamp():
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")

# ---------- Utilities ----------
def append_term(widget, txt):
    widget.configure(state="normal")
    widget.insert(tk.END, txt)
    widget.see(tk.END)
    widget.configure(state="disabled")

def is_bin_available(binname):
    if binname and os.path.isabs(binname):
        return os.path.isfile(binname)
    return shutil.which(binname) is not None

def save_text_to_file(content, initial="log.txt"):
    fn = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=initial, filetypes=[("Text files","*.txt")])
    if fn:
        with open(fn, "w", encoding="utf-8") as f:
            f.write(content)
        messagebox.showinfo("Saved", f"Saved to {fn}")

# ---------- Subprocess runner (thread-safe) ----------
def run_cmd_stream(cmd, term_widget, env=None, dry_run=False):
    """
    Run subprocess and stream output into output_q.
    cmd: list or string (list preferred)
    This function blocks until process finishes and streams output lines to output_q.
    """
    if isinstance(cmd, list):
        display = " ".join(cmd)
    else:
        display = cmd

    output_q.put(f"\n$ {display}\n")
    if dry_run:
        output_q.put("[DRY-RUN] Command not executed.\n")
        return 0

    try:
        if isinstance(cmd, list):
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        else:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, shell=True)
    except FileNotFoundError:
        output_q.put(f"Error: binary not found: {cmd[0] if isinstance(cmd, list) else cmd.split()[0]}\n")
        return -1
    except Exception as e:
        output_q.put(f"Error starting command {display}: {e}\n")
        return -1

    with proc_list_lock:
        current_procs.append(proc)

    try:
        for line in proc.stdout:
            output_q.put(line)
    except Exception as e:
        output_q.put(f"Error reading subprocess output: {e}\n")
    finally:
        try:
            proc.wait()
            output_q.put(f"\n[Process exited with code {proc.returncode}]\n")
        except Exception as e:
            output_q.put(f"\n[Process wait error: {e}]\n")
        with proc_list_lock:
            try:
                current_procs.remove(proc)
            except ValueError:
                pass
    return getattr(proc, "returncode", -1)

def start_cmd(cmd, term_widget, dry_run=False):
    t = threading.Thread(target=run_cmd_stream, args=(cmd, term_widget, None, dry_run), daemon=True)
    t.start()
    return t

def stop_all_current():
    stopped_any = False
    with proc_list_lock:
        procs_copy = list(current_procs)
    for p in procs_copy:
        try:
            if p and p.poll() is None:
                p.terminate()
                time.sleep(0.2)
                if p.poll() is None:
                    p.kill()
                stopped_any = True
        except Exception:
            pass
    return stopped_any

# ---------- Device detection ----------
def adb_devices_list():
    try:
        res = subprocess.run([ADB, "devices", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        return res.stdout
    except Exception:
        return ""

def fastboot_devices_list():
    try:
        res = subprocess.run([FASTBOOT, "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        return res.stdout
    except Exception:
        return ""

def detect_device_state():
    adb_out = adb_devices_list()
    if adb_out:
        lines = [ln for ln in adb_out.splitlines() if ln.strip()]
        for ln in lines:
            if not ln.lower().startswith("list of devices"):
                if "device" in ln.split():
                    return ("adb", ln.strip())
    fb_out = fastboot_devices_list()
    if fb_out and fb_out.strip():
        return ("fastboot", fb_out.strip())
    return ("none", "")

def fastboot_getvar_all():
    try:
        res = subprocess.run([FASTBOOT, "getvar", "all"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=6)
        combined = (res.stdout or "") + "\n" + (res.stderr or "")
        return combined
    except Exception as e:
        return f"Error getting getvar all: {e}"

# ---------- Unlock / Lock workers ----------
def unlock_worker(term_widget, dry_run=False, force=False, logfile=None):
    output_q.put("\n=== START UNIVERSAL UNLOCK ATTEMPT ===\n")
    if not is_bin_available(FASTBOOT):
        output_q.put("fastboot not found in PATH. Aborting.\n")
        return

    fb_list = fastboot_devices_list()
    if not fb_list.strip():
        output_q.put("No fastboot device detected. Put device in bootloader/fastboot mode.\n")
        return

    getvar = fastboot_getvar_all()
    output_q.put("[fastboot getvar all]\n")
    output_q.put(getvar + "\n")

    vendor_hint = ""
    lower = getvar.lower()
    if "xiaomi" in lower or "redmi" in lower:
        vendor_hint = "Xiaomi detected — may require Mi Unlock (account/token)."
    elif "samsung" in lower:
        vendor_hint = "Samsung detected — modern Samsung often locked; Odin/vendor tools likely needed."
    elif "huawei" in lower:
        vendor_hint = "Huawei detected — often requires official unlock code."
    elif "oneplus" in lower or "oppo" in lower or "realme" in lower:
        vendor_hint = "OnePlus/OPPO/Realme family — often support fastboot unlock but some models need token."
    if vendor_hint:
        output_q.put(f"[Vendor hint] {vendor_hint}\n")

    if not dry_run and not force:
        ans = simpledialog.askstring("Confirm Unlock",
                                     "Unlocking will likely ERASE DATA and void warranty.\n"
                                     "Type EXACTLY 'unlock' to proceed, or Cancel to abort.")
        if ans is None or ans.strip().lower() != "unlock":
            output_q.put("User cancelled unlock (confirmation not given).\n")
            output_q.put("=== END UNIVERSAL UNLOCK ATTEMPT ===\n")
            return

    lf = None
    if logfile:
        try:
            lf = open(logfile, "a", encoding="utf-8")
            lf.write("\n\n=== LOG START: " + timestamp() + " ===\n")
            lf.write("[fastboot getvar all]\n")
            lf.write(getvar + "\n")
            lf.flush()
        except Exception:
            lf = None

    for cmd in UNLOCK_COMMANDS:
        output_q.put(f"Trying: {' '.join(cmd)}\n")
        if lf:
            lf.write(f"\n$ {' '.join(cmd)}\n"); lf.flush()
        if dry_run:
            output_q.put("[DRY-RUN] skip execution\n")
            if lf: lf.write("[DRY-RUN] skip execution\n")
            continue

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        except FileNotFoundError:
            output_q.put(f"Error: binary not found: {cmd[0]}\n")
            if lf: lf.write(f"Error: binary not found: {cmd[0]}\n")
            continue
        except Exception as e:
            output_q.put(f"Error starting {cmd}: {e}\n")
            if lf: lf.write(f"Error starting {cmd}: {e}\n")
            continue

        with proc_list_lock:
            current_procs.append(proc)

        try:
            for line in proc.stdout:
                output_q.put(line)
                if lf: lf.write(line); lf.flush()
        except Exception as e:
            output_q.put(f"Error reading output: {e}\n")
            if lf: lf.write(f"Error reading output: {e}\n")
        finally:
            try:
                proc.wait()
                output_q.put(f"[Exited with {proc.returncode}]\n")
                if lf: lf.write(f"[Exited with {proc.returncode}]\n")
            except Exception as e:
                output_q.put(f"[Wait error: {e}]\n")
                if lf: lf.write(f"[Wait error: {e}]\n")
            with proc_list_lock:
                try:
                    current_procs.remove(proc)
                except ValueError:
                    pass

        if proc.returncode == 0:
            output_q.put("Command returned code 0 — likely success.\n")
            if lf: lf.write("Command return 0 — likely success.\n")
            break
        else:
            output_q.put("No success indication — trying next method.\n")
            if lf: lf.write("No success indication — trying next.\n")

    output_q.put("=== END UNIVERSAL UNLOCK ATTEMPT ===\n")
    if lf:
        lf.write("=== LOG END ===\n"); lf.close()

def lock_worker(term_widget, dry_run=False, force=False):
    output_q.put("\n=== START LOCK BOOTLOADER ATTEMPT ===\n")
    if not is_bin_available(FASTBOOT):
        output_q.put("fastboot not found in PATH. Aborting.\n")
        return
    fb_list = fastboot_devices_list()
    if not fb_list.strip():
        output_q.put("No fastboot device detected.\n")
        return

    preferred = [ [FASTBOOT, "flashing", "lock"], [FASTBOOT, "oem", "lock"] ]
    for cmd in preferred:
        output_q.put(f"Trying: {' '.join(cmd)}\n")
        if dry_run:
            output_q.put("[DRY-RUN] skip execution\n")
            continue
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        except Exception as e:
            output_q.put(f"Error starting {cmd}: {e}\n")
            continue
        with proc_list_lock:
            current_procs.append(proc)
        try:
            for line in proc.stdout:
                output_q.put(line)
        except Exception as e:
            output_q.put(f"Error reading output: {e}\n")
        finally:
            try:
                proc.wait()
                output_q.put(f"[Exited with {proc.returncode}]\n")
            except Exception as e:
                output_q.put(f"[Wait error: {e}\n")
            with proc_list_lock:
                try:
                    current_procs.remove(proc)
                except ValueError:
                    pass
        if proc.returncode == 0:
            output_q.put("Bootloader lock command returned 0 — likely locked.\n")
            break
    output_q.put("=== END LOCK BOOTLOADER ATTEMPT ===\n")

# ---------- Auto-flash ZIP helpers ----------
def map_images_to_partitions(img_files):
    """
    Smart partition mapper:
    - exact filename match
    - PARTITION_HINTS aware
    - dynamic partition safe
    return: list[(partition, img_path)]
    """
    mapped = []
    used = set()

    # 1️⃣ Exact filename → partition
    for img in img_files:
        name = os.path.basename(img).lower()
        base = name.replace(".img", "")
        if base in PARTITION_HINTS:
            mapped.append((base, img))
            used.add(img)

    # 2️⃣ Hint-based mapping
    for part, hints in PARTITION_HINTS.items():
        if any(p == part for p, _ in mapped):
            continue
        for img in img_files:
            if img in used:
                continue
            name = os.path.basename(img).lower()
            for h in hints:
                if h in name:
                    mapped.append((part, img))
                    used.add(img)
                    break

    # 3️⃣ super.img (dynamic partition container)
    for img in img_files:
        if img in used:
            continue
        if os.path.basename(img).lower() == "super.img":
            mapped.append(("super", img))
            used.add(img)

    # 4️⃣ fallback (last resort)
    for img in img_files:
        if img in used:
            continue
        part = os.path.basename(img).split(".img")[0]
        mapped.append((part, img))

    return mapped

def auto_flash_zip_worker(term_widget, zipfile_path, dry_run=False, force_active=False):
    output_q.put(f"\n=== START AUTO FLASH ZIP: {zipfile_path} ===\n")
    if not is_bin_available(FASTBOOT):
        output_q.put("fastboot not found in PATH. Aborting.\n")
        return
    if not os.path.isfile(zipfile_path):
        output_q.put("ZIP file not found.\n")
        return

    tmpdir = tempfile.mkdtemp(prefix="faa_flash_")
    try:
        try:
            with zipfile.ZipFile(zipfile_path, 'r') as z:
                z.extractall(tmpdir)
        except Exception as e:
            output_q.put(f"Failed to extract zip: {e}\n")
            return

        img_files = []
        for rootp, dirs, files in os.walk(tmpdir):
            for f in files:
                if f.lower().endswith('.img'):
                    img_files.append(os.path.join(rootp, f))
        if not img_files:
            output_q.put("No .img files found inside ZIP. Is this the correct firmware package?\n")
            return

        mapped = map_images_to_partitions(img_files)
        if not mapped:
            output_q.put("Could not map images to partitions automatically; will use filename heuristics.\n")
            for full in img_files:
                part = os.path.basename(full).split('.img')[0]
                mapped.append((part, full))

        output_q.put("Planned flash operations:\n")
        for p, f in mapped:
            output_q.put(f" - {p} -> {f}\n")

        if not dry_run:
            ok = messagebox.askyesno("Confirm Auto Flash", f"Will flash {len(mapped)} image(s) to device. Continue?")
            if not ok:
                output_q.put("User cancelled auto-flash.\n")
                return

# -------- SLOT AWARE + SAFETY FLASH --------
        ab = is_ab_device()
        inactive = get_inactive_slot() if ab else None

        if dry_run:
            sim_plan = []
            for p, f in mapped:
                if ab:
                    sim_plan.append((p, f, inactive))
                else:
                    sim_plan.append((p, f, None))

            simulate_flash(sim_plan, ab, inactive)

        for p, f in mapped:
            # ---- BOOT IMAGE TOOLKIT HOOK ----
            if p == "boot":
                tmp_boot = tempfile.mkdtemp(prefix="faa_boot_")
                ok, msg = unpack_boot(f, tmp_boot)
                if not ok:
                    output_q.put(f"[BOOT TOOLKIT] unpack failed: {msg}\n")
                    shutil.rmtree(tmp_boot, ignore_errors=True)
                    return

                is_magisk = detect_magisk(tmp_boot)
                output_q.put(f"[BOOT TOOLKIT] Magisk detected: {is_magisk}\n")

                ok, new_img = repack_boot(tmp_boot)
                if not ok:
                    output_q.put(f"[BOOT TOOLKIT] repack failed: {new_img}\n")
                    shutil.rmtree(tmp_boot, ignore_errors=True)
                    return

                f = new_img  # override image to flash
                shutil.rmtree(tmp_boot, ignore_errors=True)
            # --------------------------------

            # Build flash plan (1 item per loop)
            if ab:
                flash_plan = [(p, f, inactive)]
            else:
                flash_plan = [(p, f, None)]

            ok, msg = preflash_safety_guard(flash_plan)
            if not ok:
                output_q.put(f"[SAFETY BLOCK] {msg}\n")
                return  # HARD STOP (anti-brick)

            part, img, slot = flash_plan[0]
            target = f"{part}_{slot}" if slot else part
            cmd = [FASTBOOT, "flash", target, img]

            output_q.put(f"[SAFE FLASH] {' '.join(cmd)}\n")

            if dry_run:
                output_q.put("[DRY-RUN] skip execution\n")
                continue

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True
                )
            except Exception as e:
                output_q.put(f"Error starting flash {target}: {e}\n")
                continue

            with proc_list_lock:
                current_procs.append(proc)

            try:
                for line in proc.stdout:
                    output_q.put(line)
            except Exception as e:
                output_q.put(f"Error reading output: {e}\n")
            finally:
                try:
                    proc.wait()
                    output_q.put(f"[Exited with {proc.returncode}]\n")
                except Exception as e:
                    output_q.put(f"[Wait error: {e}\n")
                with proc_list_lock:
                    try:
                        current_procs.remove(proc)
                    except ValueError:
                        pass
# ------------------------------------------

        if ab and inactive and not dry_run:
            output_q.put(f"[INFO] Switching active slot to {inactive}\n")
            run_cmd_stream([FASTBOOT, "set_active", inactive], term_widget)

        output_q.put("=== END AUTO FLASH ZIP ===\n")
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

# ---------- Auto-detect suggestion ----------
def detect_unlock_suggestion():
    try:
        res = subprocess.run([ADB, 'shell', 'getprop', 'ro.product.manufacturer'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=4)
        manuf = (res.stdout or '').strip().lower()
    except Exception:
        manuf = ''
    fb_all = fastboot_getvar_all().lower()

    if 'xiaomi' in manuf or 'xiaomi' in fb_all or 'redmi' in fb_all:
        return 'Xiaomi detected: may need Mi Unlock (official) or token; fastboot may not be enough.'
    if 'samsung' in manuf or 'samsung' in fb_all:
        return 'Samsung detected: many models locked; vendor tools (Odin) often required.'
    if 'huawei' in manuf or 'huawei' in fb_all:
        return 'Huawei detected: often requires official unlock code.'
    if 'oneplus' in manuf or 'oppo' in manuf or 'realme' in manuf or 'oneplus' in fb_all:
        return 'OnePlus/OPPO/Realme: usually supports fastboot oem unlock, but some require token.'
    if 'pixel' in manuf or 'google' in manuf or 'android' in fb_all:
        return 'Google/Pixel or generic Android: fastboot flashing unlock usually works.'
    return 'No specific vendor detected. Run \"fastboot getvar all\" and use dry-run first.'

# ---------- Extra features from old tool: Logcat viewer, Device Info ----------
def start_logcat_window(parent, dry_run=False):
    win = tk.Toplevel(parent)
    win.title("Logcat Viewer")
    win.geometry("900x500")
    txt = tk.Text(win, state="disabled")
    txt.pack(fill=tk.BOTH, expand=True)
    btnf = ttk.Frame(win)
    btnf.pack(fill=tk.X)
    stop_flag = {"stop": False}

    def append(txtwidget, s):
        txtwidget.configure(state="normal")
        txtwidget.insert("end", s); txtwidget.see("end"); txtwidget.configure(state="disabled")

    def run_logcat():
        if dry_run:
            append(txt, "[DRY-RUN] logcat not started\n")
            return
        try:
            proc = subprocess.Popen([ADB, "logcat"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        except Exception as e:
            append(txt, f"Failed start logcat: {e}\n"); return
        with proc_list_lock:
            current_procs.append(proc)
        try:
            for line in proc.stdout:
                if stop_flag["stop"]:
                    break
                append(txt, line)
        except Exception as e:
            append(txt, f"Error reading logcat: {e}\n")
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            with proc_list_lock:
                try:
                    current_procs.remove(proc)
                except ValueError:
                    pass

    def stop():
        stop_flag["stop"] = True

    ttk.Button(btnf, text="Stop", command=stop).pack(side=tk.LEFT, padx=4)
    threading.Thread(target=run_logcat, daemon=True).start()

def show_device_info(parent):
    info = []
    try:
        res = subprocess.run([ADB, "shell", "getprop"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=4)
        info.append("[adb getprop]\n")
        info.append(res.stdout + "\n")
    except Exception:
        info.append("[adb getprop] failed or no adb device\n")
    try:
        res2 = subprocess.run([FASTBOOT, "getvar", "all"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=4)
        info.append("[fastboot getvar all]\n")
        info.append((res2.stdout or "") + "\n" + (res2.stderr or "") + "\n")
    except Exception:
        info.append("[fastboot getvar all] failed or no fastboot device\n")
    win = tk.Toplevel(parent)
    win.title("Device Info")
    win.geometry("900x600")
    txt = tk.Text(win)
    txt.pack(fill=tk.BOTH, expand=True)
    txt.insert("end", "".join(info))
    txt.configure(state="disabled")

# ---------- MultiFlash Window Class ----------
class MultiFlashWindow:
    def __init__(self, parent, main_term_widget, dryrun_var):
        self.parent = parent
        self.term = main_term_widget
        self.dryrun_var = dryrun_var
        self.win = tk.Toplevel(parent)
        self.win.title("Multi Flash (Batch Flash Tool)")
        self.win.geometry("820x480")
        # allow user to interact with main window (non-modal)
        self.rows = []  # list of dicts: {'frame','chk','part_entry','file_entry','browse_btn','prog'}
        self._build_ui()

    def _build_ui(self):
        ttk.Label(self.win, text="Tambahkan beberapa partition & file untuk diflash berurutan").pack(anchor=tk.W, padx=8, pady=(8,0))

        container = ttk.Frame(self.win)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Scrollable frame for rows
        canvas = tk.Canvas(container)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=vsb.set)
        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0,0), window=inner, anchor='nw')
        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_configure)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # header row
        hdr = ttk.Frame(inner)
        hdr.pack(fill=tk.X, pady=2)
        ttk.Label(hdr, text="Sel", width=4).pack(side=tk.LEFT, padx=4)
        ttk.Label(hdr, text="Partition", width=18).pack(side=tk.LEFT, padx=4)
        ttk.Label(hdr, text="File Path", anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Label(hdr, text="Progress", width=16).pack(side=tk.LEFT, padx=8)

        self.rows_container = inner

        # initial two rows
        self.add_row()
        self.add_row()

        # bottom controls
        btnf = ttk.Frame(self.win)
        btnf.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(btnf, text="+ Add Row", command=self.add_row).pack(side=tk.LEFT)
        ttk.Button(btnf, text="Remove Selected", command=self.remove_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(btnf, text="Load From File...", command=self.load_from_file).pack(side=tk.LEFT, padx=6)
        ttk.Button(btnf, text="Save To File...", command=self.save_to_file).pack(side=tk.LEFT, padx=6)

        actionf = ttk.Frame(self.win)
        actionf.pack(fill=tk.X, padx=8, pady=(0,8))
        ttk.Button(actionf, text="Start Flash", command=self.start_flash_confirm).pack(side=tk.RIGHT, padx=6)
        ttk.Button(actionf, text="Close", command=self.win.destroy).pack(side=tk.RIGHT)

    def add_row(self, partition_value="", file_value=""):
        rowf = ttk.Frame(self.rows_container)
        rowf.pack(fill=tk.X, pady=3, padx=2)

        var_chk = tk.BooleanVar(value=True)
        chk = ttk.Checkbutton(rowf, variable=var_chk)
        chk.var = var_chk
        chk.pack(side=tk.LEFT, padx=4)

        part_entry = ttk.Entry(rowf, width=20)
        part_entry.insert(0, partition_value)
        part_entry.pack(side=tk.LEFT, padx=4)

        file_entry = ttk.Entry(rowf)
        file_entry.insert(0, file_value)
        file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        def browse_cb():
            fn = filedialog.askopenfilename(title="Select file to flash", filetypes=[("All files","*.*")])
            if fn:
                file_entry.delete(0, tk.END)
                file_entry.insert(0, fn)

        browse_btn = ttk.Button(rowf, text="Browse", command=browse_cb)
        browse_btn.pack(side=tk.LEFT, padx=4)

        prog = ttk.Progressbar(rowf, mode="determinate", length=140)
        prog.pack(side=tk.LEFT, padx=8)

        self.rows.append({
            'frame': rowf,
            'chk': chk,
            'part_entry': part_entry,
            'file_entry': file_entry,
            'browse_btn': browse_btn,
            'prog': prog
        })
        # ensure scrollregion updated
        self.win.update_idletasks()

    def remove_selected(self):
        to_remove = [r for r in self.rows if r['chk'].var.get() is True]
        if not to_remove:
            messagebox.showinfo("Info", "Tidak ada baris yang dipilih untuk dihapus.")
            return
        if not messagebox.askyesno("Confirm", f"Hapus {len(to_remove)} baris terpilih?"):
            return
        for r in to_remove:
            try:
                r['frame'].destroy()
            except Exception:
                pass
            try:
                self.rows.remove(r)
            except ValueError:
                pass

    def load_from_file(self):
        fn = filedialog.askopenfilename(title="Load multi-flash list", filetypes=[("Text files","*.txt;*.csv"),("All files","*.*")])
        if not fn:
            return
        try:
            with open(fn, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]
            # expected format: partition|filepath  OR partition,filepath OR partition filepath
            for ln in lines:
                if "|" in ln:
                    part, path = ln.split("|",1)
                elif "," in ln:
                    part, path = ln.split(",",1)
                else:
                    parts = ln.split()
                    if len(parts) >= 2:
                        part = parts[0]; path = " ".join(parts[1:])
                    else:
                        continue
                self.add_row(part.strip(), path.strip())
        except Exception as e:
            messagebox.showerror("Error", f"Gagal load file: {e}")

    def save_to_file(self):
        fn = filedialog.asksaveasfilename(defaultextension=".txt", title="Save multi-flash list as", filetypes=[("Text files","*.txt")])
        if not fn:
            return
        try:
            with open(fn, "w", encoding="utf-8") as f:
                for r in self.rows:
                    part = r['part_entry'].get().strip()
                    path = r['file_entry'].get().strip()
                    if part or path:
                        f.write(f"{part}|{path}\n")
            messagebox.showinfo("Saved", f"Saved to {fn}")
        except Exception as e:
            messagebox.showerror("Error", f"Gagal menyimpan: {e}")

    def start_flash_confirm(self):
        # collect selected rows
        selected = [r for r in self.rows if r['chk'].var.get() is True]
        if not selected:
            messagebox.showinfo("Info", "Tidak ada baris yang dipilih untuk diflash.")
            return
        # confirm overall
        if not messagebox.askyesno("Confirm Start", f"Akan mengeksekusi {len(selected)} operasi flash berurutan.\nLanjutkan?"):
            return
        # start worker thread
        threading.Thread(target=self._flash_worker, args=(selected,), daemon=True).start()

    def _flash_worker(self, selected_rows):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("fastboot missing", "fastboot not found in PATH.")
            return
        fb_list = fastboot_devices_list()
        if not fb_list.strip():
            messagebox.showerror("No device", "No fastboot device detected. Enter bootloader first.")
            return

        for idx, r in enumerate(selected_rows, start=1):
            part = r['part_entry'].get().strip()
            path = r['file_entry'].get().strip()
            prog = r['prog']
            if not part:
                output_q.put(f"[MultiFlash] Skipping row {idx}: partition name kosong.\n")
                continue
            if not path or not os.path.exists(path):
                # ask whether to continue or skip
                if not os.path.exists(path):
                    ans = messagebox.askyesno("File not found", f"File untuk partition '{part}' tidak ditemukan: {path}\nSkip this row and continue?")
                    if ans:
                        output_q.put(f"[MultiFlash] Skipped row {idx} (file not found).\n")
                        continue
                    else:
                        output_q.put(f"[MultiFlash] Aborted by user on missing file for partition {part}.\n")
                        return
            # per-row confirmation
            ok = True
            if not self.dryrun_var.get():
                ok = messagebox.askyesno("Confirm Flash", f"Flash partition '{part}' with file:\n{path}\n\nProceed for this row?")
            else:
                # if dry-run, show info but don't require confirm repeatedly (still show a confirmation)
                ok = messagebox.askyesno("Confirm Dry-Run", f"[DRY-RUN] Would execute: fastboot flash {part} {path}\nProceed for this row?")
            if not ok:
                output_q.put(f"[MultiFlash] User skipped row {idx}: {part}\n")
                continue
            # run command and show progress (indeterminate)
            try:
                output_q.put(f"\n[MultiFlash] Executing row {idx}/{len(selected_rows)}: fastboot flash {part} {path}\n")
                prog.config(mode="indeterminate")
                prog.start(20)
                # use run_cmd_stream to capture and stream output
                ret = run_cmd_stream([FASTBOOT, "flash", part, path], self.term, dry_run=self.dryrun_var.get())
                prog.stop()
                prog.config(mode="determinate")
                if ret == 0:
                    output_q.put(f"[MultiFlash] Row {idx} completed successfully.\n")
                    prog['value'] = 100
                else:
                    output_q.put(f"[MultiFlash] Row {idx} ended with code {ret}.\n")
                    prog['value'] = 0
                    # continue to next row (do not abort automatically)
            except Exception as e:
                prog.stop()
                prog.config(mode="determinate")
                prog['value'] = 0
                output_q.put(f"[MultiFlash] Error on row {idx}: {e}\n")
            # small pause between rows
            time.sleep(0.3)
        output_q.put("\n[MultiFlash] All selected rows processed.\n")

# ---------- Multi Push (ADB) ----------
class MultiPushWindow:
    def __init__(self, parent, term_widget, dryrun_var):
        self.parent = parent
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.rows = []
        self.win = tk.Toplevel(parent)
        self.win.title("Multi Push Files (ADB)")
        self.win.geometry("750x400")
        self._build_ui()

    def _build_ui(self):
        ttk.Label(self.win, text="Tambahkan beberapa file dan path tujuan di device").pack(anchor="w", padx=8, pady=(8, 4))
        container = ttk.Frame(self.win)
        container.pack(fill="both", expand=True, padx=8, pady=8)

        self.canvas = tk.Canvas(container)
        self.scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.add_row()

        bottom_frame = ttk.Frame(self.win)
        bottom_frame.pack(fill="x", padx=8, pady=8)

        ttk.Button(bottom_frame, text="+ Add Row", command=self.add_row).pack(side="left")
        ttk.Button(bottom_frame, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(bottom_frame, text="Load From File...", command=self.load_from_file).pack(side="left", padx=4)
        ttk.Button(bottom_frame, text="Save To File...", command=self.save_to_file).pack(side="left", padx=4)

        ttk.Button(bottom_frame, text="Close", command=self.win.destroy).pack(side="right")
        ttk.Button(bottom_frame, text="Start Push", command=self.start_push).pack(side="right", padx=4)

    def add_row(self):
        row = {}
        row["frame"] = ttk.Frame(self.scrollable_frame)
        row["frame"].pack(fill="x", pady=2)

        row["var"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(row["frame"], variable=row["var"]).pack(side="left")

        row["src_entry"] = ttk.Entry(row["frame"], width=40)
        row["src_entry"].pack(side="left", padx=4)
        ttk.Button(row["frame"], text="Browse", command=lambda: self._choose_file(row["src_entry"])).pack(side="left", padx=2)

        row["dest_entry"] = ttk.Entry(row["frame"], width=25)
        row["dest_entry"].insert(0, "/sdcard/")
        row["dest_entry"].pack(side="left", padx=4)

        row["progress"] = ttk.Progressbar(row["frame"], length=120, mode="determinate")
        row["progress"].pack(side="left", padx=4)

        self.rows.append(row)

    def remove_selected(self):
        for row in self.rows[:]:
            if row["var"].get():
                row["frame"].destroy()
                self.rows.remove(row)

    def _choose_file(self, entry):
        fn = filedialog.askopenfilename(title="Select file to push")
        if fn:
            entry.delete(0, tk.END)
            entry.insert(0, fn)

    def start_push(self):
        for row in self.rows:
            src = row["src_entry"].get().strip()
            dest = row["dest_entry"].get().strip()
            if src and dest:
                row["progress"]["value"] = 30
                start_cmd([ADB, "push", src, dest], self.term, dry_run=self.dryrun_var.get())
                row["progress"]["value"] = 100
                self.term.insert(tk.END, f"\n[MultiPush] Pushed: {os.path.basename(src)} → {dest}\n")
        self.term.insert(tk.END, "\n[MultiPush] All selected files pushed.\n")

    def save_to_file(self):
        file_path = filedialog.asksaveasfilename(
            title="Save List", defaultextension=".json", filetypes=[("JSON files", "*.json")]
        )
        if not file_path:
            return
        data = []
        for row in self.rows:
            src = row["src_entry"].get().strip()
            dest = row["dest_entry"].get().strip()
            if src and dest:
                data.append({"src": src, "dest": dest})
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        messagebox.showinfo("Saved", "Multi Push list saved successfully!")

    def load_from_file(self):
        file_path = filedialog.askopenfilename(title="Open List", filetypes=[("JSON files", "*.json")])
        if not file_path:
            return
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for row in self.rows:
            row["frame"].destroy()
        self.rows.clear()
        for item in data:
            self.add_row()
            self.rows[-1]["src_entry"].insert(0, item.get("src", ""))
            self.rows[-1]["dest_entry"].insert(0, item.get("dest", "/sdcard/"))

# ---------- Multi Pull (ADB) ----------
class MultiPullWindow:
    def __init__(self, parent, term_widget, dryrun_var):
        self.parent = parent
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.rows = []
        self.win = tk.Toplevel(parent)
        self.win.title("Multi Pull Files (ADB)")
        self.win.geometry("750x400")
        self._build_ui()

    def _build_ui(self):
        ttk.Label(self.win, text="Tambahkan beberapa path di device dan folder tujuan di PC").pack(anchor="w", padx=8, pady=(8, 4))
        container = ttk.Frame(self.win)
        container.pack(fill="both", expand=True, padx=8, pady=8)

        self.canvas = tk.Canvas(container)
        self.scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.add_row()

        bottom_frame = ttk.Frame(self.win)
        bottom_frame.pack(fill="x", padx=8, pady=8)

        ttk.Button(bottom_frame, text="+ Add Row", command=self.add_row).pack(side="left")
        ttk.Button(bottom_frame, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(bottom_frame, text="Load From File...", command=self.load_from_file).pack(side="left", padx=4)
        ttk.Button(bottom_frame, text="Save To File...", command=self.save_to_file).pack(side="left", padx=4)

        ttk.Button(bottom_frame, text="Close", command=self.win.destroy).pack(side="right")
        ttk.Button(bottom_frame, text="Start Pull", command=self.start_pull).pack(side="right", padx=4)

    def add_row(self):
        row = {}
        row["frame"] = ttk.Frame(self.scrollable_frame)
        row["frame"].pack(fill="x", pady=2)

        row["var"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(row["frame"], variable=row["var"]).pack(side="left")

        row["src_entry"] = ttk.Entry(row["frame"], width=35)
        row["src_entry"].insert(0, "/sdcard/")
        row["src_entry"].pack(side="left", padx=4)

        row["dest_entry"] = ttk.Entry(row["frame"], width=30)
        row["dest_entry"].insert(0, os.getcwd())
        row["dest_entry"].pack(side="left", padx=4)
        ttk.Button(row["frame"], text="Browse", command=lambda: self._choose_folder(row["dest_entry"])).pack(side="left", padx=2)

        row["progress"] = ttk.Progressbar(row["frame"], length=120, mode="determinate")
        row["progress"].pack(side="left", padx=4)

        self.rows.append(row)

    def remove_selected(self):
        for row in self.rows[:]:
            if row["var"].get():
                row["frame"].destroy()
                self.rows.remove(row)

    def _choose_folder(self, entry):
        folder = filedialog.askdirectory(title="Select destination folder")
        if folder:
            entry.delete(0, tk.END)
            entry.insert(0, folder)

    def start_pull(self):
        for row in self.rows:
            src = row["src_entry"].get().strip()
            dest = row["dest_entry"].get().strip()
            if src and dest:
                row["progress"]["value"] = 30
                start_cmd([ADB, "pull", src, dest], self.term, dry_run=self.dryrun_var.get())
                row["progress"]["value"] = 100
                self.term.insert(tk.END, f"\n[MultiPull] Pulled: {src} → {dest}\n")
        self.term.insert(tk.END, "\n[MultiPull] All selected files pulled.\n")

    def save_to_file(self):
        file_path = filedialog.asksaveasfilename(
            title="Save List", defaultextension=".json", filetypes=[("JSON files", "*.json")]
        )
        if not file_path:
            return
        data = []
        for row in self.rows:
            src = row["src_entry"].get().strip()
            dest = row["dest_entry"].get().strip()
            if src and dest:
                data.append({"src": src, "dest": dest})
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        messagebox.showinfo("Saved", "Multi Pull list saved successfully!")

    def load_from_file(self):
        file_path = filedialog.askopenfilename(title="Open List", filetypes=[("JSON files", "*.json")])
        if not file_path:
            return
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for row in self.rows:
            row["frame"].destroy()
        self.rows.clear()
        for item in data:
            self.add_row()
            self.rows[-1]["src_entry"].insert(0, item.get("src", ""))
            self.rows[-1]["dest_entry"].insert(0, item.get("dest", os.getcwd()))

# ---------- Multi ADB Command Window ----------
class MultiADBCommandWindow:
    def __init__(self, parent, term_widget, dryrun_var):
        self.parent = parent
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.rows = []
        self.win = tk.Toplevel(parent)
        self.win.title("Multi Raw ADB Commands")
        self.win.geometry("760x480")
        self._build_ui()

    def _build_ui(self):
        ttk.Label(self.win, text="Tambahkan beberapa perintah ADB untuk dijalankan berurutan").pack(anchor="w", padx=8, pady=(8, 0))

        container = ttk.Frame(self.win)
        container.pack(fill="both", expand=True, padx=8, pady=8)

        # Scrollable area
        canvas = tk.Canvas(container)
        vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Header
        hdr = ttk.Frame(inner)
        hdr.pack(fill="x", pady=2)
        ttk.Label(hdr, text="Sel", width=4).pack(side="left", padx=4)
        ttk.Label(hdr, text="Command", anchor="w").pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(hdr, text="Progress", width=16).pack(side="left", padx=8)

        self.rows_container = inner
        self.add_row()
        self.add_row()

        # Bottom buttons
        btnf = ttk.Frame(self.win)
        btnf.pack(fill="x", padx=8, pady=6)
        ttk.Button(btnf, text="+ Add Row", command=self.add_row).pack(side="left")
        ttk.Button(btnf, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(btnf, text="Load From File...", command=self.load_from_file).pack(side="left", padx=6)
        ttk.Button(btnf, text="Save To File...", command=self.save_to_file).pack(side="left", padx=6)

        actionf = ttk.Frame(self.win)
        actionf.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(actionf, text="Start Commands", command=self.start_commands_confirm).pack(side="right", padx=6)
        ttk.Button(actionf, text="Close", command=self.win.destroy).pack(side="right")

    def add_row(self, cmd_value=""):
        rowf = ttk.Frame(self.rows_container)
        rowf.pack(fill="x", pady=3, padx=2)

        var_chk = tk.BooleanVar(value=True)
        chk = ttk.Checkbutton(rowf, variable=var_chk)
        chk.var = var_chk
        chk.pack(side="left", padx=4)

        # Lebarkan kolom command
        cmd_entry = ttk.Entry(rowf, width=90)  # sebelumnya default kecil
        cmd_entry.insert(0, cmd_value)
        cmd_entry.pack(side="left", fill="x", expand=True, padx=4)

        prog = ttk.Progressbar(rowf, mode="determinate", length=120)
        prog.pack(side="left", padx=8)

        self.rows.append({
            "frame": rowf,
            "chk": chk,
            "cmd_entry": cmd_entry,
            "prog": prog
        })
        self.win.update_idletasks()

    def remove_selected(self):
        for r in self.rows[:]:
            if r["chk"].var.get():
                r["frame"].destroy()
                self.rows.remove(r)

    def load_from_file(self):
        fn = filedialog.askopenfilename(title="Load ADB Command List", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not fn: return
        with open(fn, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
        for ln in lines:
            self.add_row(ln)

    def save_to_file(self):
        fn = filedialog.asksaveasfilename(defaultextension=".txt", title="Save ADB Command List")
        if not fn: return
        with open(fn, "w", encoding="utf-8") as f:
            for r in self.rows:
                cmd = r["cmd_entry"].get().strip()
                if cmd: f.write(cmd + "\n")
        messagebox.showinfo("Saved", f"Saved to {fn}")

    def start_commands_confirm(self):
        selected = [r for r in self.rows if r["chk"].var.get()]
        if not selected:
            messagebox.showinfo("Info", "Tidak ada baris yang dipilih.")
            return
        if not messagebox.askyesno("Confirm", f"Jalankan {len(selected)} perintah ADB berurutan?"):
            return
        threading.Thread(target=self._worker, args=(selected,), daemon=True).start()

    def _worker(self, selected):
        for idx, r in enumerate(selected, start=1):
            cmd = r["cmd_entry"].get().strip()
            prog = r["prog"]
            if not cmd: continue
            argv = split_cmdline(cmd)
            if argv is None:
                output_q.put(f"\n[ADB MultiCmd] Baris {idx}: sintaks quotes tidak valid, dilewati.\n")
                continue
            full_cmd = [ADB] + argv
            output_q.put(f"\n[ADB MultiCmd] Running {idx}: {' '.join(full_cmd)}\n")
            prog.config(mode="indeterminate")
            prog.start(20)
            ret = run_cmd_stream(full_cmd, self.term, dry_run=self.dryrun_var.get())
            prog.stop(); prog.config(mode="determinate")
            prog["value"] = 100 if ret == 0 else 0
        output_q.put("\n[ADB MultiCmd] All commands processed.\n")

# ---------- Multi Fastboot Command Window ----------
class MultiFastbootCommandWindow(MultiADBCommandWindow):
    def __init__(self, parent, term_widget, dryrun_var):
        super().__init__(parent, term_widget, dryrun_var)
        self.win.title("Multi Raw Fastboot Commands")

    def _worker(self, selected):
        for idx, r in enumerate(selected, start=1):
            cmd = r["cmd_entry"].get().strip()
            prog = r["prog"]
            if not cmd: continue
            argv = split_cmdline(cmd)
            if argv is None:
                output_q.put(f"\n[Fastboot MultiCmd] Baris {idx}: sintaks quotes tidak valid, dilewati.\n")
                continue
            full_cmd = [FASTBOOT] + argv
            output_q.put(f"\n[Fastboot MultiCmd] Running {idx}: {' '.join(full_cmd)}\n")
            prog.config(mode="indeterminate")
            prog.start(20)
            ret = run_cmd_stream(full_cmd, self.term, dry_run=self.dryrun_var.get())
            prog.stop(); prog.config(mode="determinate")
            prog["value"] = 100 if ret == 0 else 0
        output_q.put("\n[Fastboot MultiCmd] All commands processed.\n")

# ---------- Scrcpy GUI (visual command builder, mirip Scrcpy-GUI) ----------
# ---------- Clipboard PC <-> HP (pakai modul fcc) ----------
class ClipboardWindow:
    """Window clipboard: tab PTH (PC->HP) + HTP (HP->PC) + Manual dari fcc."""

    def __init__(self, parent):
        if not FCC_AVAILABLE:
            messagebox.showerror("Clipboard", "fcc.py tidak ditemukan di folder app.")
            return
        fcc.ADB = ADB  # ikut path Settings yg aktif
        self.win = tk.Toplevel(parent)
        self.win.title("📋 Clipboard PC ↔ HP")
        self.win.geometry("640x680")
        nb = ttk.Notebook(self.win)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        nb.add(fcc.PthTab(nb), text="PTH (PC → HP)")
        nb.add(fcc.HtpTab(nb), text="HTP (HP → PC)")
        nb.add(fcc.ManualTab(nb), text="Manual")


class ScrcpyGuiWindow:
    """Pilih device + opsi scrcpy lewat GUI, preview command live, Run/Stop."""

    def __init__(self, parent, term_widget, dryrun_var):
        self.parent = parent
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.procs = []

        self.win = tk.Toplevel(parent)
        self.win.title("🖥️ Scrcpy GUI — Screen Mirror")
        self.win.geometry("720x660")
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

        # --- vars ---
        self.device_var = tk.StringVar()
        self.maxsize_var = tk.StringVar()
        self.bitrate_var = tk.StringVar(value="8M")
        self.fps_var = tk.StringVar()
        self.title_var = tk.StringVar(value="FADB")
        self.record_var = tk.StringVar()
        self.ip_var = tk.StringVar()
        self.preview_var = tk.StringVar()
        self.status_var = tk.StringVar(value="0 berjalan")
        self.opt_vars = {
            "no_audio": tk.BooleanVar(value=False),
            "fullscreen": tk.BooleanVar(value=False),
            "always_top": tk.BooleanVar(value=False),
            "stay_awake": tk.BooleanVar(value=True),
            "screen_off": tk.BooleanVar(value=False),
            "show_touches": tk.BooleanVar(value=False),
            "no_control": tk.BooleanVar(value=False),
            "power_off_close": tk.BooleanVar(value=False),
        }

        root = ttk.Frame(self.win, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        # Device
        devf = ttk.LabelFrame(root, text="📱 Device", padding=8)
        devf.pack(fill=tk.X, pady=(0, 8))
        self.device_combo = ttk.Combobox(devf, textvariable=self.device_var, width=32)
        self.device_combo.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(devf, text="🔄 Refresh", command=self.refresh_devices).pack(side=tk.LEFT, padx=4)
        ttk.Label(devf, text="boleh ketik serial manual").pack(side=tk.LEFT, padx=6)

        # TCP/IP cepat
        ipf = ttk.Frame(devf)
        ipf.pack(side=tk.RIGHT)
        ttk.Entry(ipf, textvariable=self.ip_var, width=16).pack(side=tk.LEFT, padx=4)
        self.ip_var.set("192.168.1.x:5555")
        ttk.Button(ipf, text="📶 Connect", command=self.tcp_connect).pack(side=tk.LEFT, padx=2)
        ttk.Button(ipf, text="🔌 Disconnect", command=self.tcp_disconnect).pack(side=tk.LEFT, padx=2)

        # Video
        vidf = ttk.LabelFrame(root, text="🎞️ Video", padding=8)
        vidf.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(vidf, text="Max size").pack(side=tk.LEFT)
        ttk.Entry(vidf, textvariable=self.maxsize_var, width=8).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(vidf, text="Bitrate").pack(side=tk.LEFT)
        ttk.Entry(vidf, textvariable=self.bitrate_var, width=8).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(vidf, text="Max FPS").pack(side=tk.LEFT)
        ttk.Entry(vidf, textvariable=self.fps_var, width=6).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Checkbutton(vidf, text="🔇 No audio", variable=self.opt_vars["no_audio"]).pack(side=tk.LEFT, padx=4)

        # Window
        winf = ttk.LabelFrame(root, text="🪟 Window", padding=8)
        winf.pack(fill=tk.X, pady=(0, 8))
        ttk.Checkbutton(winf, text="⛶ Fullscreen", variable=self.opt_vars["fullscreen"]).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(winf, text="📌 Always on top", variable=self.opt_vars["always_top"]).pack(side=tk.LEFT, padx=4)
        ttk.Label(winf, text="Title").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(winf, textvariable=self.title_var, width=12).pack(side=tk.LEFT, padx=4)

        # Device options
        devof = ttk.LabelFrame(root, text="⚙️ Device", padding=8)
        devof.pack(fill=tk.X, pady=(0, 8))
        ttk.Checkbutton(devof, text="☀ Stay awake", variable=self.opt_vars["stay_awake"]).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(devof, text="📴 Screen off", variable=self.opt_vars["screen_off"]).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(devof, text="👆 Show touches", variable=self.opt_vars["show_touches"]).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(devof, text="🚫 No control", variable=self.opt_vars["no_control"]).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(devof, text="🔌 Power off on close", variable=self.opt_vars["power_off_close"]).pack(side=tk.LEFT, padx=4)

        # Record
        recf = ttk.LabelFrame(root, text="⏺️ Record", padding=8)
        recf.pack(fill=tk.X, pady=(0, 8))
        ttk.Entry(recf, textvariable=self.record_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(recf, text="📂 Browse...", command=self.browse_record).pack(side=tk.LEFT)

        # Preview
        ttk.Label(root, text="👁️ Preview command:").pack(anchor="w")
        prev = ttk.Entry(root, textvariable=self.preview_var, state="readonly")
        prev.pack(fill=tk.X, pady=(0, 8))

        # Actions
        actf = ttk.Frame(root)
        actf.pack(fill=tk.X)
        ttk.Button(actf, text="▶ Run", command=self.run).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actf, text="⏹ Stop All", command=self.stop_all).pack(side=tk.LEFT, padx=6)
        ttk.Button(actf, text="Close", command=self.on_close).pack(side=tk.RIGHT)
        ttk.Label(actf, textvariable=self.status_var).pack(side=tk.RIGHT, padx=10)

        for v in [self.device_var, self.maxsize_var, self.bitrate_var,
                  self.fps_var, self.title_var, self.record_var,
                  *self.opt_vars.values()]:
            v.trace_add("write", lambda *a: self.update_preview())

        self.refresh_devices()
        self.update_preview()

    # --- device ---
    def refresh_devices(self):
        try:
            res = subprocess.run([ADB, "devices"], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=5)
            usable, other = [], []
            for ln in (res.stdout or "").splitlines()[1:]:
                parts = ln.split()
                if len(parts) >= 2:
                    (usable if parts[1] == "device" else other).append(parts[0])
            self.device_combo["values"] = usable
            if usable and not self.device_var.get():
                self.device_var.set(usable[0])
            msg = f"{len(usable)} device siap"
            if other:
                msg += f" ({len(other)} unauthorized/offline)"
            self.status_var.set(msg + " • 0 berjalan")
            self._update_running_label()
        except Exception as e:
            self.status_var.set(f"Gagal refresh: {e}")

    def tcp_connect(self):
        ip = self.ip_var.get().strip()
        if not ip or ip == "192.168.1.x:5555":
            messagebox.showinfo("TCP/IP", "Isi IP:port device dulu (mis. 192.168.1.10:5555).")
            return
        self._log(f"[ScrcpyGUI] adb connect {ip}")
        threading.Thread(target=self._tcp_worker,
                         args=(["connect", ip],), daemon=True).start()

    def tcp_disconnect(self):
        self._log("[ScrcpyGUI] adb disconnect")
        threading.Thread(target=self._tcp_worker,
                         args=(["disconnect"],), daemon=True).start()

    def _tcp_worker(self, args):
        try:
            res = subprocess.run([ADB] + args, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=15)
            self._log((res.stdout or "") + (res.stderr or ""))
        except Exception as e:
            self._log(f"[ScrcpyGUI] TCP error: {e}")
        self.win.after(500, self.refresh_devices)

    # --- command ---
    def build_cmd(self):
        cmd = [SCRCPY]
        serial = self.device_var.get().strip()
        if serial:
            cmd += ["-s", serial]
        if self.maxsize_var.get().strip():
            cmd += ["-m", self.maxsize_var.get().strip()]
        if self.bitrate_var.get().strip():
            cmd += ["-b", self.bitrate_var.get().strip()]
        if self.fps_var.get().strip():
            cmd += ["--max-fps", self.fps_var.get().strip()]
        if self.opt_vars["no_audio"].get():
            cmd += ["--no-audio"]
        if self.opt_vars["fullscreen"].get():
            cmd += ["-f"]
        if self.opt_vars["always_top"].get():
            cmd += ["--always-on-top"]
        if self.title_var.get().strip():
            cmd += ["--window-title", self.title_var.get().strip()]
        if self.opt_vars["stay_awake"].get():
            cmd += ["-w"]
        if self.opt_vars["screen_off"].get():
            cmd += ["-S"]
        if self.opt_vars["show_touches"].get():
            cmd += ["-t"]
        if self.opt_vars["no_control"].get():
            cmd += ["--no-control"]
        if self.opt_vars["power_off_close"].get():
            cmd += ["--power-off-on-close"]
        if self.record_var.get().strip():
            cmd += ["--record", self.record_var.get().strip()]
        return cmd

    @staticmethod
    def fmt_cmd(argv):
        out = []
        for a in argv:
            if any(c in a for c in " \t\"'"):
                a = '"' + a.replace('"', '\\"') + '"'
            out.append(a)
        return " ".join(out)

    def update_preview(self):
        try:
            self.preview_var.set(self.fmt_cmd(self.build_cmd()))
        except Exception:
            pass

    def browse_record(self):
        fn = filedialog.asksaveasfilename(defaultextension=".mp4",
                                          filetypes=[("MP4", "*.mp4"), ("MKV", "*.mkv")])
        if fn:
            self.record_var.set(fn)

    # --- run/stop ---
    def run(self):
        cmd = self.build_cmd()
        self._log(f"[ScrcpyGUI] $ {self.fmt_cmd(cmd)}")
        if self.dryrun_var.get():
            messagebox.showinfo("Dry-Run", "Mode dry-run: command tidak dijalankan.")
            return
        try:
            p = subprocess.Popen(cmd)
            self.procs.append(p)
            self._update_running_label()
        except FileNotFoundError:
            messagebox.showerror("Scrcpy not found", "Install scrcpy dan pastikan ada di PATH.")
        except Exception as e:
            messagebox.showerror("Scrcpy error", str(e))

    def stop_all(self):
        for p in self.procs:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=4)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass
            except Exception:
                pass
        # pastikan benar-benar mati sebelum lanjut (teardown bisa lambat)
        import time as _t
        for _ in range(30):
            if all(p.poll() is not None for p in self.procs):
                break
            _t.sleep(0.1)
        self.procs = [p for p in self.procs if p.poll() is None]
        self._update_running_label()
        self._log("[ScrcpyGUI] Semua scrcpy dihentikan.")

    def _update_running_label(self):
        self.procs = [p for p in self.procs if p.poll() is None]
        base = self.status_var.get().split("•")[0].strip()
        self.status_var.set(f"{base} • {len(self.procs)} berjalan")

    def on_close(self):
        self.stop_all()
        try:
            self.win.destroy()
        except Exception:
            pass

    def _log(self, msg):
        try:
            self.term.insert(tk.END, msg.rstrip() + "\n")
            self.term.see(tk.END)
        except Exception:
            pass


# ---------- ADB File Explorer ----------
class ADBFileExplorer:
    def __init__(self, parent, term_widget, dryrun_var, root_mode=False):
        self.parent = parent
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.root_mode = root_mode
        self.base_path = "/" if root_mode else "/sdcard/"
        self.current_path = self.base_path
        self.win = tk.Toplevel(parent)
        mode_text = "Root Mode" if root_mode else "Non-Root Mode"
        self.win.title(f"ADB File Explorer — {mode_text}")
        self.win.geometry("850x500")
        self._build_ui()
        self.list_directory()

    def _build_ui(self):
        # Toolbar
        toolbar = ttk.Frame(self.win)
        toolbar.pack(fill="x", padx=8, pady=4)

        ttk.Button(toolbar, text="⬅️ Back", command=self.go_back).pack(side="left", padx=4)
        ttk.Button(toolbar, text="🔄 Refresh", command=self.list_directory).pack(side="left", padx=4)
        ttk.Button(toolbar, text="📤 Push File", command=self.push_file).pack(side="left", padx=4)

        ttk.Label(toolbar, text="Path:").pack(side="left", padx=(12, 2))
        self.path_var = tk.StringVar(value=self.current_path)
        path_entry = ttk.Entry(toolbar, textvariable=self.path_var, width=50)
        path_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(toolbar, text="Go", command=self.go_to_path).pack(side="left", padx=4)

        # Mode label kanan atas
        ttk.Label(toolbar, text=f"Mode: {'ROOT' if self.root_mode else 'NORMAL'}",
                  font=("Segoe UI", 9, "bold"),
                  foreground="#FF5555" if self.root_mode else "#22AA22").pack(side="right", padx=8)

        # Table
        columns = ("Name", "Type", "Size")
        self.tree = ttk.Treeview(self.win, columns=columns, show="headings")
        self.tree.heading("Name", text="Name")
        self.tree.heading("Type", text="Type")
        self.tree.heading("Size", text="Size (KB)")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)

        scrollbar = ttk.Scrollbar(self.tree, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.on_item_double_click)

        # Bottom buttons
        bottom = ttk.Frame(self.win)
        bottom.pack(fill="x", padx=8, pady=8)
        ttk.Button(bottom, text="📂 Pull Selected", command=self.pull_selected).pack(side="left", padx=4)
        ttk.Button(bottom, text="❌ Delete Selected", command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(bottom, text="Close", command=self.win.destroy).pack(side="right", padx=4)

    # ---------------- Core Logic ---------------- #
    def adb_cmd(self, base_cmd):
        """Handle root / non-root automatically"""
        if self.root_mode:
            return [ADB, "shell", "su", "-c", base_cmd]
        else:
            return [ADB, "shell", base_cmd]

    def list_directory(self):
        """List isi folder saat ini"""
        self.tree.delete(*self.tree.get_children())
        path = self.path_var.get().strip()
        if not path:
            path = self.base_path
        self.current_path = path

        try:
            cmd = f"ls -l '{path}'"
            result = subprocess.run(self.adb_cmd(cmd), capture_output=True, text=True, encoding="utf-8")
            output = result.stdout.strip().splitlines()

            for line in output:
                if not line or line.startswith("total"):
                    continue
                parts = line.split()
                if len(parts) < 6:
                    continue

                perms, _, _, _, size, *rest = parts
                name = rest[-1]
                type_ = "Folder" if perms.startswith("d") else "File"
                size_kb = int(size) // 1024 if size.isdigit() else 0
                self.tree.insert("", "end", values=(name, type_, size_kb))

            mode = "Root" if self.root_mode else "Normal"
            self.term.insert(tk.END, f"\n[FileExplorer] Listed {path} ({mode})\n")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to list {path}\n{e}")

    def on_item_double_click(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        name, type_, _ = self.tree.item(selected[0], "values")
        new_path = self.current_path.rstrip("/") + "/" + name
        if type_ == "Folder":
            self.path_var.set(new_path)
            self.list_directory()

    def go_back(self):
        """Go up one directory safely with mode limits"""
        path = self.current_path.strip("/")

        # batas root berdasarkan mode
        limit_path = "/" if self.root_mode else "/sdcard/"

        # Jika sudah di limit -> stop
        if self.current_path.rstrip("/") == limit_path.rstrip("/"):
            self.term.insert(tk.END, f"\n[FileExplorer] Already at top directory: {limit_path}\n")
            return

        # Naik satu folder
        parts = path.split("/")
        new_path = "/" if len(parts) == 1 else "/" + "/".join(parts[:-1])

        if not new_path.endswith("/"):
            new_path += "/"

        # kalau udah di atas batas non-root, balik ke /sdcard/
        if not self.root_mode and not new_path.startswith("/sdcard"):
            new_path = "/sdcard/"

        self.path_var.set(new_path)
        self.list_directory()

    def go_to_path(self):
        self.list_directory()

    def pull_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Select a file to pull first.")
            return

        dest = filedialog.askdirectory(title="Select destination folder")
        if not dest:
            return

        for sel in selected:
            name, type_, _ = self.tree.item(sel, "values")
            if type_ != "File":
                continue
            remote = self.current_path.rstrip("/") + "/" + name
            self.term.insert(tk.END, f"\n[Pull] {remote} → {dest}\n")
            start_cmd([ADB, "pull", remote, dest], self.term, dry_run=self.dryrun_var.get())

    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Select file(s) to delete.")
            return

        if not messagebox.askyesno("Confirm", "Delete selected file(s)?"):
            return

        for sel in selected:
            name, type_, _ = self.tree.item(sel, "values")
            remote = self.current_path.rstrip("/") + "/" + name
            cmd = f"rm -rf '{remote}'"
            start_cmd(self.adb_cmd(cmd), self.term, dry_run=self.dryrun_var.get())
            self.term.insert(tk.END, f"\n[Delete] {remote}\n")

        self.list_directory()

    def push_file(self):
        file_path = filedialog.askopenfilename(title="Select file to upload")
        if not file_path:
            return
        remote_path = self.current_path.rstrip("/") + "/"
        self.term.insert(tk.END, f"\n[Push] {file_path} → {remote_path}\n")
        start_cmd([ADB, "push", file_path, remote_path], self.term, dry_run=self.dryrun_var.get())
        self.list_directory()

# ---------- Root Checker ----------
class RootCheckerWindow:
    def __init__(self, parent, term_widget, dryrun_var):
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.win = tk.Toplevel(parent)
        self.win.title("Root Checker / Magisk Detector")
        self.win.geometry("400x280")
        self._build_ui()
        self.check_root()

    def _build_ui(self):
        frame = ttk.Frame(self.win, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Root Checker", font=("Segoe UI", 12, "bold")).pack(pady=(0,8))
        self.result_label = ttk.Label(frame, text="Checking...", font=("Segoe UI", 11))
        self.result_label.pack(pady=8)

        self.detail_text = tk.Text(frame, height=8, wrap="word", font=("Consolas", 9))
        self.detail_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.detail_text.insert("1.0", "Running checks...\n")
        self.detail_text.configure(state="disabled")

        ttk.Button(frame, text="Re-check", command=self.check_root).pack(side="left", padx=8, pady=8)
        ttk.Button(frame, text="Close", command=self.win.destroy).pack(side="right", padx=8, pady=8)

    def append_log(self, text):
        self.detail_text.configure(state="normal")
        self.detail_text.insert(tk.END, text + "\n")
        self.detail_text.configure(state="disabled")
        self.detail_text.see(tk.END)

    def check_root(self):
        self.append_log("Checking root access...")
        self.result_label.config(text="Checking...")

        su_cmd = [ADB, "shell", "su", "-c", "id"]
        magisk_cmd = [ADB, "shell", "which", "magisk"]

        su_out = subprocess.run(su_cmd, capture_output=True, text=True, encoding="utf-8")
        magisk_out = subprocess.run(magisk_cmd, capture_output=True, text=True, encoding="utf-8")

        su_result = su_out.stdout.strip()
        magisk_result = magisk_out.stdout.strip()

        self.term.insert(tk.END, f"\n[RootChecker] su output: {su_result}\n")
        self.term.insert(tk.END, f"[RootChecker] magisk output: {magisk_result}\n")

        root_detected = "uid=0" in su_result or "root" in su_result.lower()
        magisk_detected = "/magisk" in magisk_result or "magisk" in magisk_result.lower()

        if root_detected and magisk_detected:
            status = "🟢 Rooted (Magisk Detected)"
            color = "green"
        elif root_detected:
            status = "🟡 Rooted (No Magisk)"
            color = "goldenrod"
        else:
            status = "🔴 Not Rooted"
            color = "red"

        self.result_label.config(text=status, foreground=color)
        self.append_log(f"Result: {status}")
        self.append_log("-" * 40)

# ---------- 🧹 Debloat Preset Manager ----------
class DebloatPresetWindow:
    def __init__(self, parent, term_widget, dryrun_var):
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.pkgs = []
        self.win = tk.Toplevel(parent)
        self.win.title("🧹 Debloat Preset Manager — ADB Tools")
        self.win.geometry("650x450")
        self._build_ui()

    def _build_ui(self):
        frame = ttk.Frame(self.win, padding=8)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="🧹 Debloat Preset Manager", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            frame,
            text="Select a preset file (.txt or .json) containing a list of packages to disable or uninstall."
        ).pack(anchor="w", pady=(0, 6))

        ttk.Button(frame, text="📁 Load Preset File", command=self.load_preset_file).pack(anchor="w", pady=4)

        columns = ("pkg", "status")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        self.tree.heading("pkg", text="Package Name")
        self.tree.heading("status", text="Status")
        self.tree.column("pkg", width=420)
        self.tree.column("status", width=120, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=6)

        btnf = ttk.Frame(frame)
        btnf.pack(fill="x", pady=4)
        ttk.Button(btnf, text="🚫 Disable Selected", command=lambda: self.execute_selected("disable")).pack(side="left", padx=4)
        ttk.Button(btnf, text="❌ Uninstall Selected", command=lambda: self.execute_selected("uninstall")).pack(side="left", padx=4)
        ttk.Button(btnf, text="✖ Close", command=self.win.destroy).pack(side="right", padx=4)

    def load_preset_file(self):
        fn = filedialog.askopenfilename(
            title="📂 Select Preset File (.txt or .json)",
            filetypes=[("Preset Files", "*.txt *.json"), ("All Files", "*.*")]
        )
        if not fn:
            return

        pkgs = []
        try:
            if fn.lower().endswith(".json"):
                with open(fn, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        pkgs = [p.strip() for p in data if p.strip()]
            else:
                with open(fn, "r", encoding="utf-8") as f:
                    pkgs = [ln.strip() for ln in f.readlines() if ln.strip()]
        except Exception as e:
            messagebox.showerror("❌ Error", f"Failed to read preset file:\n{e}")
            return

        self.tree.delete(*self.tree.get_children())
        self.pkgs = pkgs
        for p in pkgs:
            self.tree.insert("", "end", values=(p, "Pending ⏳"))

        messagebox.showinfo("✅ Loaded", f"{len(pkgs)} package(s) loaded from preset successfully!")

    def execute_selected(self, mode):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("⚠️ No Selection", "Please select at least one package before executing.")
            return

        if mode == "uninstall":
            confirm = messagebox.askyesno("🗑 Confirm Uninstall", f"Uninstall {len(selected)} package(s)? Continue?")
            adb_cmd = ["pm", "uninstall", "--user", "0"]
        else:
            confirm = messagebox.askyesno("🚫 Confirm Disable", f"Disable {len(selected)} package(s)? Continue?")
            adb_cmd = ["pm", "disable-user", "--user", "0"]

        if not confirm:
            return

        for sel in selected:
            pkg = self.tree.item(sel, "values")[0]
            cmd = [ADB, "shell"] + adb_cmd + [pkg]
            self.term.insert(tk.END, f"\n[Debloat] {' '.join(adb_cmd)} {pkg}\n")
            start_cmd(cmd, self.term, dry_run=self.dryrun_var.get())
            self.tree.set(sel, "status", "Executed ✅")

        messagebox.showinfo("🎉 Completed", "Operation finished successfully!\nCheck the terminal log for details.")

# ---------- Device Info Window ----------
class DeviceInfoWindow:
    def __init__(self, parent, term_widget, dryrun_var):
        self.term = term_widget
        self.dryrun_var = dryrun_var
        self.win = tk.Toplevel(parent)
        self.win.title("Device Info (About Phone)")
        self.win.geometry("460x400")
        self.build_ui()
        self.load_device_info()

    def build_ui(self):
        frame = ttk.Frame(self.win, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Device Information", font=("Segoe UI", 13, "bold")).pack(pady=(0,8))

        # Teks info
        self.info_text = tk.Text(frame, height=16, wrap="word", font=("Consolas", 10))
        self.info_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.info_text.insert("1.0", "Fetching device information...\n")
        self.info_text.configure(state="disabled")

        # Tombol
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=8)
        ttk.Button(btn_frame, text="🔄 Refresh", command=self.load_device_info).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="📋 Copy All", command=self.copy_info).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Close", command=self.win.destroy).pack(side="right", padx=5)

    def append_text(self, text):
        self.info_text.configure(state="normal")
        self.info_text.insert(tk.END, text + "\n")
        self.info_text.configure(state="disabled")
        self.info_text.see(tk.END)

    def load_device_info(self):
        """Mengambil info device via adb getprop"""
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert("1.0", "Collecting info...\n")
        self.info_text.configure(state="disabled")

        # Properti yang mau diambil
        props = {
            "Brand": "ro.product.brand",
            "Model": "ro.product.model",
            "Device": "ro.product.device",
            "Android Version": "ro.build.version.release",
            "SDK Level": "ro.build.version.sdk",
            "Security Patch": "ro.build.version.security_patch",
            "Build ID": "ro.build.display.id",
            "Fingerprint": "ro.build.fingerprint",
            "Manufacturer": "ro.product.manufacturer",
        }

        info_dict = {}

        for label, prop in props.items():
            cmd = [ADB, "shell", "getprop", prop]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            val = result.stdout.strip() or "-"
            info_dict[label] = val
            self.term.insert(tk.END, f"[DeviceInfo] {label}: {val}\n")

        # tampilkan hasil
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert(tk.END, "📱 DEVICE INFORMATION\n\n")

        for label, val in info_dict.items():
            self.info_text.insert(tk.END, f"{label:<18}: {val}\n")

        self.info_text.configure(state="disabled")

    def copy_info(self):
        text = self.info_text.get("1.0", tk.END)
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        messagebox.showinfo("Copied", "Device info copied to clipboard!")

# ---------- Smart Logger System ----------
import os
import logging
from datetime import datetime
import tkinter as tk
from tkinter import messagebox

class SmartLogger:
    def __init__(self, app_ref):
        self.app = app_ref

        # Ambil lokasi file utama .py
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_dir = os.path.join(base_dir, "logs")
        os.makedirs(self.log_dir, exist_ok=True)

        # Buat file log baru
        log_file = os.path.join(
            self.log_dir,
            f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )

        # Setup logger
        self.logger = logging.getLogger("FaaRamadhanLogger")
        self.logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s — %(levelname)s — %(message)s", "%H:%M:%S")
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)

    def success(self, msg):
        self._log("SUCCESS", msg)
        self.logger.info(msg)

    def error(self, msg):
        self._log("ERROR", msg)
        self.logger.error(msg)
        self._smart_suggest(msg)

    def info(self, msg):
        self._log("INFO", msg)
        self.logger.info(msg)

    def _log(self, level, msg):
        try:
            self.app.term.configure(state="normal")
            self.app.term.insert(tk.END, f"[{level}] {msg}\n")
            self.app.term.configure(state="disabled")
            self.app.term.see(tk.END)
        except Exception:
            pass

    def _smart_suggest(self, msg):
        suggestion = "Try reconnecting your device or check the ADB path."
        if "not found" in msg.lower():
            suggestion = "ADB binary not found. Make sure adb.exe is in PATH or same folder."
        elif "unauthorized" in msg.lower():
            suggestion = "Authorize the device on your phone’s screen, then try again."
        elif "offline" in msg.lower():
            suggestion = "Device is offline. Try unplugging and reconnecting the USB cable."
        elif "install" in msg.lower():
            suggestion = "APK install failed. Enable 'Install via USB' in Developer Options."
        elif "permission" in msg.lower():
            suggestion = "Permission denied. Try re-enabling USB Debugging."
        elif "timeout" in msg.lower():
            suggestion = "Connection timed out. Restart adb with 'adb kill-server && adb start-server'."

        messagebox.showwarning("Smart Error Suggestion", f"{msg}\n\n💡 Suggestion:\n{suggestion}")

# ---------- GUI App ----------
class FaaRamadhanApp:
    def __init__(self, root):
        self.root = root
        # Modern look: pakai theme bootstrap terang kalau tersedia,
        # fallback ke clam + palet custom.
        if BOOTSTRAP_AVAILABLE:
            try:
                self.style = tb.Style()
            except Exception:
                self.style = None
        else:
            self.style = None

        root.title(f"ADB & Fastboot by Faa Ramadhan v{APP_VERSION}")
        root.state('zoomed')
        try:
            root.minsize(1100, 700)
        except Exception:
            pass

        self.logger = SmartLogger(self)
        self.logger.info("Application started.")

        # === Apply loaded theme ===
        theme = current_theme if current_theme else DEFAULT_THEME
        self.rgb_enabled = current_theme.get("rgb_logo", True)

        self.root.configure(bg=theme["bg"])

        # Styling modern terpusat (Segoe UI + palet Light Blue Sea)
        self._apply_modern_style(theme)

        # === 🌊 App Header ===
        header = ttk.Frame(root, padding=(12, 10))
        header.pack(fill=tk.X)
        ttk.Label(header, text="🌊 ADB & Fastboot Tools", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text="by Faa Ramadhan  •  Light Blue Sea  •  v" + APP_VERSION, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(10, 0))

        # === 🧭 Main Toolbar ===
        toolbar = ttk.Frame(root, padding=6)
        toolbar.pack(fill=tk.X)

        # Mode selector
        ttk.Label(toolbar, text="🧭 Mode:").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="ADB")
        ttk.Radiobutton(toolbar, text="📱 ADB", variable=self.mode_var, value="ADB", command=self.rebuild_left).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(toolbar, text="⚡ Fastboot", variable=self.mode_var, value="Fastboot", command=self.rebuild_left).pack(side=tk.LEFT, padx=4)

        # Right-side tools
        ttk.Button(toolbar, text="🔄 Refresh Device Now", command=self.manual_refresh).pack(side=tk.RIGHT, padx=4)
        ttk.Button(toolbar, text="⚙️ Settings", command=self.open_settings).pack(side=tk.RIGHT, padx=4)
        ttk.Button(toolbar, text="🚀 Start Shizuku", command=self.start_shizuku).pack(side=tk.RIGHT, padx=4)

        # main panes
        paned = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        self.left = ttk.Frame(paned, width=380, padding=8)
        paned.add(self.left, weight=0)
        right = ttk.Frame(paned, padding=8)
        paned.add(right, weight=1)

        # left content (will be built)
        self.dryrun_var = tk.BooleanVar(value=False)
        self.force_var = tk.BooleanVar(value=False)
        self.build_left_adb()

        # === 🖥️ Terminal & Progress ===
        ttk.Label(right, text="🧠 Terminal Output:").pack(anchor=tk.W)
        term_bg = current_theme.get("terminal_bg", DEFAULT_THEME["terminal_bg"])
        term_fg = current_theme.get("terminal_fg", DEFAULT_THEME["terminal_fg"])

        self.term = tk.Text(
            right,
            state="disabled",
            wrap="none",
            height=28,
            bg=term_bg,
            fg=term_fg,
            insertbackground=term_fg,
        )

        self.term.pack(fill=tk.BOTH, expand=True)

        # === 📊 Progress area under terminal ===
        progress_frame = ttk.Frame(right)
        progress_frame.pack(fill=tk.X, pady=(6, 0))
        self.progress_label = ttk.Label(progress_frame, text="⚙️ Status: Idle")
        self.progress_label.pack(side=tk.LEFT)
        self.prog = ttk.Progressbar(progress_frame, mode='indeterminate')
        self.prog.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        self.prog.pack_forget()
        self.prog_running = False

        # === 🧩 Bottom buttons ===
        tbtn = ttk.Frame(right)
        tbtn.pack(fill=tk.X, pady=6)

        ttk.Button(tbtn, text="🧹 Clear", command=self.clear_term).pack(side=tk.LEFT)
        ttk.Button(tbtn, text="🛑 Stop", command=self.stop_proc).pack(side=tk.LEFT, padx=6)
        ttk.Button(tbtn, text="💾 Save Log", command=self.save_log).pack(side=tk.LEFT, padx=6)
        ttk.Button(tbtn, text="🧹 Clear Logs Folder", command=self.clear_logs_folder).pack(side=tk.LEFT, padx=6)
        ttk.Button(tbtn, text="📱 Show Device Info", command=lambda: show_device_info(root)).pack(side=tk.LEFT, padx=6)
        ttk.Button(tbtn, text="🐞 Logcat Viewer", command=lambda: start_logcat_window(root, dry_run=self.dryrun_var.get())).pack(side=tk.LEFT, padx=6)

        # === ⚙️ Options ===
        ttk.Checkbutton(tbtn, text="🧪 Dry-run (Simulasi)", variable=self.dryrun_var).pack(side=tk.RIGHT, padx=6)
        ttk.Checkbutton(tbtn, text="⚡ Force (Skip Confirmations)", variable=self.force_var).pack(side=tk.RIGHT)

        # === 📡 Status Bar ===
        self.status = ttk.Label(root, text="🛰️ Initializing...", relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

        # === Logo RGB "Faa Ramadhan" di tengah atas dengan efek Neon + Typing (versi slow) ===
        import itertools

        self.rgb_text_full = "Faa Ramadhan"
        self.rgb_label = tk.Label(
            self.root,
            text="",
            font=("Segoe UI", 18, "bold"),
            bg=theme.get("bg", DEFAULT_THEME["bg"]),
        )
        self.rgb_label.place(relx=0.5, rely=0.02, anchor="n")
        if not self.rgb_enabled:
            self.rgb_label.place_forget()

        self.rgb_colors = [
            "#ff004c", "#ff7b00", "#ffe600", "#00ff6a",
            "#00d9ff", "#6a00ff", "#ff00ea"
        ]
        self.rgb_cycle = itertools.cycle(self.rgb_colors)

        self.current_text = ""
        self.typing_index = 0
        self.is_typing = True
        self.neon_brightness = 0
        self.neon_direction = 1

        def update_label_effect():
            try:
                color = next(self.rgb_cycle)

                # Glow brightness (efek pelan)
                self.neon_brightness += self.neon_direction * 4
                if self.neon_brightness >= 100:
                    self.neon_direction = -1
                elif self.neon_brightness <= 0:
                    self.neon_direction = 1

                # Efek ngetik + hapus ulang
                if self.is_typing:
                    if self.typing_index < len(self.rgb_text_full):
                        self.typing_index += 1
                        self.current_text = self.rgb_text_full[:self.typing_index]
                    else:
                        self.is_typing = False
                        self.root.after(1200, lambda: setattr(self, "is_typing", True))
                        self.typing_index = 0
                        self.current_text = ""

                self.rgb_label.config(
                    text=self.current_text,
                    fg=color,
                    bg=(current_theme.get("bg", DEFAULT_THEME["bg"])
                        if isinstance(current_theme, dict) else DEFAULT_THEME["bg"]),
                )
                self.root.after(250, update_label_effect)  # 250ms biar smooth
            except tk.TclError:
                return

        update_label_effect()

        # pollers
        self.root.after(120, self.poll_output)
        self.root.after(500, self.poll_device_state)
        self.root.after(200, self.update_progress_state)

        # ttkbootstrap menimpa warna tk.Text saat idle pertama;
        # paksa warna terminal kembali setelah itu.
        self._fix_terminal_colors()

    def _fix_terminal_colors(self):
        """Kembalikan warna terminal custom (anti-timpa ttkbootstrap)."""
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            theme = current_theme if current_theme else DEFAULT_THEME
            self.term.configure(
                bg=theme.get("terminal_bg", DEFAULT_THEME["terminal_bg"]),
                fg=theme.get("terminal_fg", DEFAULT_THEME["terminal_fg"]),
                insertbackground=theme.get("terminal_fg", DEFAULT_THEME["terminal_fg"]),
            )
        except Exception:
            pass

    # ----- Two-column ADB left panel (scrollable, fixed layout) -----
    def build_left_adb(self):
        try:
            self.left.unbind_all("<MouseWheel>")
        except:
            pass

        # Hapus isi kiri
        for w in self.left.winfo_children():
            w.destroy()

        # === Scrollable container ===
        outer_frame = ttk.Frame(self.left)
        outer_frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(outer_frame, highlightthickness=0, bg="#e8ffe8", borderwidth=0)
        scrollbar = ttk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview)

        # Frame isi scroll
        scrollable_frame = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        # Pastikan tinggi scroll selalu sesuai konten
        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(canvas_window, width=canvas.winfo_width())

        scrollable_frame.bind("<Configure>", _on_configure)
        canvas.configure(yscrollcommand=scrollbar.set)

        outer_frame.columnconfigure(0, weight=1)
        outer_frame.rowconfigure(0, weight=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Scroll dengan roda mouse
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        container = scrollable_frame

        # === Header ===
        ttk.Label(container, text="⚙️ ADB Mode", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        # Helper tombol
        def btn(text, cmd, r, c, colspan=1):
            b = ttk.Button(container, text=text, command=cmd)
            b.grid(row=r, column=c, sticky="ew", padx=4, pady=3, columnspan=colspan)
            return b

        container.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(1, weight=1)
        row = 1

        # === 📦 Basic ADB Control ===
        ttk.Label(container, text="📦 Basic ADB Control", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8,2))
        row += 1
        btn("🔍 Check devices", self.cmd_devices, row, 0)
        btn("💻 Shell (prompt)", self.adb_shell_prompt, row, 1)
        row += 1
        btn("🖵 ADB Terminal", self.open_adb_terminal, row, 0, colspan=2)

        # === 🧩 ADB Raw Command ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="🧩 ADB Raw Command", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("💬 Raw ADB Command", self.adb_raw_prompt, row, 0)
        btn("🧩 Multi Raw ADB Commands", lambda: MultiADBCommandWindow(self.root, self.term, self.dryrun_var), row, 1)

        # === 📱 Apps & Packages ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="📱 Apps & Packages", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("📦 Package Manager", self.open_package_manager, row, 0)
        btn("📲 Install APK (Choose File)", self.adb_install_apk, row, 1)

        # === 📂 File Operations ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="📂 File Operations", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("📤 Push File → Device", self.adb_push_file, row, 0)
        btn("📥 Pull File ← Device", self.adb_pull_file, row, 1)

        row += 1
        btn("📦 Multi Push Files → Device", self.open_multi_push_window, row, 0)
        btn("📁 Multi Pull Files ← Device", self.open_multi_pull_window, row, 1)

        row += 1
        btn("🗂️ File Explorer (ADB)", self.open_file_explorer, row, 0, colspan=2)

        # === 🔋 Tools ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="🔋 Tools", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("🔐 Permission Manager", self.open_permission_manager_ui, row, 0, colspan=2)

        row += 1
        btn("⚙️ Presets (Debloat Sample)", self.run_preset_debloat, row, 0)
        btn("🧠 Smart Error Log", self.open_log_folder, row, 1)

        row += 1
        btn("📱 Device Info (About Phone)", self.open_device_info_window, row, 0)
        btn("🔬 Device Analyzer (Full)", self.open_smart_device_analyzer, row, 1)

        row += 1
        btn("🖥️ Scrcpy GUI / Mirror", self.start_scrcpy, row, 0)
        btn("🔁 One-Click Fixer", self.open_one_click_fixer, row, 1)

        row += 1
        btn("📋 Clipboard PC ↔ HP", self.open_clipboard, row, 0, colspan=2)

        row += 1
        btn("🔍 Check Root Status", self.check_root_status, row, 0)
        btn("⚠️ Thermal Unlocker 🔥", self.open_thermal_unlocker, row, 1)

        row += 1
        btn("⚙️ RAM Cleaner", self.ram_cleaner, row, 0)
        btn("🧹 Cache Cleaner (Root)", self.cache_cleaner, row, 1)

        # === ⚙️ Reboot / Power Options ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10,6))
        row += 1
        ttk.Label(container, text="⚙️ Reboot / Power Options", font=("Segoe UI", 12, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("🔁 Reboot System", lambda: start_cmd([ADB, "reboot"], self.term, dry_run=self.dryrun_var.get()), row, 0)
        btn("🧰 Reboot to Recovery", lambda: start_cmd([ADB, "reboot", "recovery"], self.term, dry_run=self.dryrun_var.get()), row, 1)
        row += 1
        btn("🚀 Reboot to Bootloader", lambda: start_cmd([ADB, "reboot", "bootloader"], self.term, dry_run=self.dryrun_var.get()), row, 0)
        btn("💤 Power Off Device", lambda: start_cmd([ADB, "shell", "reboot", "-p"], self.term, dry_run=self.dryrun_var.get()), row, 1)

        # === 🧑‍💻 Dev Mode ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10,6))
        row += 1
        ttk.Label(container, text="🧑‍💻 Dev Mode", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("🧑‍💻 Developer Mode", self.open_developer_mode, row, 0, colspan=2)

    # ----- Two-column Fastboot left panel (scrollable + fixed layout) -----
    def build_left_fastboot(self):
        try:
            self.left.unbind_all("<MouseWheel>")
        except:
            pass

        # Bersihkan panel kiri
        for w in self.left.winfo_children():
            w.destroy()

        # === Scrollable container setup ===
        outer_frame = ttk.Frame(self.left)
        outer_frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(outer_frame, highlightthickness=0, bg="#e8ffe8", borderwidth=0)
        scrollbar = ttk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        # Fix celah kanan
        scrollable_frame_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        scrollable_frame.bind("<Configure>", on_frame_configure)

        def on_canvas_resize(event):
            canvas.itemconfig(scrollable_frame_id, width=event.width)
        canvas.bind("<Configure>", on_canvas_resize)

        canvas.configure(yscrollcommand=scrollbar.set)
        outer_frame.columnconfigure(0, weight=1)
        outer_frame.rowconfigure(0, weight=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Scroll pakai roda mouse
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Container isi
        container = scrollable_frame

        # Header
        ttk.Label(container, text="⚡ Fastboot Mode", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        # Helper tombol
        def btn(text, cmd, r, c, colspan=1):
            b = ttk.Button(container, text=text, command=cmd)
            b.grid(row=r, column=c, sticky="ew", padx=4, pady=3, columnspan=colspan)
            return b

        container.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(1, weight=1)

        # === 📟 Basic Fastboot Control ===
        ttk.Label(container, text="📟 Basic Fastboot Control", font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 2)
        )
        row = 2
        btn("🔍 Check Devices", self.fastboot_devices, row, 0)
        btn("📜 Getvar (All or Var)", self.fastboot_getvar_prompt, row, 1)

        row += 1
        btn("🖵 Fastboot Terminal", self.open_fastboot_terminal, row, 0, colspan=2)

        # === 🧩 Fastboot Raw Command ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="🧩 Raw Fastboot Command", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("💬 Raw Fastboot Command", self.fastboot_raw_prompt, row, 0)
        btn("⚙️ Multi Raw Fastboot Commands", lambda: MultiFastbootCommandWindow(self.root, self.term, self.dryrun_var), row, 1)

        # === 💾 Flash / Erase ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="💾 Flash / Erase", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("📤 Flash Partition (.img)", lambda: self.confirm_and_run("Flash Partition", [FASTBOOT, "flash"]), row, 0)
        btn("🧽 Erase Partition", lambda: self.confirm_and_run("Erase Partition", [FASTBOOT, "erase"]), row, 1)

        # === 🔓 Bootloader Tools ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="🔓 Bootloader Tools", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("🔓 Attempt Unlock Bootloader", lambda: self.confirm_and_run("Unlock Bootloader", [FASTBOOT, "oem", "unlock"]), row, 0)
        btn("🔒 Lock Bootloader", lambda: self.confirm_and_run("Lock Bootloader", [FASTBOOT, "oem", "lock"]), row, 1)
        row += 1
        btn("🧩 Detect Unlock Method", self.show_detect_unlock, row, 0)
        btn("📊 Get Fastboot Vars (getvar all)", self.fastboot_getvar_all_cmd, row, 1)

        # === 🧰 Firmware Tools ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1
        ttk.Label(container, text="🧰 Firmware Tools", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        self.force_active_slot = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            container,
            text="⚠️ Force Active Slot (Expert)",
            variable=self.force_active_slot
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("⚡ Auto Flash Firmware ZIP", self.open_auto_flash_zip, row, 0)
        btn("🧪 Simulate Flash (Dry Run)", self.simulate_auto_flash_zip, row, 1)
        row += 1
        btn("🧰 Boot Image Toolkit", self.open_boot_image_toolkit, row, 0)
        btn("📂 Multi Flash (Batch)", self.open_multi_flash_window, row, 1)

        # === ⚙️ Reboot / Power Options ===
        row += 1
        ttk.Separator(container, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        row += 1
        ttk.Label(container, text="⚙️ Reboot / Power Options", font=("Segoe UI", 11, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        btn("🔃 Reboot System", lambda: self.confirm_and_run("Reboot System", [FASTBOOT, "reboot"]), row, 0)
        btn("🧩 Reboot to Recovery 🛠️", lambda: self.confirm_and_run("Reboot to Recovery", [FASTBOOT, "reboot", "recovery"]), row, 1)
        row += 1
        btn("🚀 Bootloader Mode", lambda: self.confirm_and_run("Reboot to Bootloader", [FASTBOOT, "reboot", "bootloader"]), row, 0)
        btn("🌙 Power Off Device", lambda: self.confirm_and_run("Power Off Device", [FASTBOOT, "oem", "poweroff"]), row, 1)

    # ----- ensure rebuild_left exists (replace/add) -----
    def _apply_modern_style(self, theme):
        """Terapkan styling modern: font Segoe UI + palet theme ke semua widget ttk."""
        bg = theme.get("bg", DEFAULT_THEME["bg"])
        fg = theme.get("fg", DEFAULT_THEME["fg"])
        btn = theme.get("button", DEFAULT_THEME["button"])
        acc = theme.get("accent", DEFAULT_THEME["accent"])
        acc_dark = _darken(acc)
        try:
            import tkinter.font as tkfont
            for fname in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                try:
                    f = tkfont.nametofont(fname)
                    if "Segoe" not in f.cget("family"):
                        f.configure(family="Segoe UI", size=10)
                except Exception:
                    continue
        except Exception:
            pass
        try:
            s = self.style or ttk.Style()
            if self.style is None:
                try:
                    s.theme_use("clam")
                except Exception:
                    pass
            s.configure("TFrame", background=bg)
            s.configure("TLabel", background=bg, foreground=fg)
            s.configure("TButton", background=btn, foreground=fg, padding=(10, 6))
            s.configure("Accent.TButton", background=acc, foreground="#ffffff", padding=(10, 6))
            s.map("TButton",
                  background=[("active", acc), ("pressed", acc_dark)],
                  foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
            s.map("Accent.TButton",
                  background=[("active", acc_dark), ("pressed", acc_dark)],
                  foreground=[("active", "#ffffff")])
            s.configure("TEntry", fieldbackground="#ffffff", foreground=fg)
            s.configure("TCombobox", fieldbackground="#ffffff", foreground=fg)
            s.configure("TNotebook", background=bg)
            s.configure("TNotebook.Tab", background=btn, foreground=fg, padding=(12, 6))
            s.map("TNotebook.Tab",
                  background=[("selected", acc)],
                  foreground=[("selected", "#ffffff")])
            s.configure("Treeview", background="#ffffff", foreground=fg,
                        fieldbackground="#ffffff", rowheight=26)
            s.configure("Treeview.Heading", background=btn, foreground=fg)
            s.configure("TProgressbar", background=acc)
            s.configure("TLabelframe", background=bg, foreground=fg)
            s.configure("TLabelframe.Label", background=bg, foreground=fg)
            # label RGB animasi ikut warna background theme
            try:
                if getattr(self, "rgb_label", None) is not None:
                    self.rgb_label.configure(background=bg)
            except Exception:
                pass
        except Exception:
            pass

    def rebuild_left(self):
        """Rebuild panel kiri sesuai mode (ADB atau Fastboot)"""
        # simply call the correct builder; builder will clear self.left itself
        if self.mode_var.get() == "ADB":
            self.build_left_adb()
        else:
            self.build_left_fastboot()

    # ---------- terminal helpers ----------
    def clear_term(self):
        self.term.configure(state="normal")
        self.term.delete("1.0", tk.END)
        self.term.configure(state="disabled")

    # ---------- 🧾 Save Log ----------
    def save_log(self):
        content = self.term.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("😶 Info", "No output to save, bro. Nothing to log 😅")
            return
        save_text_to_file(content, initial=f"fadb_log_{timestamp()}.txt")
        messagebox.showinfo("✅ Saved!", "Log file successfully saved 💾")

    # ---------- 🧹 Clear Logs Folder ----------
    def clear_logs_folder(self):
        """🗑️ Delete all files in the logs folder"""
        try:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            if not os.path.exists(log_dir):
                messagebox.showinfo("📁 Info", "Logs folder not found 🤷‍♂️")
                return

            files = [f for f in os.listdir(log_dir) if os.path.isfile(os.path.join(log_dir, f))]
            if not files:
                messagebox.showinfo("✨ Info", "No log files to delete 😌")
                return

            confirm = messagebox.askyesno(
                "⚠️ Confirm",
                f"Are you sure you want to delete {len(files)} log file(s) in:\n{log_dir}\nContinue? 🧨"
            )
            if not confirm:
                return

            deleted = 0
            for f in files:
                try:
                    os.remove(os.path.join(log_dir, f))
                    deleted += 1
                except Exception as e:
                    self.logger.error(f"❌ Failed to delete {f}: {e}")

            # Log + terminal output
            self.logger.success(f"🧹 {deleted} log file(s) successfully deleted from logs/")
            self.term.configure(state="normal")
            self.term.insert(tk.END, f"\n[🧾 LOGS] {deleted} log file(s) deleted from logs/ ✅\n")
            self.term.configure(state="disabled")
            self.term.see(tk.END)

            messagebox.showinfo("🧹 Success", f"{deleted} log file(s) successfully deleted 🗂️")
        except Exception as e:
            self.logger.error(f"💥 Error while clearing logs: {e}")
            self.term.configure(state="normal")
            self.term.insert(tk.END, f"\n[⚠️ ERROR] Failed to clear logs: {e}\n")
            self.term.configure(state="disabled")
            self.term.see(tk.END)
            messagebox.showerror("💥 Error", f"Failed to clear logs: {e}")

    # ---------- 🛑 Stop Process ----------
    def stop_proc(self):
        ok = stop_all_current()
        if ok:
            messagebox.showinfo("🛑 Stopped", "Running process(es) terminated")
        else:
            messagebox.showinfo("ℹ️ Info", "No running process found")

    # ---------- 📡 Poll Output ----------
    def poll_output(self):
        try:
            while True:
                line = output_q.get_nowait()
                append_term(self.term, line)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_output)

    # ---------- Device Manager ----------
    def poll_device_state(self):
        """Cek status perangkat secara periodik (1.5 detik)"""
        mode, detail = detect_device_state()

        # Hindari update berulang jika status sama
        if getattr(self, "_last_device_state", None) != mode:
            self._last_device_state = mode

            if mode == "adb":
                self.status.config(
                    text=f"🟢 Connected via ADB — {detail}",
                    foreground="green"
                )
            elif mode == "fastboot":
                self.status.config(
                    text=f"🟡 Connected via Fastboot — {detail}",
                    foreground="orange"
                )
            else:
                self.status.config(
                    text="🔴 No device detected",
                    foreground="red"
                )

            # Log perubahan status ke terminal
            self.logger.info(f"Device state: {mode.upper() if mode != 'none' else 'NONE'}")

        # Jalankan ulang setiap 1500 ms
        self.root.after(1500, self.poll_device_state)

    def manual_refresh(self):
        mode, info = detect_device_state()
        self.status.config(text=(f"{mode}: {info}"))

    # ---------- Smart Error Popup ----------
    def show_smart_error(self, title, message):
        """Popup error with automatic suggestion based on error content."""
        suggestion = ""

        msg_lower = message.lower()

        if "device not found" in msg_lower:
            suggestion = "💡 Make sure the device is connected & USB debugging is enabled."
        elif "unauthorized" in msg_lower:
            suggestion = "💡 Allow USB Debugging authorization on the device screen."
        elif "no permissions" in msg_lower:
            suggestion = "💡 Run the application as Administrator."
        elif "adb not found" in msg_lower:
            suggestion = "💡 Ensure ADB is installed and added to the PATH environment."
        elif "fastboot" in msg_lower and "not found" in msg_lower:
            suggestion = "💡 Make sure fastboot.exe is in the tools folder or in PATH."
        elif "syntax" in msg_lower or "invalid" in msg_lower:
            suggestion = "💡 Check your command, there might be a typo."
        elif "timeout" in msg_lower:
            suggestion = "💡 Try reconnecting the device."
        else:
            suggestion = "💡 Check the connection and ensure the device is in the correct mode."

        full_msg = f"{message}\n\n{suggestion}"
        messagebox.showerror(title, full_msg)
        self.logger.error(message)

    # ---------- ADB actions ----------
    def cmd_devices(self):
        try:
            start_cmd([ADB, "devices"], self.term, dry_run=self.dryrun_var.get())
            self.logger.success("✅ ADB device list retrieved successfully.")
        except Exception as e:
            self.logger.error(f"❌ Failed to list devices: {e}")

    # ---------- Universal Confirm Dialog ----------
    def confirm_action(self, action_name="this operation"):
        """Show confirmation popup before running critical commands."""
        result = messagebox.askyesno(
            "Confirm Operation",
            f"⚠️ Are you sure you want to proceed with:\n\n{action_name}?\n\nThis action may affect your device."
        )
        if result:
            self.logger.info(f"User confirmed action: {action_name}")
        else:
            self.logger.info(f"User canceled action: {action_name}")
        return result

    def confirm_and_run(self, action_name, cmd_list):
        """Helper untuk jalankan command dengan konfirmasi dulu."""
        if self.confirm_action(action_name):
            start_cmd(cmd_list, self.term, dry_run=self.dryrun_var.get())

    # ---------- Settings (path adb / fastboot / scrcpy) ----------
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("⚙️ Settings — Path Tools")
        win.geometry("640x560")
        win.resizable(False, False)

        tools = [
            ("adb", "ADB", "version"),
            ("fastboot", "Fastboot", "--version"),
            ("scrcpy", "Scrcpy", "--version"),
        ]
        vars_ = {}
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        def resolved_of(key, mode, manual):
            manual = (manual or "").strip()
            if mode == "manual" and manual and os.path.isfile(manual):
                return manual
            return shutil.which(key) or key

        def refresh_info(key):
            mode_v, path_v, info_v, _ = vars_[key]
            info_v.set("Efektif: " + resolved_of(key, mode_v.get(), path_v.get()))

        def browse(path_v):
            fn = filedialog.askopenfilename(title="Pilih binary",
                                            filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
            if fn:
                path_v.set(fn)

        def test_tool(key):
            mode_v, path_v, info_v, verflag = vars_[key]
            exe = resolved_of(key, mode_v.get(), path_v.get())
            try:
                r = subprocess.run([exe, verflag], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, timeout=8)
                first = (r.stdout or "").strip().splitlines()
                info_v.set("Efektif: " + exe + "  |  " + (first[0] if first else f"exit {r.returncode}"))
            except Exception as e:
                info_v.set(f"Gagal jalanin {exe}: {e}")

        for key, label, verflag in tools:
            saved = (current_theme.get(key + "_path") or "").strip()
            mode_v = tk.StringVar(value="manual" if saved else "auto")
            path_v = tk.StringVar(value=saved)
            info_v = tk.StringVar()
            box = ttk.LabelFrame(frm, text=f"🔧 {label}", padding=8)
            box.pack(fill=tk.X, pady=6)
            ttk.Radiobutton(box, text="Otomatis (dari PATH)", variable=mode_v,
                            value="auto", command=lambda k=key: refresh_info(k)).pack(anchor="w")
            row = ttk.Frame(box)
            row.pack(fill=tk.X, pady=4)
            ttk.Radiobutton(row, text="Manual:", variable=mode_v,
                            value="manual", command=lambda k=key: refresh_info(k)).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=path_v, width=40).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
            ttk.Button(row, text="📂 Browse", command=lambda v=path_v: browse(v)).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Button(row, text="🧪 Test", command=lambda k=key: test_tool(k)).pack(side=tk.LEFT)
            ttk.Label(box, textvariable=info_v, foreground="#0077a3").pack(anchor="w")
            path_v.trace_add("write", lambda *a, k=key: refresh_info(k))
            vars_[key] = (mode_v, path_v, info_v, verflag)

        def save_all():
            for key, (mode_v, path_v, _iv, _vf) in vars_.items():
                if mode_v.get() == "manual" and path_v.get().strip():
                    current_theme[key + "_path"] = path_v.get().strip()
                else:
                    current_theme.pop(key + "_path", None)
            save_config(CONFIG_PATH, current_theme)
            apply_tool_paths(current_theme)
            for k in vars_:
                refresh_info(k)
            messagebox.showinfo("Settings", "Path tools tersimpan dan langsung dipakai.")

        btnf = ttk.Frame(frm)
        btnf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btnf, text="💾 Save", command=save_all).pack(side=tk.LEFT)
        ttk.Button(btnf, text="Close", command=win.destroy).pack(side=tk.RIGHT)

        for k in vars_:
            refresh_info(k)

    # ---------- Start Shizuku ----------
    def start_shizuku(self):
        if not is_bin_available(ADB):
            messagebox.showerror("adb missing", "adb not found in PATH.")
            return
        cmd = [ADB, "shell", "sh", "/storage/emulated/0/Android/data/moe.shizuku.privileged.api/start.sh"]
        start_cmd(cmd, self.term, dry_run=self.dryrun_var.get())

    # --------- Clipboard PC <-> HP ----------
    def open_clipboard(self):
        ClipboardWindow(self.root)

    # --------- Scrcpy GUI ----------
    def start_scrcpy(self):
        if not is_bin_available(SCRCPY):
            messagebox.showerror("Scrcpy not found", "Install scrcpy terlebih dahulu dan pastikan ada di PATH.")
            return
        ScrcpyGuiWindow(self.root, self.term, self.dryrun_var)

    # ---------- ADB Shell Prompt ----------
    def adb_shell_prompt(self):
        if not is_bin_available(ADB):
            messagebox.showerror("adb missing", "adb not found.")
            self.logger.error("adb not found while trying to open shell prompt.")
            return
        cmd = simpledialog.askstring("ADB Shell", "Enter shell command (e.g. pm list packages):")
        if cmd:
            try:
                # seluruh perintah dikirim utuh sebagai 1 remote command
                # agar su -c "...", quotes, dan pipe jalan device-side
                start_cmd([ADB, "shell", cmd.strip()], self.term, dry_run=self.dryrun_var.get())
                self.logger.success(f"Executed ADB shell command: {cmd}")
            except Exception as e:
                self.logger.error(f"Error executing ADB shell command '{cmd}': {e}")

    # ---------- ADB Terminal ----------
    def open_adb_terminal(self):
        """Open Mini ADB Terminal (auto 'adb' prefix + command history + clear button)"""
        win = tk.Toplevel(self.root)
        win.title("🖵 ADB Terminal — Simplified Mode")
        win.geometry("720x420")

        # --- Center window ---
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        win.geometry(f"+{x}+{y}")

        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="🖵 ADB Mini Terminal — type commands (no need to prefix with 'adb'):",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0,4))

        text_area = tk.Text(frame, height=18, wrap="word", font=("Consolas", 10))
        text_area.pack(fill="both", expand=True, pady=4)
        text_area.insert(
            tk.END,
            "[ADB Mini Terminal Started]\n\n"
        )
        text_area.configure(state="disabled")

        input_frame = ttk.Frame(frame)
        input_frame.pack(fill="x", pady=(4,0))

        cmd_var = tk.StringVar()
        entry = ttk.Entry(input_frame, textvariable=cmd_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0,4))
        entry.focus()

        # --- Buttons ---
        ttk.Button(input_frame, text="🧹 Clear", command=lambda: clear_output()).pack(side="left", padx=(0,4))
        ttk.Button(input_frame, text="Run", command=lambda: run_command()).pack(side="right", padx=(4,0))

        # --- Command history ---
        history = []
        history_index = -1

        def append_output(text):
            text_area.configure(state="normal")
            text_area.insert(tk.END, text + "\n")
            text_area.see(tk.END)
            text_area.configure(state="disabled")

        def clear_output():
            text_area.configure(state="normal")
            text_area.delete("1.0", tk.END)
            text_area.insert(tk.END, "[ADB Mini Terminal Cleared]\n\n")
            text_area.configure(state="disabled")

        def run_command(event=None):
            nonlocal history_index
            cmdline = cmd_var.get().strip()
            if not cmdline:
                return

            # add to history
            history.append(cmdline)
            history_index = len(history)

            cmd_var.set("")
            append_output(f"$ {cmdline}")

            parts = build_adb_command(cmdline)
            if not parts:
                append_output("[Error] Invalid command syntax.")
                return

            try:
                proc = subprocess.Popen(
                    parts,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True
                )
                for line in proc.stdout:
                    append_output(line.rstrip())
                proc.wait()
                append_output(f"[Exited with code {proc.returncode}]\n")
            except FileNotFoundError:
                append_output("[Error] adb not found in PATH.")
            except Exception as e:
                append_output(f"[Error] {e}")

        def navigate_history(event):
            nonlocal history_index
            if not history:
                return
            if event.keysym == "Up":
                history_index = max(0, history_index - 1)
            elif event.keysym == "Down":
                history_index = min(len(history), history_index + 1)
            if history_index < len(history):
                cmd_var.set(history[history_index])
                entry.icursor(tk.END)
            else:
                cmd_var.set("")

        entry.bind("<Return>", run_command)
        entry.bind("<Up>", navigate_history)
        entry.bind("<Down>", navigate_history)

    # ---------- ADB Install APK ----------
    def adb_install_apk(self):
        if not is_bin_available(ADB):
            messagebox.showerror("adb missing", "adb not found.")
            self.logger.error("adb not found while trying to install APK.")
            return
        apk = filedialog.askopenfilename(title="Select APK to install", filetypes=[("APK", "*.apk")])
        if apk:
            try:
                start_cmd([ADB, "install", "-r", apk], self.term, dry_run=self.dryrun_var.get())
                self.logger.success(f"APK installed successfully: {os.path.basename(apk)}")
            except Exception as e:
                self.logger.error(f"Failed to install APK '{apk}': {e}")

    # ---------- ADB Push / Pull ----------
    def adb_push_file(self):
        if not is_bin_available(ADB):
            messagebox.showerror("adb missing", "adb not found.")
            self.logger.error("adb not found while trying to push file.")
            return
        src = filedialog.askopenfilename(title="Select file to push to device")
        if not src:
            return
        dest = simpledialog.askstring("Push to device", "Enter destination path on device (e.g. /sdcard/):", initialvalue="/sdcard/")
        if not dest:
            return
        try:
            start_cmd([ADB, "push", src, dest], self.term, dry_run=self.dryrun_var.get())
            self.logger.success(f"File pushed to device: {os.path.basename(src)} → {dest}")
        except Exception as e:
            self.logger.error(f"Failed to push file '{src}' to '{dest}': {e}")

    def adb_pull_file(self):
        if not is_bin_available(ADB):
            messagebox.showerror("adb missing", "adb not found.")
            self.logger.error("adb not found while trying to pull file.")
            return
        src = simpledialog.askstring("Pull from device", "Enter source path on device (e.g. /sdcard/DCIM/Camera):")
        if not src:
            return
        dest = filedialog.askdirectory(title="Select destination folder on PC")
        if not dest:
            return
        try:
            start_cmd([ADB, "pull", src, dest], self.term, dry_run=self.dryrun_var.get())
            self.logger.success(f"File pulled from device: {src} → {dest}")
        except Exception as e:
            self.logger.error(f"Failed to pull file '{src}' from device: {e}")

    # ---------- Open Log Folder ----------
    def open_log_folder(self):
        """Open the folder where logs are stored (next to main script)."""
        import os
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(base_dir, "logs")

        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            self.logger.info("Created logs directory.")

        try:
            os.startfile(log_dir)  # Windows
            self.logger.info("Opened log folder.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open log folder:\n{e}")
            self.logger.error(f"Failed to open log folder: {e}")

    # ---------- Multi Push / Multi Pull ----------
    def open_multi_push_window(self):
        MultiPushWindow(self.root, self.term, self.dryrun_var)

    def open_multi_pull_window(self):
        MultiPullWindow(self.root, self.term, self.dryrun_var)

    # ---------- 📂 ADB File Explorer ----------
    def open_file_explorer(self):
        """Chooser: open ADB File Explorer with Non-Root or Root mode"""
        win = tk.Toplevel(self.root)
        win.title("📂 Choose Explorer Mode")
        win.geometry("260x190")
        win.resizable(True, True)

        # 🧭 Center window di tengah layar
        win.update_idletasks()
        width, height = 260, 190
        x = (win.winfo_screenwidth() // 2) - (width // 2)
        y = (win.winfo_screenheight() // 2) - (height // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

        # === UI ===
        ttk.Label(
            win,
            text="Select File Explorer Mode:",
            font=("Segoe UI", 10, "bold")
        ).pack(pady=(15, 8))

        ttk.Button(
            win,
            text="📁 Non-Root Mode",
            width=25,
            command=lambda: [
                win.destroy(),
                ADBFileExplorer(self.root, self.term, self.dryrun_var, root_mode=False)
            ]
        ).pack(pady=4)

        ttk.Button(
            win,
            text="🧩 Root Mode",
            width=25,
            command=lambda: self.confirm_root_mode(win)
        ).pack(pady=4)

        ttk.Separator(win, orient="horizontal").pack(fill="x", pady=8)

        ttk.Button(
            win,
            text="❌ Cancel",
            width=25,
            command=win.destroy
        ).pack(pady=(2, 6))


    # ---------- 🧩 Confirm Root Mode ----------
    def confirm_root_mode(self, win):
        """Ask user confirmation before opening Root Mode"""
        confirm = messagebox.askyesno(
            "⚠️ Root Mode Confirmation",
            "Root Mode grants full system access.\n"
            "Modifying or deleting critical files may cause boot issues.\n\n"
            "Proceed with Root Mode?"
        )
        if confirm:
            win.destroy()
            ADBFileExplorer(self.root, self.term, self.dryrun_var, root_mode=True)
        else:
            self.term.insert(tk.END, "\n[FileExplorer] Root Mode canceled by user.\n")

    # ---------- Check Root Status ----------
    def check_root_status(self):
        RootCheckerWindow(self.root, self.term, self.dryrun_var)

    # ---------- Device Info Window ----------
    def open_device_info_window(self):
        DeviceInfoWindow(self.root, self.term, self.dryrun_var)

    # ---------- ADB Raw Command ----------
    def adb_raw_prompt(self):
        if not is_bin_available(ADB):
            messagebox.showerror("adb missing", "adb not found.")
            return
        args = simpledialog.askstring("Raw adb", "Enter adb args (without 'adb'):")
        if args:
            argv = split_cmdline(args)
            if argv is None:
                messagebox.showerror("Raw adb", "Sintaks quotes tidak valid.")
                return
            start_cmd([ADB] + argv, self.term, dry_run=self.dryrun_var.get())

    # ---------- helper to run adb commands ----------
    def _run_adb_cmd(self, cmd_list, timeout=10):
        """Run adb command (list) and return stdout (string). Handles errors and logs."""
        try:
            out = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
            if out.returncode != 0:
                self.logger.error(f"ADB cmd failed: {' '.join(cmd_list)} -> {out.stderr.strip()}")
                return out.stdout + ("\n\n[ERROR]\n" + out.stderr if out.stderr else "")
            return out.stdout or ""
        except Exception as e:
            self.logger.error(f"Exception running adb cmd {' '.join(cmd_list)}: {e}")
            return f"Error: {e}"

    # ---------- Smart Device Analyzer (4-in-1, manual fetch) ----------
    def open_smart_device_analyzer(self):
        if not is_bin_available(ADB):
            messagebox.showerror("ADB Missing", "adb not found in PATH.")
            try: self.logger.error("Smart Analyzer failed: ADB not found.")
            except: pass
            return

        win = tk.Toplevel(self.root)
        win.title("📊 Smart Device Analyzer")
        win.geometry("700x600")

        # Notebook
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        # common create tab helper
        def make_tab(title):
            f = ttk.Frame(nb)
            text = tk.Text(f, wrap="word", font=("Consolas", 10))
            text.pack(fill="both", expand=True, padx=6, pady=(6, 40))  # leave space for buttons
            # bottom frame for buttons
            btnf = ttk.Frame(f)
            btnf.place(relx=0.0, rely=1.0, anchor="sw", relwidth=1.0, height=36)
            return f, text, btnf

        # --- Tab 1: Battery & Thermal ---
        tab_bat, txt_bat, btnf_bat = make_tab("Battery & Thermal")
        nb.add(tab_bat, text="🔋 Battery & Thermal")

        def fetch_battery_thermal():
            txt_bat.config(state="normal")
            txt_bat.delete("1.0", tk.END)
            txt_bat.insert(tk.END, "=== Fetching Battery ===\n")
            bat = self._run_adb_cmd([ADB, "shell", "dumpsys", "battery"])
            txt_bat.insert(tk.END, bat + "\n\n")
            txt_bat.insert(tk.END, "=== Fetching Thermal (tz*) ===\n")
            therm = self._run_adb_cmd([ADB, "shell", "cat", "/sys/class/thermal/thermal_zone*/temp"])
            txt_bat.insert(tk.END, therm.strip() + "\n")
            txt_bat.config(state="disabled")
            self.logger.info("Battery & Thermal fetched.")

        ttk.Button(btnf_bat, text="Fetch", command=fetch_battery_thermal).pack(side="left", padx=6, pady=4)
        ttk.Button(btnf_bat, text="Save Report", command=lambda: self._save_text_contents(txt_bat, "battery_thermal")).pack(side="left", padx=6, pady=4)

        # --- Tab 2: Memory & Storage ---
        tab_mem, txt_mem, btnf_mem = make_tab("Memory & Storage")
        nb.add(tab_mem, text="💾 Memory & Storage")

        def fetch_memory_storage():
            txt_mem.config(state="normal")
            txt_mem.delete("1.0", tk.END)
            txt_mem.insert(tk.END, "=== /proc/meminfo ===\n")
            mem = self._run_adb_cmd([ADB, "shell", "cat", "/proc/meminfo"])
            txt_mem.insert(tk.END, mem + "\n\n")
            txt_mem.insert(tk.END, "=== df -h ===\n")
            df = self._run_adb_cmd([ADB, "shell", "df", "-h"])
            txt_mem.insert(tk.END, df + "\n")
            txt_mem.config(state="disabled")
            self.logger.info("Memory & Storage fetched.")

        ttk.Button(btnf_mem, text="Fetch", command=fetch_memory_storage).pack(side="left", padx=6, pady=4)
        ttk.Button(btnf_mem, text="Save Report", command=lambda: self._save_text_contents(txt_mem, "memory_storage")).pack(side="left", padx=6, pady=4)

        # --- Tab 3: Connection & Charging ---
        tab_conn, txt_conn, btnf_conn = make_tab("Connection & Charging")
        nb.add(tab_conn, text="⚡ Connection & Charging")

        def fetch_connection_charging():
            txt_conn.config(state="normal")
            txt_conn.delete("1.0", tk.END)
            # device state
            mode, info = detect_device_state()
            txt_conn.insert(tk.END, f"Device state: {mode} — {info}\n\n")
            # ip addresses
            txt_conn.insert(tk.END, "=== ip addr (wlan0) ===\n")
            ip = self._run_adb_cmd([ADB, "shell", "ip", "addr", "show", "wlan0"])
            txt_conn.insert(tk.END, ip + "\n\n")
            # usb state & getprop usb config
            txt_conn.insert(tk.END, "=== USB & ADB ===\n")
            usb = self._run_adb_cmd([ADB, "shell", "getprop", "sys.usb.state"])
            txt_conn.insert(tk.END, f"sys.usb.state: {usb}\n")
            # battery summary (level & status)
            bat = self._run_adb_cmd([ADB, "shell", "dumpsys", "battery"])
            level = self.extract_value(bat, "level")
            status = self.extract_value(bat, "status")
            txt_conn.insert(tk.END, f"\nBattery level: {level}%\nBattery status: {status}\n")
            txt_conn.config(state="disabled")
            self.logger.info("Connection & Charging fetched.")

        ttk.Button(btnf_conn, text="Fetch", command=fetch_connection_charging).pack(side="left", padx=6, pady=4)
        ttk.Button(btnf_conn, text="Save Report", command=lambda: self._save_text_contents(txt_conn, "connection_charging")).pack(side="left", padx=6, pady=4)

        # --- Tab 4: Full Device Report ---
        tab_full, txt_full, btnf_full = make_tab("Full Device Report")
        nb.add(tab_full, text="📱 Full Device Report")

        def analyze_full():
            txt_full.config(state="normal")
            txt_full.delete("1.0", tk.END)
            txt_full.insert(tk.END, "=== Collecting getprop ===\n")
            props = self._run_adb_cmd([ADB, "shell", "getprop"])
            txt_full.insert(tk.END, props + "\n\n")
            txt_full.insert(tk.END, "=== dumpsys battery ===\n")
            bat = self._run_adb_cmd([ADB, "shell", "dumpsys", "battery"])
            txt_full.insert(tk.END, bat + "\n\n")
            txt_full.insert(tk.END, "=== /proc/meminfo ===\n")
            mem = self._run_adb_cmd([ADB, "shell", "cat", "/proc/meminfo"])
            txt_full.insert(tk.END, mem + "\n\n")
            txt_full.insert(tk.END, "=== df -h ===\n")
            df = self._run_adb_cmd([ADB, "shell", "df", "-h"])
            txt_full.insert(tk.END, df + "\n\n")
            txt_full.insert(tk.END, "=== top (first lines) ===\n")
            top = self._run_adb_cmd([ADB, "shell", "sh", "-c", "top -n 1 | sed -n '1,20p'"], timeout=15)
            txt_full.insert(tk.END, top + "\n")
            txt_full.config(state="disabled")
            self.logger.success("Full Device Report generated.")

        ttk.Button(btnf_full, text="Analyze", command=analyze_full).pack(side="left", padx=6, pady=4)
        ttk.Button(btnf_full, text="Save Report", command=lambda: self._save_text_contents(txt_full, "full_report")).pack(side="left", padx=6, pady=4)

        # bottom global controls
        bottom = ttk.Frame(win)
        bottom.pack(fill="x", padx=8, pady=6)
        ttk.Button(bottom, text="Save Active Tab", command=lambda: self._save_text_contents(nb.nametowidget(nb.select()), None)).pack(side="left", padx=6)
        ttk.Button(bottom, text="Close", command=win.destroy).pack(side="right", padx=6)

    # ---------- helper to save text widget contents ----------
    def _save_text_contents(self, widget_or_textwidget, hint="report"):
        """
        widget_or_textwidget: tk.Text OR a container frame (in case called from bottom button).
        If container frame is passed, try to find a Text child inside.
        """
        txtw = None
        if isinstance(widget_or_textwidget, tk.Text):
            txtw = widget_or_textwidget
        else:
            # try to find Text inside given container
            try:
                for child in widget_or_textwidget.winfo_children():
                    if isinstance(child, tk.Text):
                        txtw = child
                        break
            except Exception:
                pass

        if not txtw:
            messagebox.showinfo("Save", "No text to save in this tab.")
            return

        content = txtw.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("Save", "Tab is empty.")
            return

        initname = f"{hint}_{timestamp()}.txt" if hint else f"report_{timestamp()}.txt"
        path = filedialog.asksaveasfilename(title="Save Report", initialfile=initname, defaultextension=".txt",
                                            filetypes=[("Text files", "*.txt")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                messagebox.showinfo("Saved", f"Report saved to:\n{path}")
                try: self.logger.info(f"Saved report: {path}")
                except: pass
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save: {e}")
                try: self.logger.error(f"Failed saving report {path}: {e}")
                except: pass

    # ---------- ⚙️ Tools: RAM Cleaner & Cache Cleaner ----------
    def ram_cleaner(self):
        """Kill all background apps (non-root safe)"""
        if not messagebox.askyesno(
            "⚠️ Confirmation",
            "This will attempt to kill background apps.\nContinue?"
        ):
            return

        if not messagebox.askyesno(
            "⚠️ Second Confirmation",
            "Are you absolutely sure?\nKilling system processes can cause instability!"
        ):
            return

        # 🧩 Dry-run safety
        if hasattr(self, 'dryrun_var') and self.dryrun_var.get():
            messagebox.showinfo("Dry-Run", "Dry-run mode is active. Skipping execution.")
            return

        self.term.insert(tk.END, "\n[RAM Cleaner] Attempting to kill background apps...\n")
        cmd = [ADB, "shell", "am", "kill-all"]
        start_cmd(cmd, self.term)
        self.term.insert(tk.END, "[RAM Cleaner] ✅ Finished killing background apps.\n")


    def cache_cleaner(self):
        """Clear all app caches (requires root)"""
        if not messagebox.askyesno(
            "⚠️ Root Required",
            "This will clear all app caches (root required).\nContinue?"
        ):
            return

        if not messagebox.askyesno(
            "⚠️ Final Warning",
            "This action may cause temporary lag or app reloads.\nProceed anyway?"
        ):
            return

        # 🧩 Dry-run safety
        if hasattr(self, 'dryrun_var') and self.dryrun_var.get():
            messagebox.showinfo("Dry-Run", "Dry-run mode is active. Skipping execution.")
            return

        self.term.insert(tk.END, "\n[Cache Cleaner] Attempting to clear caches (root)...\n")
        cmd = [
            ADB, "shell", "su", "-c",
            "rm -rf /data/data/*/cache/* && rm -rf /data/cache/* && rm -rf /cache/*"
        ]
        start_cmd(cmd, self.term)
        self.term.insert(tk.END, "[Cache Cleaner] ✅ Cache cleared successfully (root required).\n")

    # --------- One-Click Fixer ----------
    def open_one_click_fixer(self):
        win = tk.Toplevel(self.root)
        win.title("One-Click Fixer — Adaptive Live Monitor Edition by Faa Ramadhan")
        win.geometry("520x620")
        win.minsize(480, 580)

        # === Layout utama ===
        top_frame = ttk.Frame(win)
        top_frame.pack(fill="both", expand=True)
        bottom_frame = ttk.Frame(win)
        bottom_frame.pack(fill="x", side="bottom")

        # === Scrollable area (atas) ===
        canvas = tk.Canvas(top_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(top_frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        scroll_window = canvas.create_window((0, 0), window=scrollable, anchor="nw")

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # === Resizing agar proporsional (adaptive width)
        def _resize_canvas(event):
            canvas.itemconfig(scroll_window, width=event.width - 10)
        canvas.bind("<Configure>", _resize_canvas)

        # === Scroll pakai mouse ===
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # === Detect brand ===
        try:
            brand_out = subprocess.run([ADB, "shell", "getprop", "ro.product.brand"], capture_output=True, text=True)
            brand = brand_out.stdout.strip().lower()
        except Exception:
            brand = "unknown"

        ttk.Label(scrollable, text=f"📱 Detected Brand: {brand.capitalize() or 'Unknown'}",
                  font=("Segoe UI", 9, "italic")).pack(pady=(6,4))
        ttk.Label(scrollable, text="🧰 Choose what to fix:", font=("Segoe UI", 10, "bold")).pack(pady=(2,2))

        # === LOG + Progress di bawah ===
        logbox = tk.Text(bottom_frame, height=9, bg="#f8fff6", fg="#003300", font=("Consolas", 9))
        logbox.pack(fill="both", expand=True, padx=8, pady=(0,6))
        progress = ttk.Progressbar(bottom_frame, mode="indeterminate")
        progress.pack(fill="x", padx=8, pady=(0,4))

        def log(msg, color=None):
            logbox.insert(tk.END, msg + "\n")
            if color:
                logbox.tag_add(color, "end-2l linestart", "end-1l lineend")
                logbox.tag_config(color, foreground=color)
            logbox.see(tk.END)

        # Fixer yg punya efek samping destruktif -> wajib konfirmasi + warning
        RISKY = {
            "🧧 Fix MIUI Services / Themes":
                "pm clear Theme Manager bisa RESET TEMA & WALLPAPER!\n"
                "Jangan dipakai di custom ROM non-MIUI.",
        }

        def run_fix(cmds, msg="Done!", warn=None):
            if warn:
                if not messagebox.askyesno("⚠️ Efek Samping", f"{warn}\n\nLanjut?"):
                    log("Dibatalkan.", "orange")
                    return
            progress.start(15)
            log(f"\n▶ Running {len(cmds)} commands...\n", "blue")
            win.update_idletasks()
            try:
                for cmd in cmds:
                    log(f"$ {' '.join(cmd)}", "#666")
                    proc = subprocess.run(cmd, capture_output=True, text=True)
                    if proc.stdout.strip():
                        log(proc.stdout.strip(), "green")
                    if proc.stderr.strip():
                        log(proc.stderr.strip(), "red")
                log(f"✅ {msg}", "green")
            except Exception as e:
                log(f"❌ Error: {e}", "red")
            finally:
                progress.stop()
                log("—" * 50)

        # === Fixers umum (Fix WiFi/Network punya fungsi + tombol sendiri) ===
        fixers = {
            "🧹 Fix Play Store": [
                [ADB, "shell", "pm", "clear", "com.android.vending"],
                [ADB, "shell", "am", "force-stop", "com.android.vending"]
            ],
            "💬 Fix Google Services (aman, tanpa logout)": [
                [ADB, "shell", "pm", "trim-caches", "1073741824"],
                [ADB, "shell", "am", "force-stop", "com.google.android.gms"]
            ],
            "📸 Fix Camera": [
                [ADB, "shell", "pm", "clear", "com.android.camera"],
                [ADB, "shell", "am", "force-stop", "com.android.camera"]
            ],
            "🔊 Fix Audio / Media (root)": [
                [ADB, "shell", "su", "-c", "killall audioserver"],
                [ADB, "shell", "su", "-c", "killall mediaserver"]
            ],
            "🪫 Fix Battery Stats": [
                [ADB, "shell", "dumpsys", "batterystats", "--reset"]
            ],
            "🧭 Fix GPS / Location": [
                [ADB, "shell", "settings", "put", "secure", "location_providers_allowed", "gps,network"]
            ],
            "📱 Fix UI Lag (aman, wallpaper utuh)": "uifix",
        }

        brand_fixers = {
            "xiaomi": {
                "🧧 Fix MIUI Services / Themes": [
                    [ADB, "shell", "pm", "clear", "com.miui.securitycenter"],
                    [ADB, "shell", "pm", "clear", "com.android.thememanager"]
                ]
            },
            "samsung": {
                "📲 Fix OneUI Home / System (root)": [
                    [ADB, "shell", "pm", "clear", "com.sec.android.app.launcher"],
                    [ADB, "shell", "su", "-c", "pkill -f com.android.systemui"]
                ]
            },
            "realme": {
                "🎨 Fix ColorOS Launcher": [
                    [ADB, "shell", "pm", "clear", "com.oppo.launcher"]
                ]
            },
            "oppo": {
                "🎨 Fix ColorOS Launcher": [
                    [ADB, "shell", "pm", "clear", "com.oppo.launcher"]
                ]
            },
            "vivo": {
                "🌈 Fix Funtouch Launcher": [
                    [ADB, "shell", "pm", "clear", "com.bbk.launcher2"]
                ]
            },
        }

        def run_wifi_fix():
            """Fix WiFi + network bertahap: cek state -> airplane cycle ->
            wifi off/on -> data off/on -> restart wpa_supplicant (root, opsional)
            -> verifikasi. Tanpa root tetap jalan (langkah root best-effort)."""
            progress.start(15)
            log("\n▶ Fix WiFi / Network dimulai...\n", "blue")
            win.update_idletasks()

            def step(cmd, note=""):
                if note:
                    log(f"• {note}", "#666")
                log(f"$ {' '.join(cmd)}", "#666")
                win.update_idletasks()
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                except Exception as e:
                    log(f"❌ Error: {e}", "red")
                    return ""
                out = (proc.stdout.strip() + "\n" + proc.stderr.strip()).strip()
                if out:
                    log(out, "green" if proc.returncode == 0 else "red")
                return out

            def pause(s, msg):
                log(f"⏳ {msg} ({s} dtk)...")
                win.update_idletasks()
                try:
                    time.sleep(s)
                except Exception:
                    pass

            try:
                # 1. state awal
                wifi_on = step([ADB, "shell", "settings", "get", "global", "wifi_on"], "State WiFi awal")
                step([ADB, "shell", "settings", "get", "global", "airplane_mode_on"], "State airplane awal")
                # 2. airplane cycle via cmd connectivity (tanpa root,
                #    am broadcast AIRPLANE_MODE ditolak sistem sejak Android 12)
                step([ADB, "shell", "cmd", "connectivity", "airplane-mode", "enable"], "Airplane ON")
                pause(3, "tunggu radio mati")
                step([ADB, "shell", "cmd", "connectivity", "airplane-mode", "disable"], "Airplane OFF")
                step([ADB, "shell", "cmd", "connectivity", "airplane-mode"], "Cek airplane")
                pause(2, "tunggu radio nyala")
                # 3. restart wpa_supplicant DULU (butuh root, gagal = skip),
                #    lalu wifi off/on agar stack nyambung lagi dengan bersih
                step([ADB, "shell", "su", "-c", "pkill wpa_supplicant"],
                     "Restart wpa_supplicant (root, opsional)")
                pause(3, "tunggu supplicant")
                # 4. wifi off -> on
                step([ADB, "shell", "svc", "wifi", "disable"], "WiFi OFF")
                pause(2, "tunggu wifi mati")
                step([ADB, "shell", "svc", "wifi", "enable"], "WiFi ON")
                pause(5, "tunggu wifi konek")
                # 5. data seluler off -> on (buat yg pakai SIM)
                step([ADB, "shell", "svc", "data", "disable"], "Data OFF")
                pause(1, "jeda")
                step([ADB, "shell", "svc", "data", "enable"], "Data ON")
                # 6. verifikasi akhir: wifi_on + interface wlan UP (polling,
                #    interface bisa lambat muncul habis supplicant restart)
                wifi_end = step([ADB, "shell", "settings", "get", "global", "wifi_on"], "State WiFi akhir")
                wlan_up = False
                for _i in range(6):
                    links = step([ADB, "shell", "ip", "-o", "link", "show"], "Cek interface")
                    if any("wlan" in ln and "state UP" in ln for ln in links.splitlines()):
                        wlan_up = True
                        break
                    pause(5, "tunggu interface")
                if wifi_end.strip() == "1" and wlan_up:
                    log("✅ Fix WiFi selesai: WiFi ON + interface UP. Cek ikon WiFi di HP nyambung lagi.", "green")
                elif wifi_end.strip() == "1":
                    log("⚠️ WiFi ON tapi interface belum UP, tunggu sebentar / cek manual di HP.", "orange")
                else:
                    log("⚠️ WiFi masih OFF, nyalakan manual di Settings HP.", "orange")
            except Exception as e:
                log(f"❌ Error: {e}", "red")
            finally:
                progress.stop()
                log("—" * 50)

        def run_uifix():
            """Refresh UI tanpa hapus apa pun: restart launcher (auto-nyala
            lagi), drop caches (root opsional), trim-caches global.
            Wallpaper & data aman (tidak ada pm clear / kill SystemUI)."""
            progress.start(15)
            log("\n▶ Fix UI Lag dimulai (wallpaper aman)...\n", "blue")
            win.update_idletasks()

            def step(cmd, note=""):
                if note:
                    log(f"• {note}", "#666")
                log(f"$ {' '.join(cmd)}", "#666")
                win.update_idletasks()
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                except Exception as e:
                    log(f"❌ Error: {e}", "red")
                    return ""
                out = (proc.stdout.strip() + "\n" + proc.stderr.strip()).strip()
                if out:
                    log(out, "green" if proc.returncode == 0 else "red")
                return out

            def pause(s, msg):
                log(f"⏳ {msg} ({s} dtk)...")
                win.update_idletasks()
                try:
                    time.sleep(s)
                except Exception:
                    pass

            try:
                # 1. deteksi launcher default
                out = step([ADB, "shell", "cmd", "package", "resolve-activity", "--brief",
                            "-a", "android.intent.action.MAIN",
                            "-c", "android.intent.category.HOME"], "Deteksi launcher")
                launcher = "com.android.launcher3"
                for ln in out.splitlines():
                    ln = ln.strip()
                    if "/" in ln and not ln.startswith("priority"):
                        launcher = ln.split("/")[0]
                        break
                log(f"Launcher: {launcher}")
                # 2. restart launcher saja (otomatis nyala lagi, wallpaper utuh)
                step([ADB, "shell", "am", "force-stop", launcher], "Restart launcher")
                pause(3, "tunggu launcher nyala")
                # 3. drop caches RAM (root, gagal = skip, tanpa hapus data)
                step([ADB, "shell", "su", "-c", "echo 3 > /proc/sys/vm/drop_caches"],
                     "Drop caches RAM (root, opsional)")
                # 4. trim caches global (aman)
                step([ADB, "shell", "pm", "trim-caches", "1073741824"], "Trim caches")
                # 5. verifikasi launcher hidup lagi
                pid = step([ADB, "shell", "pidof", launcher], "Cek launcher")
                if pid.strip():
                    log(f"✅ UI refresh selesai, launcher hidup (pid {pid.strip()}). Wallpaper utuh.", "green")
                else:
                    log("⚠️ Launcher belum kelihatan, tap Home sekali di HP.", "orange")
            except Exception as e:
                log(f"❌ Error: {e}", "red")
            finally:
                progress.stop()
                log("—" * 50)

        # === Create Buttons ===
        for name, cmds in fixers.items():
            if cmds == "uifix":
                ttk.Button(scrollable, text=name,
                           command=run_uifix).pack(fill="x", padx=10, pady=3)
                continue
            ttk.Button(scrollable, text=name,
                       command=lambda c=cmds, n=name: run_fix(c, f"{n} Completed!", RISKY.get(n))
                       ).pack(fill="x", padx=10, pady=3)

        def run_fix_all():
            risky = [n for n in fixers if n in RISKY]
            if risky and not messagebox.askyesno(
                    "WARNING Fix All",
                    "Fix All menjalankan ini juga:\n- " + "\n- ".join(
                        f"{n}: {RISKY[n].splitlines()[0]}" for n in risky) +
                    "\n\nLanjut?"):
                log("Fix All dibatalkan.", "orange")
                return
            run_fix([cmd for cmds in fixers.values() if isinstance(cmds, list) for cmd in cmds],
                    "All basic fixes executed successfully!")
            if any(c == "uifix" for c in fixers.values()):
                run_uifix()

        ttk.Button(scrollable, text="📶 Fix WiFi / Network",
                   command=run_wifi_fix).pack(fill="x", padx=10, pady=3)

        if brand in brand_fixers:
            ttk.Separator(scrollable, orient="horizontal").pack(fill="x", padx=10, pady=(10,4))
            ttk.Label(scrollable, text="🏷 Brand-Specific Fixes", font=("Segoe UI", 10, "bold")).pack()
            for name, cmds in brand_fixers[brand].items():
                ttk.Button(scrollable, text=name,
                           command=lambda c=cmds, n=name: run_fix(c, f"{n} Completed!", RISKY.get(n))
                           ).pack(fill="x", padx=10, pady=3)

        ttk.Separator(scrollable, orient="horizontal").pack(fill="x", padx=10, pady=(10,6))
        ttk.Button(scrollable, text="⚡ Fix All (Recommended)", style="Accent.TButton",
                   command=run_fix_all).pack(fill="x", padx=10, pady=(2,8))

        ttk.Button(scrollable, text="Close", command=win.destroy).pack(pady=8)

    # ---------- Package Manager UI ----------
    def open_package_manager(self):
        if not is_bin_available(ADB):
            messagebox.showerror("❌ adb missing", "adb not found in PATH.")
            return

        win = tk.Toplevel(self.root)
        win.title("📦 Package Manager")
        win.geometry("700x480")

        ttk.Label(win, text="📋 Packages (pm list packages):").pack(anchor=tk.W, padx=6, pady=4)

        search_var = tk.StringVar()
        search_entry = ttk.Entry(win, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=6)

        listbox = tk.Listbox(win, selectmode=tk.EXTENDED)
        listbox.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        btnf = ttk.Frame(win)
        btnf.pack(fill=tk.X, padx=6, pady=6)

        ttk.Button(btnf, text="🔄 Refresh", command=lambda: self.pm_refresh(listbox, search_var)).pack(side=tk.LEFT)
        ttk.Button(btnf, text="🗑️ Uninstall (normal)", command=lambda: self.pm_uninstall_normal(listbox)).pack(side=tk.LEFT, padx=6)
        ttk.Button(btnf, text="👤 Uninstall (user 0)", command=lambda: self.pm_uninstall_selected(listbox)).pack(side=tk.LEFT, padx=6)
        ttk.Button(btnf, text="🚫 Disable", command=lambda: self.pm_disable_selected(listbox)).pack(side=tk.LEFT, padx=6)
        ttk.Button(btnf, text="✅ Enable", command=lambda: self.pm_enable_selected(listbox)).pack(side=tk.LEFT, padx=6)
        ttk.Button(btnf, text="💾 Backup selected APKs", command=lambda: self.pm_backup_selected(listbox)).pack(side=tk.RIGHT)

        self.pm_refresh(listbox, search_var)

        def on_search(*a):
            term = search_var.get().lower()
            items = getattr(listbox, "_allpkgs", [])
            listbox.delete(0, tk.END)
            for p in items:
                if term in p.lower():
                    listbox.insert(tk.END, p)

        search_var.trace_add("write", lambda *a: on_search())


    def pm_refresh(self, listbox, search_var):
        try:
            res = subprocess.run(
                [ADB, "shell", "pm", "list", "packages"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8
            )
            lines = [ln.split("package:")[-1].strip() for ln in res.stdout.splitlines() if ln.strip()]
        except Exception as e:
            messagebox.showerror("💥 Error", f"Failed to list packages:\n{e}")
            lines = []
        listbox._allpkgs = lines
        q = search_var.get().lower()
        listbox.delete(0, tk.END)
        for p in lines:
            if q in p.lower():
                listbox.insert(tk.END, p)


    def pm_uninstall_selected(self, listbox):
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("ℹ️ Info", "No package selected.")
            return
        packages = [listbox.get(i) for i in sel]
        if not messagebox.askyesno("⚠️ Confirm", f"Uninstall {len(packages)} package(s) for user 0?"):
            return
        for pkg in packages:
            start_cmd([ADB, "shell", "pm", "uninstall", "-k", "--user", "0", pkg], self.term, dry_run=self.dryrun_var.get())
            time.sleep(0.08)


    def pm_uninstall_normal(self, listbox):
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("ℹ️ Info", "No package selected.")
            return
        packages = [listbox.get(i) for i in sel]
        if not messagebox.askyesno("⚠️ Confirm", f"Uninstall {len(packages)} package(s) permanently (all users)?"):
            return
        for pkg in packages:
            start_cmd([ADB, "shell", "pm", "uninstall", pkg], self.term, dry_run=self.dryrun_var.get())
            time.sleep(0.08)


    def pm_disable_selected(self, listbox):
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("ℹ️ Info", "No package selected.")
            return
        pkgs = [listbox.get(i) for i in sel]
        for p in pkgs:
            start_cmd([ADB, "shell", "pm", "disable-user", "--user", "0", p], self.term, dry_run=self.dryrun_var.get())
            time.sleep(0.08)


    def pm_enable_selected(self, listbox):
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("ℹ️ Info", "No package selected.")
            return
        pkgs = [listbox.get(i) for i in sel]
        for p in pkgs:
            start_cmd([ADB, "shell", "pm", "enable", p], self.term, dry_run=self.dryrun_var.get())
            time.sleep(0.08)


    def pm_backup_selected(self, listbox):
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("ℹ️ Info", "No package selected.")
            return
        pkgs = [listbox.get(i) for i in sel]
        folder = filedialog.askdirectory(title="💾 Select folder to save APK backups")
        if not folder:
            return
        for pkg in pkgs:
            threading.Thread(target=self._backup_apk_worker_with_progress, args=(pkg, folder), daemon=True).start()


    def _backup_apk_worker_with_progress(self, package, folder):
        progress = ProgressDialog(self.root, f"📦 Backing up {package} ...")
        try:
            try:
                res = subprocess.run(
                    [ADB, "shell", "pm", "path", package],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10
                )
                out = res.stdout.strip()
            except Exception as e:
                output_q.put(f"❌ Error getting pm path for {package}: {e}\n")
                progress.close(); return

            if not out:
                output_q.put(f"⚠️ Package {package} path not found or no permission.\n")
                progress.close(); return

            paths = [ln.split("package:")[-1].strip() for ln in out.splitlines() if ln.strip()]
            for pth in paths:
                base = os.path.basename(pth)
                dest = os.path.join(folder, f"{package}__{base}")
                run_cmd_stream([ADB, "pull", pth, dest], self.term, dry_run=self.dryrun_var.get())

            output_q.put(f"✅ Backup finished for {package}\n")

        finally:
            progress.close()

    # ---------- 🔥 Thermal Unlocker (Root Only) ----------
    def open_thermal_unlocker(self):
        """Thermal Unlocker UI — toggle thermal throttling (root only)"""
        win = tk.Toplevel(self.root)
        win.title("🔥 Thermal Unlocker (Root Only)")
        win.geometry("300x220")
        win.resizable(True, True)

        # --- Center window on screen ---
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        win.geometry(f"+{x}+{y}")

        ttk.Label(win, text="Thermal Unlocker", font=("Segoe UI", 11, "bold")).pack(pady=(15, 5))
        ttk.Label(
            win,
            text="⚠️ Root Required\nModify thermal control system.\nUse with caution!",
            foreground="#ff4444",
            font=("Segoe UI", 9)
        ).pack(pady=5)

        ttk.Separator(win, orient="horizontal").pack(fill="x", pady=6)

        ttk.Button(
            win,
            text="🚀 Disable Thermal Throttling",
            width=30,
            command=lambda: self.disable_thermal(win)
        ).pack(pady=6)

        ttk.Button(
            win,
            text="♻️ Re-enable Thermal Control",
            width=30,
            command=lambda: self.enable_thermal(win)
        ).pack(pady=4)

        ttk.Separator(win, orient="horizontal").pack(fill="x", pady=8)

        ttk.Button(
            win,
            text="❌ Close",
            width=30,
            command=win.destroy
        ).pack(pady=4)


    def disable_thermal(self, parent):
        """Disable thermal control (dangerous)"""
        if not messagebox.askyesno("⚠️ Confirmation", "Disable all thermal throttling?\nThis may cause overheating."):
            return

        if not messagebox.askyesno("⚠️ Final Warning", "Seriously disable thermal control?\nDevice may overheat or shut down!"):
            return

        if hasattr(self, 'dryrun_var') and self.dryrun_var.get():
            messagebox.showinfo("Dry-Run", "Dry-run mode active. Command skipped.")
            return

        cmd = [
            ADB, "shell", "su", "-c",
            "for z in /sys/class/thermal/thermal_zone*/mode; do echo disabled > $z; done"
        ]
        self.term.insert(tk.END, "\n[ThermalUnlocker] Disabling thermal throttling...\n")
        start_cmd(cmd, self.term)
        self.term.insert(tk.END, "[ThermalUnlocker] ⚡ Thermal throttling disabled.\n")


    def enable_thermal(self, parent):
        """Re-enable thermal control"""
        if not messagebox.askyesno("♻️ Re-enable", "Re-enable thermal management?"):
            return

        if hasattr(self, 'dryrun_var') and self.dryrun_var.get():
            messagebox.showinfo("Dry-Run", "Dry-run mode active. Command skipped.")
            return

        cmd = [
            ADB, "shell", "su", "-c",
            "for z in /sys/class/thermal/thermal_zone*/mode; do echo enabled > $z; done"
        ]
        self.term.insert(tk.END, "\n[ThermalUnlocker] Re-enabling thermal control...\n")
        start_cmd(cmd, self.term)
        self.term.insert(tk.END, "[ThermalUnlocker] ✅ Thermal control restored.\n")

    # ---------- Developer Mode ----------
    def open_developer_mode(self):
        # Developer Mode — improved Theme Customizer / Presets / Import-Export
        dev = tk.Toplevel(self.root)
        dev.title("👨‍💻 Dev Mode — Theme & UI Customizer 🧩")
        dev.geometry("350x520")
        dev.resizable(True, True)

        # --- Center window ---
        dev.update_idletasks()
        w = dev.winfo_width()
        h = dev.winfo_height()
        sw = dev.winfo_screenwidth()
        sh = dev.winfo_screenheight()
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        dev.geometry(f"+{x}+{y}")

        # current theme dict (fallback)
        theme = current_theme if isinstance(current_theme, dict) else DEFAULT_THEME.copy()

        # helper: apply theme to whole app (live)
        def apply_theme_to_app(t):
            try:
                # styling modern terpusat dulu, lalu warna terminal + status bar
                self._apply_modern_style(t)
                s = ttk.Style()
                # try keep existing theme, just override colors
                s.configure("TFrame", background=t.get("bg"))
                s.configure("TLabel", background=t.get("bg"), foreground=t.get("fg"))
                s.configure("TButton", background=t.get("button"), foreground=t.get("fg"))
                s.configure("TEntry", fieldbackground=t.get("bg"), foreground=t.get("fg"))
                s.map("TButton", background=[("active", t.get("accent"))])
                # update terminal colors if exists
                try:
                    self.term.configure(bg=t.get("terminal_bg", DEFAULT_THEME["terminal_bg"]),
                                        fg=t.get("terminal_fg", DEFAULT_THEME["terminal_fg"]))
                except Exception:
                    pass
                # update status bar
                try:
                    self.status.configure(background=t.get("bg"), foreground=t.get("fg"))
                except Exception:
                    pass
            except Exception:
                # don't crash app on styling errors
                pass

        # Frame layout
        top = ttk.Frame(dev, padding=8)
        top.pack(fill=tk.BOTH, expand=False)

        ttk.Label(top, text="🎨 Theme Customizer", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0,6))

        frm = ttk.Frame(dev)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        # keys shown to user (label -> theme key)
        color_keys = [
            ("🌆 Background", "bg"),
            ("📝 Teks (Label)", "fg"),
            ("🖲️ Tombol", "button"),
            ("💡 Accent (Active)", "accent"),
            ("🖥️ Terminal BG", "terminal_bg"),
            ("💚 Terminal FG", "terminal_fg")
        ]

        entries = {}

        def pick_color(key):
            cur = entries[key].get()
            col = colorchooser.askcolor(color=cur, title=f"Pilih warna untuk {key}")
            if col and col[1]:
                entries[key].delete(0, tk.END)
                entries[key].insert(0, col[1])
                preview_local()

        # create grid of label + entry + picker
        for i, (label, key) in enumerate(color_keys):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=6)
            ent = ttk.Entry(frm, width=18)
            ent.grid(row=i, column=1, padx=6, sticky="w")
            ent.insert(0, theme.get(key, DEFAULT_THEME.get(key)))
            btn = ttk.Button(frm, text="🎨", width=3, command=lambda k=key: pick_color(k))
            btn.grid(row=i, column=2, padx=(6,0))
            entries[key] = ent

        # font size accessibility
        ttk.Label(frm, text="🔠 Font size (UI)").grid(row=len(color_keys), column=0, sticky="w", pady=(12,6))
        font_size_var = tk.IntVar(value=10)
        font_size_spin = ttk.Spinbox(frm, from_=8, to=20, textvariable=font_size_var, width=6)
        font_size_spin.grid(row=len(color_keys), column=1, sticky="w", pady=(12,6))

        # presets (quick)
        def apply_preset(name):
            if name == "Dark":
                preset = {
                    "bg": "#1e1e1e", "fg": "#ffffff", "button": "#2d2d2d",
                    "accent": "#0078d7", "terminal_bg": "#0b0b0b", "terminal_fg": "#00ff99"
                }
            elif name == "Light":
                preset = {
                    "bg": "#f4f4f4", "fg": "#1a1a1a", "button": "#e0e0e0",
                    "accent": "#0066cc", "terminal_bg": "#ffffff", "terminal_fg": "#006600"
                }
            elif name == "Sea":
                preset = LIGHT_BLUE_SEA_THEME.copy()
            else:  # Faa Ramadhan default
                preset = DEFAULT_THEME.copy()
            for k, v in preset.items():
                if k in entries:
                    entries[k].delete(0, tk.END)
                    entries[k].insert(0, v)
            preview_local()
            apply_theme_to_app(preset)

        presetf = ttk.Frame(dev)
        presetf.pack(fill=tk.X, padx=10)
        ttk.Label(presetf, text="🎨 Presets:").pack(side=tk.LEFT)
        ttk.Button(presetf, text="🌑 Dark", command=lambda: apply_preset("Dark")).pack(side=tk.LEFT, padx=6)
        ttk.Button(presetf, text="🌕 Light", command=lambda: apply_preset("Light")).pack(side=tk.LEFT, padx=6)
        ttk.Button(presetf, text="🌊 Sea", command=lambda: apply_preset("Sea")).pack(side=tk.LEFT, padx=6)
        ttk.Button(presetf, text="⚙️ Default", command=lambda: apply_preset("Default")).pack(side=tk.LEFT, padx=6)

        # preview area (small sample)
        preview = tk.Frame(dev, relief=tk.SUNKEN, height=90)
        preview.pack(fill=tk.X, padx=10, pady=(10,6))
        sample_lbl = tk.Label(preview, text="Sample text — Faa Ramadhan Tools", anchor="w")
        sample_lbl.pack(fill=tk.X, padx=8, pady=6)
        sample_btn = tk.Button(preview, text="Sample Button")
        sample_btn.pack(padx=8, pady=(0,8))

        def preview_local():
            # apply colors to preview widgets only
            local = {k: entries[k].get() for _, k in color_keys}
            bgc = local.get("bg", DEFAULT_THEME["bg"])
            fgc = local.get("fg", DEFAULT_THEME["fg"])
            btnc = local.get("button", DEFAULT_THEME["button"])
            tbg = local.get("terminal_bg", DEFAULT_THEME["terminal_bg"])
            tfg = local.get("terminal_fg", DEFAULT_THEME["terminal_fg"])
            # preview frame/bg
            try:
                preview.configure(bg=bgc)
                sample_lbl.configure(bg=bgc, fg=fgc, font=("Segoe UI", font_size_var.get()))
                sample_btn.configure(bg=btnc, fg=fgc)
            except Exception:
                pass

        # import / export theme (JSON)
        def export_theme():
            data = {k: entries[k].get() for _, k in color_keys}
            data["mode"] = "custom"
            fn = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json")])
            if not fn:
                return
            try:
                with open(fn, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                messagebox.showinfo("✅ Export Successful", "🎨 Theme successfully exported to JSON file.")
            except Exception as e:
                messagebox.showerror("❌ Export Failed", f"⚠️ Theme export failed:\n{e}")

        def import_theme():
            fn = filedialog.askopenfilename(title="📂 Import theme JSON", filetypes=[("JSON","*.json")])
            if not fn:
                return
            try:
                with open(fn, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for _, k in color_keys:
                    if k in data:
                        entries[k].delete(0, tk.END); entries[k].insert(0, data[k])
                preview_local()
                messagebox.showinfo("✅ Import Successful", "🎨 The theme was successfully imported and is displayed as a preview.")
            except Exception as e:
                messagebox.showerror("❌ Import Failed", f"⚠️ Unable to import theme:\n{e}")

        iof = ttk.Frame(dev)
        iof.pack(fill=tk.X, padx=10, pady=(6,8))
        ttk.Button(iof, text="💾 Export Theme", command=export_theme).pack(side=tk.LEFT, padx=4)
        ttk.Button(iof, text="📂 Import Theme", command=import_theme).pack(side=tk.LEFT, padx=4)

        def apply_live():
            newt = {k: entries[k].get() for _, k in color_keys}
            newt["mode"] = "custom"
            preview_local()
            apply_theme_to_app(newt)

        def save_and_close():
            for _, k in color_keys:
                current_theme[k] = entries[k].get()
            current_theme["mode"] = "custom"
            save_config(CONFIG_PATH, current_theme)
            apply_theme_to_app(current_theme)

            messagebox.showinfo("✅ Saved", "💾 Theme & RGB settings successfully saved to config.json.")
            dev.destroy()

        # --- fungsi helper (reset/apply/save) ---
        def reset_to_default():
            for k, v in DEFAULT_THEME.items():
                if k in entries:
                    entries[k].delete(0, tk.END)
                    entries[k].insert(0, v)
            preview_local()
            apply_theme_to_app(DEFAULT_THEME)

        def apply_live():
            newt = {k: entries[k].get() for _, k in color_keys}
            newt["mode"] = "custom"
            preview_local()
            apply_theme_to_app(newt)

        def save_and_close():
            for _, k in color_keys:
                current_theme[k] = entries[k].get()
            current_theme["mode"] = "custom"
            save_config(CONFIG_PATH, current_theme)
            apply_theme_to_app(current_theme)
            messagebox.showinfo("✅ Saved", "💾 Theme successfully saved to config.\nSome changes may require a restart.")
            dev.destroy()

        # --- tombol aksi bawah ---
        actionf = ttk.Frame(dev)
        actionf.pack(fill=tk.X, padx=10, pady=(6,12))
        ttk.Button(actionf, text="♻️ Reset to Default", command=reset_to_default).pack(side=tk.LEFT)
        ttk.Button(actionf, text="⚡ Apply Live", command=apply_live).pack(side=tk.RIGHT, padx=6)
        ttk.Button(actionf, text="💾 Save & Close", command=save_and_close).pack(side=tk.RIGHT)

        # initial preview
        preview_local()

    # ---------- Backup/Restore ----------
    def backup_apks_prompt(self):
        pkgs = simpledialog.askstring(
            "📦 Backup APKs",
            "Enter package names separated by commas (or leave blank to open Package Manager):"
        )
        if not pkgs:
            self.open_package_manager()
            return

        packages = [p.strip() for p in pkgs.split(",") if p.strip()]
        folder = filedialog.askdirectory(title="💾 Select folder to save APKs")
        if not folder:
            return

        confirm = messagebox.askyesno("Confirm", f"Backup {len(packages)} APK(s) to:\n{folder} ?")
        if not confirm:
            return

        for pkg in packages:
            threading.Thread(
                target=self._backup_apk_worker_with_progress,
                args=(pkg, folder),
                daemon=True
            ).start()

        messagebox.showinfo("📁 Backup Started", "Backup process is running in background.")


    def restore_apks_prompt(self):
        fn = filedialog.askopenfilename(
            title="📲 Select APK or ZIP file (or cancel to choose folder)",
            filetypes=[("APK", ".apk"), ("ZIP", ".zip"), ("All files", ".*")]
        )

        if not fn:
            folder = filedialog.askdirectory(title="📂 Select folder containing APKs")
            if not folder:
                return

            confirm = messagebox.askyesno("Confirm", f"Install all APKs from:\n{folder} ?")
            if not confirm:
                return

            for f in os.listdir(folder):
                if f.lower().endswith(".apk"):
                    start_cmd([ADB, "install", "-r", os.path.join(folder, f)], self.term, dry_run=self.dryrun_var.get())
            messagebox.showinfo("✅ Done", "All APKs from folder have been installed.")
            return

        if fn.lower().endswith(".zip"):
            tmp = filedialog.askdirectory(title="📦 Select temp extract folder")
            if not tmp:
                return
            try:
                with zipfile.ZipFile(fn, "r") as z:
                    z.extractall(tmp)
                for rootp, dirs, files in os.walk(tmp):
                    for f in files:
                        if f.lower().endswith(".apk"):
                            start_cmd([ADB, "install", "-r", os.path.join(rootp, f)], self.term, dry_run=self.dryrun_var.get())
                messagebox.showinfo("🎯 Success", "All APKs from ZIP installed successfully.")
            except Exception as e:
                messagebox.showerror("💥 Error", f"Failed to extract or install:\n{e}")

        elif fn.lower().endswith(".apk"):
            confirm = messagebox.askyesno("Confirm", f"Install this APK?\n{os.path.basename(fn)}")
            if confirm:
                start_cmd([ADB, "install", "-r", fn], self.term, dry_run=self.dryrun_var.get())
                messagebox.showinfo("📦 Installed", f"{os.path.basename(fn)} installed successfully.")
        else:
            messagebox.showinfo("ℹ️ Info", "Unsupported file. Choose APK, ZIP, or folder.")

    # ---------- Presets ----------
    def run_preset_debloat(self):
        """Buka jendela Debloat Preset Manager"""
        DebloatPresetWindow(self.root, self.term, self.dryrun_var)

    # ---------- Fastboot actions ----------
    def fastboot_devices(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("fastboot missing", "fastboot not found in PATH."); return
        start_cmd([FASTBOOT, "devices"], self.term, dry_run=self.dryrun_var.get())

    # ---------- Fastboot Terminal ----------
    def open_fastboot_terminal(self):
        """Open Mini Fastboot Terminal (auto 'fastboot' prefix + command history + clear button)"""
        win = tk.Toplevel(self.root)
        win.title("🖵 Fastboot Terminal — Simplified Mode")
        win.geometry("720x420")

        # --- Center window ---
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        win.geometry(f"+{x}+{y}")

        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="🖵 Fastboot Mini Terminal — type commands (no need to prefix with 'fastboot'):",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0,4))

        text_area = tk.Text(frame, height=18, wrap="word", font=("Consolas", 10))
        text_area.pack(fill="both", expand=True, pady=4)
        text_area.insert(
            tk.END,
            "[Fastboot Mini Terminal Started]\nType commands like:\n"
            "  devices\n  flash boot boot.img\n  reboot\n  getvar all\n\n"
        )
        text_area.configure(state="disabled")

        input_frame = ttk.Frame(frame)
        input_frame.pack(fill="x", pady=(4,0))

        cmd_var = tk.StringVar()
        entry = ttk.Entry(input_frame, textvariable=cmd_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0,4))
        entry.focus()

        ttk.Button(input_frame, text="🧹 Clear", command=lambda: clear_output()).pack(side="left", padx=(0,4))
        ttk.Button(input_frame, text="Run", command=lambda: run_command()).pack(side="right", padx=(4,0))

        history = []
        history_index = -1

        def append_output(text):
            text_area.configure(state="normal")
            text_area.insert(tk.END, text + "\n")
            text_area.see(tk.END)
            text_area.configure(state="disabled")

        def clear_output():
            text_area.configure(state="normal")
            text_area.delete("1.0", tk.END)
            text_area.insert(tk.END, "[Fastboot Mini Terminal Cleared]\n\n")
            text_area.configure(state="disabled")

        def run_command(event=None):
            nonlocal history_index
            cmdline = cmd_var.get().strip()
            if not cmdline:
                return

            history.append(cmdline)
            history_index = len(history)

            cmd_var.set("")
            append_output(f"$ {cmdline}")

            parts = build_fastboot_command(cmdline)
            if not parts:
                append_output("[Error] Invalid command syntax.")
                return

            try:
                proc = subprocess.Popen(
                    parts,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True
                )
                for line in proc.stdout:
                    append_output(line.rstrip())
                proc.wait()
                append_output(f"[Exited with code {proc.returncode}]\n")
            except FileNotFoundError:
                append_output("[Error] fastboot not found in PATH.")
            except Exception as e:
                append_output(f"[Error] {e}")

        def navigate_history(event):
            nonlocal history_index
            if not history:
                return
            if event.keysym == "Up":
                history_index = max(0, history_index - 1)
            elif event.keysym == "Down":
                history_index = min(len(history), history_index + 1)
            if history_index < len(history):
                cmd_var.set(history[history_index])
                entry.icursor(tk.END)
            else:
                cmd_var.set("")

        entry.bind("<Return>", run_command)
        entry.bind("<Up>", navigate_history)
        entry.bind("<Down>", navigate_history)

    # ---------- Fastboot Prompts ----------
    def fastboot_getvar_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        var = simpledialog.askstring("fastboot getvar", "Enter var (e.g. all or product):", initialvalue="all")
        if var:
            start_cmd([FASTBOOT, "getvar", var], self.term, dry_run=self.dryrun_var.get())

    def fastboot_flash_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        img = filedialog.askopenfilename(title="Select .img to flash", filetypes=[("IMG",".img"),("All files",".*")])
        if not img: return
        name = os.path.basename(img).lower()
        guessed = None
        for k in ("boot", "recovery", "system", "vbmeta", "vendor", "odm", "product"):
            if k in name:
                guessed = k; break
        part = simpledialog.askstring("Partition", f"Partition to flash (guessed: {guessed}):", initialvalue=(guessed or "boot"))
        if part and messagebox.askyesno("Confirm", f"Flash {os.path.basename(img)} to partition {part}?"):
            start_cmd([FASTBOOT, "flash", part, img], self.term, dry_run=self.dryrun_var.get())

    def fastboot_erase_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        part = simpledialog.askstring("Erase", "Partition to erase (e.g. userdata):", initialvalue="userdata")
        if part and messagebox.askyesno("Confirm", f"Erase partition {part}? This is destructive."):
            start_cmd([FASTBOOT, "erase", part], self.term, dry_run=self.dryrun_var.get())

    def fastboot_reboot_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        mode = simpledialog.askstring("Reboot", "Target (reboot / bootloader / recovery / poweroff):", initialvalue="reboot")
        if mode:
            start_cmd([FASTBOOT, "reboot", mode], self.term, dry_run=self.dryrun_var.get())

    def fastboot_raw_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        args = simpledialog.askstring("Raw fastboot", "Enter args (without 'fastboot'):")
        if args:
            argv = split_cmdline(args)
            if argv is None:
                messagebox.showerror("Raw fastboot", "Sintaks quotes tidak valid.")
                return
            start_cmd([FASTBOOT] + argv, self.term, dry_run=self.dryrun_var.get())

    # ---------- Unlock/Lock UI triggers ----------
    def attempt_unlock_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        fb_list = fastboot_devices_list()
        if not fb_list.strip():
            messagebox.showerror("❌ No Device", "No fastboot device detected. Please enter bootloader first."); return

        summary = ("🚨 This will attempt common fastboot commands to unlock the bootloader.\n"
                "⚠️ WARNING: This usually ERASES ALL DATA and may void your warranty.\n\n"
                f"Dry-run: {self.dryrun_var.get()}\n"
                f"Force: {self.force_var.get()}\n\n"
                "Continue?")
        if not messagebox.askyesno("🔓 Confirm Unlock", summary):
            return

        default_log = f"unlock_log_{timestamp()}.txt"
        logfile = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=default_log, title="💾 Save unlock log as (or cancel to skip)")
        if logfile == "":
            logfile = None

        threading.Thread(target=unlock_worker, args=(self.term, self.dryrun_var.get(), self.force_var.get(), logfile), daemon=True).start()

    def attempt_lock_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        fb_list = fastboot_devices_list()
        if not fb_list.strip():
            messagebox.showerror("❌ No Device", "No fastboot device detected. Please enter bootloader first."); return

        summary = ("🔒 Locking the bootloader may ERASE ALL DATA and secure your device again.\n"
                f"Dry-run: {self.dryrun_var.get()}\n"
                f"Force: {self.force_var.get()}\n\n"
                "Continue?")
        if not messagebox.askyesno("🔐 Confirm Lock", summary):
            return

        threading.Thread(target=lock_worker, args=(self.term, self.dryrun_var.get(), self.force_var.get()), daemon=True).start()

    def fastboot_getvar_all_cmd(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found."); return
        try:
            res = subprocess.run([FASTBOOT, "getvar", "all"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=6)
            combined = (res.stdout or "") + "\n" + (res.stderr or "")
            output_q.put(combined + "\n")
        except Exception as e:
            output_q.put(f"❌ Error running getvar all: {e}\n")

    def auto_flash_zip_prompt(self):
        if not is_bin_available(FASTBOOT):
            messagebox.showerror("⚠️ Fastboot Missing", "Fastboot binary not found in PATH."); return
        fn = filedialog.askopenfilename(title="📂 Select firmware ZIP to auto-flash", filetypes=[("ZIP files",".zip")])
        if not fn: return
        threading.Thread(target=auto_flash_zip_worker, args=(self.term, fn, self.dryrun_var.get()), daemon=True).start()

    def show_detect_unlock(self):
        suggestion = detect_unlock_suggestion()
        messagebox.showinfo("🔍 Detect Unlock Method", suggestion)

    # ---------- MultiFlash window launcher ----------
    def open_multi_flash_window(self):
        MultiFlashWindow(self.root, self.term, self.dryrun_var)

    # ---------- Progress control ----------
    def update_progress_state(self):
        with proc_list_lock:
            running = any((p.poll() is None) for p in current_procs) if current_procs else False
        if running and not self.prog_running:
            try:
                self.prog.pack(side=tk.RIGHT, fill=tk.X, expand=True)
                self.prog.start(20)
                self.progress_label.config(text="⚡ Status: Running tasks...")
                self.prog_running = True
            except Exception:
                pass
        elif not running and self.prog_running:
            try:
                self.prog.stop()
                self.prog.pack_forget()
                self.progress_label.config(text="✅ Status: Idle")
                self.prog_running = False
            except Exception:
                pass
        self.root.after(200, self.update_progress_state)

    # ---------- Auto Flash ZIP ----------
    def open_auto_flash_zip(self):
        zip_path = filedialog.askopenfilename(
            title="Select Firmware ZIP",
            filetypes=[("Firmware ZIP", "*.zip")]
        )
        if not zip_path:
            return

        threading.Thread(
            target=auto_flash_zip_worker,
            args=(
                self.term,
                zip_path,
                self.dryrun_var.get(),
                self.force_active_slot.get()
            ),
            daemon=True
        ).start()

    # ---------- Simulate Auto Flash ZIP (Dry-Run) ----------
    def simulate_auto_flash_zip(self):
        zip_path = filedialog.askopenfilename(
            title="Select Firmware ZIP (Simulation)",
            filetypes=[("Firmware ZIP", "*.zip")]
        )
        if not zip_path:
            return

        threading.Thread(
            target=auto_flash_zip_worker,
            args=(self.term, zip_path, True),
            daemon=True
        ).start()

    # ---------- Boot Image Toolkit ----------
    def open_boot_image_toolkit(self):
        img = filedialog.askopenfilename(
            title="Select boot.img",
            filetypes=[("Boot Image", "*.img")]
        )
        if not img:
            return

        def worker():
            tmp = tempfile.mkdtemp(prefix="faa_boot_")
            ok, msg = unpack_boot(img, tmp)
            if not ok:
                output_q.put(f"[BOOT] Unpack failed: {msg}\n")
                return

            magisk = detect_magisk(tmp)
            output_q.put(f"[BOOT] Magisk detected: {magisk}\n")

            ok, new_img = repack_boot(tmp)
            if ok:
                output_q.put(f"[BOOT] Repacked OK: {new_img}\n")
            else:
                output_q.put("[BOOT] Repack failed\n")

        threading.Thread(target=worker, daemon=True).start()

    # ---------- Permission Manager ----------
    def open_permission_manager_ui(self):
        win = tk.Toplevel(self.root)
        win.title("Permission Manager")
        win.geometry("520x420")

        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)

        pkg_list = tk.Listbox(frame)
        pkg_list.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=6)

        def load_pkgs():
            pkg_list.delete(0, tk.END)
            for p in list_packages():
                pkg_list.insert(tk.END, p)

        def grant_perm():
            pkg = pkg_list.get(tk.ACTIVE)
            perm = simpledialog.askstring("Grant Permission", "Permission name:")
            if not pkg or not perm:
                return
            threading.Thread(
                target=lambda: output_q.put(grant_permission(pkg, perm)[1] + "\n"),
                daemon=True
            ).start()

        def revoke_perm():
            pkg = pkg_list.get(tk.ACTIVE)
            perm = simpledialog.askstring("Revoke Permission", "Permission name:")
            if not pkg or not perm:
                return
            threading.Thread(
                target=lambda: output_q.put(revoke_permission(pkg, perm)[1] + "\n"),
                daemon=True
            ).start()

        ttk.Button(btn_frame, text="🔄 Refresh", command=load_pkgs).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="✅ Grant", command=grant_perm).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="❌ Revoke", command=revoke_perm).pack(side="left", padx=4)

        threading.Thread(target=load_pkgs, daemon=True).start()


# ---------- ProgressDialog ----------
class ProgressDialog:
    def __init__(self, parent, title="Working..."):
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.geometry("360x96")
        self.top.transient(parent)
        self.top.grab_set()
        ttk.Label(self.top, text=title).pack(pady=8)
        self.pb = ttk.Progressbar(self.top, mode="indeterminate")
        self.pb.pack(fill=tk.X, padx=12, pady=6)
        self.pb.start(20)
        self.top.protocol("WM_DELETE_WINDOW", lambda: None)

    def close(self):
        try:
            self.pb.stop()
            self.top.grab_release()
            self.top.destroy()
        except Exception:
            pass

# ---------- main ----------
def main():
    if BOOTSTRAP_AVAILABLE:
        try:
            root = tb.Window(themename='cosmo')
        except Exception:
            root = tk.Tk()
    else:
        root = tk.Tk()
    app = FaaRamadhanApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
