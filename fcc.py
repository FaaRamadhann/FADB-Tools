"""
FAA Clipboard Copas — PC <-> HP via ADB (GBoard, tanpa APK tambahan)
- GUI: Python tkinter + ttk.Notebook (bawaan Python, tanpa pip install)
- Tab PTH : PC -> HP (ketik otomatis via `adb shell input`)
- Tab HTP : HP -> PC (baca via `service call clipboard 4` = getPrimaryClip)
- Tab Manual : panduan pakai + troubleshooting
- Syarat: ADB di PATH, USB Debugging ON, HP kebaca `adb devices`
- Xiaomi/Redmi WAJIB: "USB debugging (Security settings)" = ON + reboot.

Cara pakai:
  python faa_clipboard_copas.py
  atau double-click fcb.vbs (tanpa jendela cmd)
"""
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import base64
import re
import struct
import time

ADB = "adb"
CHUNK_SIZE = 400  # aman untuk `input text`, jangan >500

# IClipboard transaction codes (AOSP, stabil di Android 9-13):
#   4 = getPrimaryClip, 6 = hasPrimaryClip. Argumen: s16 <pkg> i32 <userId>.
_CLIP_PKG = "com.android.shell"
_CLIP_USER = "0"


# ============================== umum ==============================

def _no_window_kwargs() -> dict:
    """Jalankan adb tanpa kedip jendela console (Windows). Di OS lain: no-op."""
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run(cmd: list[str], timeout=15) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           **_no_window_kwargs())
        out = (r.stdout or "") + (r.stderr or "")
        return (r.returncode == 0, out.strip())
    except FileNotFoundError:
        return (False, "adb tidak ditemukan di PATH. Install Android SDK Platform-Tools.")
    except Exception as e:
        return (False, str(e))


def list_devices() -> list[str]:
    ok, out = run([ADB, "devices"])
    if not ok:
        return []
    devs = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "List of" in line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devs.append(parts[0])
    return devs


# ============================== PTH (PC -> HP) ==============================

def escape_for_input(s: str) -> str:
    """
    Aturan `adb shell input text`:
      spasi -> %s (wajib, ini dialek `input`, bukan shell).
    Quote & karakter shell lain (&|;<>`$()\\") AMAN selama dibungkus
    single-quote untuk device-shell (mksh/sh), jadi jangan dibuang —
    cukup escape `'` jadi `'\\''` (tutup-buka quote standar POSIX).
    Kita pakai source `keyboard` (Android 11+) yang lebih toleran unicode.
    """
    s = s.replace(" ", "%s")          # wajib untuk `input text`
    s = s.replace("'", "'\\''")       # escape quote tunggal ala POSIX
    return f"'{s}'"                   # bungkus agar device-shell tidak parsing isi


# Transliterasi karakter umum yg sering bikin `input` NPE
# (KeyCharacterMap HP tidak punya tombolnya: kutip lengkung, dash panjang, dsb.)
_TRANSLATE = str.maketrans({
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2026": "...",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2000": " ", "\u2001": " ",
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
    "\u2022": "*", "\u00b7": "*", "\u00d7": "x", "\u00f7": "/",
    "\u00b0": "o", "\u20ac": "EUR", "\u00a9": "(c)", "\u00ae": "(R)",
})


def sanitize_for_keymap(s: str) -> tuple[str, list[str]]:
    """Sisakan hanya karakter yg pasti bisa diketik via `adb shell input`.
    Return (teks_bersih, daftar_karakter_yg_dibuang)."""
    t = s.translate(_TRANSLATE)
    out: list[str] = []
    dropped: list[str] = []
    for ch in t:
        o = ord(ch)
        if ch == "\t" or 0x20 <= o <= 0x7E:  # ASCII printable + tab: aman
            out.append(ch)
        else:
            dropped.append(ch)
    return ("".join(out), dropped)


def classify_error(out: str) -> str:
    """Kelompokkan error ADB biar pesannya jujur: security / connection / badchar / other."""
    t = (out or "").lower()
    if "adb tidak ditemukan" in t:
        return "adb-missing"
    if "nullpointerexception" in t or "length of null array" in t:
        return "badchar"  # ada karakter yg tak bisa dipetakan ke tombol keyboard
    if any(k in t for k in ("inject_events", "inject events", "securityexception",
                            "security settings", "write_secure_settings",
                            "requires the caller", "instrumentation",
                            "permission denial", "not allowed to inject")):
        return "security"
    if any(k in t for k in ("no devices", "device not found", "device offline",
                            "unauthorized", "still connecting", "no device",
                            "timed out", "timeout", "connection", "closed",
                            "protocol failure", "adb: ", "error: device",
                            "device '", 'device "', "not found")):
        return "connection"
    return "other"


def probe_inject(serial: str) -> tuple[bool, str]:
    """Tes inject yang tidak mengetik apa-apa (KEYCODE_UNKNOWN diabaikan aplikasi).
    Sukses -> HP masih ngasih izin inject. Gagal -> ketahuan sebab aslinya."""
    for args in (["keyboard", "keyevent", "KEYCODE_UNKNOWN"],
                 ["keyevent", "KEYCODE_UNKNOWN"]):
        ok, out = run([ADB, "-s", serial, "shell", "input", *args])
        if ok and "Exception" not in out and "Error" not in out:
            return (True, "")
        last = out
    return (False, last)


def _try_send(serial: str, esc: str) -> tuple[bool, str]:
    """Satu kali percobaan kirim (keyboard dulu, fallback text biasa)."""
    ok, out = run([ADB, "-s", serial, "shell", "input", "keyboard", "text", esc])
    if not ok or "Unknown command" in out or "Error" in out or "Exception" in out:
        ok, out = run([ADB, "-s", serial, "shell", "input", "text", esc])
    if ok and "Exception" not in out and "Error" not in out:
        return (True, "")
    return (False, out)


def send_chunk(serial: str, part: str, log, tag: str, dropped_acc=None) -> tuple[bool, str, str]:
    """Kirim 1 potong teks mentah. Return (ok, reason, detail).
    Retry 3x utk gagal sesaat; kalau NPE karena karakter aneh -> sanitasi + coba lagi.
    Karakter yg dibuang dikumpulkan ke dropped_acc (kalau disediakan)."""
    esc = escape_for_input(part)
    last = ""
    for attempt in (1, 2, 3):
        ok, out = _try_send(serial, esc)
        if ok:
            return (True, "", "")
        last = out
        reason = classify_error(out)
        if reason in ("security", "adb-missing"):
            return (False, reason, out)  # retry pun percuma
        if reason == "badchar":
            break  # jangan retry buta, langsung ke sanitasi di bawah
        # connection/other: bisa transient -> probe dulu, kalau izin masih ada retry
        pok, pout = probe_inject(serial)
        if not pok:
            return (False, classify_error(pout), pout)
        log(f"[retry {attempt}] chunk {tag} gagal sesaat, coba lagi...")
        time.sleep(0.5)
    else:
        # habis 3x retry masih gagal (bukan badchar) -> nyerah
        return (False, classify_error(last), last)
    # --- jalur sanitasi: transliterasi + buang karakter tak terkirim ---
    clean, dropped = sanitize_for_keymap(part)
    if dropped:
        shown = "".join(dict.fromkeys(dropped))  # unik, urut kemunculan
        log(f"[sanitasi] chunk {tag}: dibuang/diganti {len(dropped)} char: {shown!r}")
        if dropped_acc is not None:
            dropped_acc.extend(dropped)
    if not clean.strip():
        return (False, "badchar",
                "chunk hanya berisi karakter yg tidak bisa diketik via ADB "
                "(mis. emoji). Tidak ada yg bisa dikirim.")
    ok, out = _try_send(serial, escape_for_input(clean))
    if ok:
        log(f"[sanitasi] chunk {tag} terkirim setelah dibersihkan.")
        return (True, "", "")
    return (False, classify_error(out), out)


def send_text(serial: str, text: str, log, send_enter: bool) -> tuple[bool, str, str, list]:
    # Pecah per baris agar Enter rapi, lalu chunk per baris.
    # Return (ok, reason, detail, dropped_emoji) — dropped_emoji = semua char
    # yg terpaksa dibuang karena tak bisa diketik ADB (perlu ditambah manual).
    dropped_all: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        return (True, "", "", dropped_all)
    for li, line in enumerate(lines):
        # baris kosong = tekan enter saja
        if line == "":
            ok, out = run([ADB, "-s", serial, "shell", "input", "keyboard", "keyevent", "66"])
            if not ok:  # fallback source lama
                ok, out = run([ADB, "-s", serial, "shell", "input", "keyevent", "66"])
            log(f"[enter] {out}" if out else "[enter] OK")
            if not ok:
                return (False, classify_error(out), out, dropped_all)
            continue
        # chunk teks panjang
        for i in range(0, len(line), CHUNK_SIZE):
            part = line[i:i + CHUNK_SIZE]
            if part.strip():
                # kirim via send_chunk (escape + retry + sanitasi otomatis)
                ok, reason, detail = send_chunk(serial, part, log, f"{i}-{i+len(part)}", dropped_all)
                log(f"[ketik {i}-{i+len(part)}] {'OK' if ok else 'GAGAL (' + reason + '): ' + detail}")
                if not ok:
                    return (False, reason, detail, dropped_all)
        # ganti baris -> tekan enter (kecuali baris terakhir tanpa send_enter)
        is_last = (li == len(lines) - 1)
        if (not is_last) or send_enter:
            if not (is_last and line and not send_enter):
                # untuk antar-baris selalu enter; baris terakhir hanya jika checkbox ON
                if not is_last or send_enter:
                    ok, out = run([ADB, "-s", serial, "shell", "input", "keyboard", "keyevent", "66"])
                    if not ok:
                        run([ADB, "-s", serial, "shell", "input", "keyevent", "66"])
    return (True, "", "", dropped_all)


# ================= mode faacb-root (module Magisk, tercepat) =================
# Syarat: HP rooted + module faacb terinstall. Teks di-base64 (anti masalah
# quote), di-set via Java helper sebagai root, lalu tombol PASTE (root juga,
# sehingga lolos INJECT_EVENTS). Prioritas tertinggi bila tersedia.

_ROOT_CACHE: dict[str, bool] = {}
_FAACB_CACHE: dict[str, bool] = {}


def _sh(serial: str, *args: str, timeout=15) -> tuple[bool, str]:
    return run([ADB, "-s", serial, "shell"] + list(args), timeout=timeout)


def has_root(serial: str) -> bool:
    if serial not in _ROOT_CACHE:
        ok, out = _sh(serial, "su", "-c", "id")
        _ROOT_CACHE[serial] = ok and "uid=0" in out
    return _ROOT_CACHE[serial]


def has_faacb(serial: str) -> bool:
    if serial not in _FAACB_CACHE:
        ok, out = _sh(serial, "su", "-c", "faacb")
        _FAACB_CACHE[serial] = "pakai:" in out
    return _FAACB_CACHE[serial]


def faacb_paste(serial: str, text: str, log, send_enter: bool) -> tuple[bool, str, str]:
    """Kirim teks utuh via faacb (root). Verifikasi baca-balik per potong."""
    parts = [text[i:i + HELPER_SLICE] for i in range(0, len(text), HELPER_SLICE)] or [""]
    for pi, part in enumerate(parts):
        if not part.strip():
            continue
        b64 = base64.b64encode(part.encode("utf-8")).decode()
        ok, out = _sh(serial, "su", "-c", "faacb set " + b64)
        if not ok:
            return (False, "faacb",
                    f"gagal jalanin faacb ({(out or 'tanpa respon')[:150]}). "
                    "Pastikan module terinstall + Magisk grant root untuk Shell.")
        if helper_readback(serial) != part:
            return (False, "faacb",
                    "clipboard HP tidak berubah. Cek grant root (Magisk) untuk Shell.")
        log(f"[faacb {pi + 1}/{len(parts)}] clipboard HP terisi, tekan PASTE...")
        ok, out = _sh(serial, "su", "-c", "faacb paste")
        if not ok:
            # fallback: keyevent biasa (butuh INJECT_EVENTS / Security settings)
            ok, out = run([ADB, "-s", serial, "shell", "input", "keyboard", "keyevent", "279"])
            if not ok or "Exception" in out or "Error" in out:
                ok, out = run([ADB, "-s", serial, "shell", "input", "keyevent", "279"])
        if not ok or "Exception" in out or "Error" in out:
            return (False, "helper-paste",
                    "teks SUDAH di clipboard HP, tapi tombol PASTE gagal — "
                    "tempel manual sekali di HP (tap tahan -> Paste).")
        log(f"[faacb {pi + 1}/{len(parts)}] OK")
    if send_enter:
        run([ADB, "-s", serial, "shell", "input", "keyboard", "keyevent", "66"])
    return (True, "", "")


# ================= mode helper (APK com.faa.fcbclip) =================
# Untuk teks ber-emoji: taruh utuh ke clipboard HP via broadcast Java API
# (unicode 100% utuh, tanpa KeyCharacterMap), lalu tekan tombol PASTE.
# GBoard tidak berubah sama sekali.

HELPER_PKG = "com.faa.fcbclip"
HELPER_ACTION = "com.faa.fcbclip.SET"
HELPER_CMP = "com.faa.fcbclip/.ClipReceiver"
HELPER_SLICE = 2000  # char per broadcast (aman untuk limit argumen shell)


def _needs_helper(text: str) -> bool:
    """True kalau ada char yg pasti dibuang sanitasi (di luar ASCII printable)."""
    return any(not (ch in ("\t", "\n") or 0x20 <= ord(ch) <= 0x7E) for ch in text)


def helper_available(serial: str) -> bool:
    ok, out = run([ADB, "-s", serial, "shell", "pm", "path", HELPER_PKG])
    return ok and "package:" in out


def _quote_shell(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def helper_ensure(serial: str, log) -> None:
    """Beri izin tulis clipboard (sekali saja, permanen). Aman diulang."""
    log("[helper] memberi izin clipboard (appops, sekali saja)...")
    run([ADB, "-s", serial, "shell", "appops", "set", HELPER_PKG,
         "WRITE_CLIPBOARD", "allow"])
    run([ADB, "-s", serial, "shell", "appops", "set", "--uid", HELPER_PKG,
         "WRITE_CLIPBOARD", "allow"])


def helper_readback(serial: str) -> str:
    """Baca balik clipboard HP (kode 4). Untuk verifikasi SET menempel."""
    ok, out = run([ADB, "-s", serial, "shell", "service call clipboard",
                   "4", "s16", "com.android.shell", "i32", "0"])
    if not ok or "Parcel" not in out:
        return ""
    words = re.findall(r"\b[0-9a-fA-F]{8}\b", out)
    if not words or words[0] != "00000000":
        return ""
    return _extract_clip_text(_parcel_bytes(out))[:4000]


def _broadcast_once(serial: str, part: str) -> bool:
    ok, out = run([ADB, "-s", serial, "shell", "am", "broadcast",
                   "-a", HELPER_ACTION, "-n", HELPER_CMP,
                   "-e", "text", _quote_shell(part)])
    return ok and "FCB-OK" in out


def helper_paste(serial: str, text: str, log, send_enter: bool) -> tuple[bool, str, str]:
    """Kirim teks utuh (emoji ok) via APK helper + tombol PASTE per potong.
    Tiap potong DIVERIFIKASI baca-balik; kalau tidak nempel -> beri izin,
    force-stop, coba sekali lagi. Gagal dua kali = lapor jujur."""
    parts = [text[i:i + HELPER_SLICE] for i in range(0, len(text), HELPER_SLICE)] or [""]
    for pi, part in enumerate(parts):
        if not part.strip():
            continue
        _broadcast_once(serial, part)
        if helper_readback(serial) != part:
            # tidak nempel (penolakan diam-diam MIUI?) -> beri izin + ulangi
            helper_ensure(serial, log)
            run([ADB, "-s", serial, "shell", "am", "force-stop", HELPER_PKG])
            if not _broadcast_once(serial, part) or helper_readback(serial) != part:
                return (False, "helper-set",
                        "clipboard HP menolak ditulis walau izin sudah diberi. "
                        "Cek manual: Settings > Apps > FCB Helper > Permissions "
                        "> Clipboard. Atau kunci app + battery No restrictions "
                        "agar MIUI tidak mematikan prosesnya.")
        log(f"[helper {pi + 1}/{len(parts)}] clipboard HP terisi, tekan PASTE...")
        ok, out = run([ADB, "-s", serial, "shell", "input", "keyboard", "keyevent", "279"])
        if not ok or "Exception" in out or "Error" in out:
            ok, out = run([ADB, "-s", serial, "shell", "input", "keyevent", "279"])
        if not ok or "Exception" in out or "Error" in out:
            return (False, "helper-paste",
                    "teks SUDAH di clipboard HP, tapi tombol PASTE gagal — "
                    "tempel manual sekali di HP (tap tahan -> Paste).")
        log(f"[helper {pi + 1}/{len(parts)}] OK")
    if send_enter:
        run([ADB, "-s", serial, "shell", "input", "keyboard", "keyevent", "66"])
    return (True, "", "")


# ============================== HTP (HP -> PC) ==============================

def _parcel_bytes(parcel: str) -> bytes:
    """Rekonstruksi byte stream dari dump hex `service call`."""
    words = re.findall(r"\b[0-9a-fA-F]{8}\b", parcel)
    return b"".join(struct.pack("<I", int(w, 16)) for w in words)


def _extract_clip_text(blob: bytes) -> str:
    """Ambil teks ClipData dari byte parcel.
    Item teks disimpan format String8: int32 LE panjang + byte utf-8 + NUL.
    Kumpulkan semua kandidat valid, kembalikan yang terpanjang."""
    best = b""
    n = len(blob)
    i = 0
    while i + 4 < n:
        (ln,) = struct.unpack_from("<i", blob, i)
        if 1 <= ln <= 4000 and i + 4 + ln < n and blob[i + 4 + ln] == 0:
            cand = blob[i + 4:i + 4 + ln]
            if all(b in (9, 10, 13) or 0x20 <= b <= 0x7E or b >= 0x80
                   for b in cand):
                if len(cand) > len(best):
                    best = cand
        i += 1
    try:
        return bytes(best).decode("utf-8")
    except Exception:
        return bytes(best).decode("utf-8", errors="replace")


def read_hp_clipboard(serial: str) -> tuple[bool, str]:
    """Return (ok, text). ok=True walau kosong (text='')."""
    base = [ADB, "-s", serial, "shell", "service call clipboard"]
    # 1) cek ada isi nggak (6 = hasPrimaryClip -> ...00000001 = ada)
    ok, out = run(base + ["6", "s16", _CLIP_PKG, "i32", _CLIP_USER])
    if ok and out:
        words = re.findall(r"\b[0-9a-fA-F]{8}\b", out)
        if words and int(words[-1], 16) == 0:
            return (True, "")  # hasPrimaryClip = false -> memang kosong
    # 2) ambil isi (4 = getPrimaryClip; word pertama 00000000 = sukses)
    ok, out = run(base + ["4", "s16", _CLIP_PKG, "i32", _CLIP_USER])
    if ok and out and "Parcel" in out:
        words = re.findall(r"\b[0-9a-fA-F]{8}\b", out)
        if words and words[0] == "00000000":
            text = _extract_clip_text(_parcel_bytes(out))
            if text:
                return (True, text[:4000])
            if len(out) < 300:
                return (True, "")  # ClipData null -> kosong beneran
    # 3) fallback: dumpsys clipboard (di sebagian ROM masih nampilin teks)
    ok2, out2 = run([ADB, "-s", serial, "shell", "dumpsys clipboard"])
    if ok2 and out2:
        # format umum: 'text="halo"' atau '{ T: halo }'
        m = re.search(r'text="([^"]+)"', out2)
        if m:
            return (True, m.group(1))
        m = re.search(r"\{\s*T:\s*(.+?)\s*\}", out2, re.S)
        if m:
            t = m.group(1).strip()
            if t:
                return (True, t[:4000])
        if len(out2.strip()) > 5:
            return (True, out2.strip()[:4000])
    return (True, "")


# ============================== GUI ==============================

class PthTab(ttk.Frame):
    """Tab PC -> HP."""
    def __init__(self, master):
        super().__init__(master)

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Device:").pack(side="left")
        self.cb_dev = ttk.Combobox(top, state="readonly", width=28)
        self.cb_dev.pack(side="left", padx=6)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(top, text="🔌 Reconnect", command=self.reconnect).pack(side="left", padx=4)

        self.txt = scrolledtext.ScrolledText(self, wrap="word", height=12, font=("Consolas", 11))
        self.txt.pack(fill="both", expand=True, padx=10, pady=6)
        self.txt.insert("1.0", "Ketik / paste teks dari PC di sini...\nFokuskan kursor di HP (misal kolom chat), lalu klik Paste ke HP.")

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill="x")
        self.var_enter = tk.BooleanVar(value=False)
        ttk.Checkbutton(mid, text="Kirim + Enter di akhir", variable=self.var_enter).pack(side="left")
        self.var_clip = tk.BooleanVar(value=False)
        ttk.Checkbutton(mid, text="Pakai Clipboard PC", variable=self.var_clip).pack(side="left", padx=10)
        ttk.Label(mid, text="Mode:").pack(side="left", padx=(10, 2))
        self.cb_mode = ttk.Combobox(mid, state="readonly", width=14,
                                    values=["Module (root)", "Helper APK"])
        self.cb_mode.set("Module (root)")
        self.cb_mode.pack(side="left")

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="📋 Ambil Clipboard PC", command=self.from_clipboard).pack(side="left", padx=3)
        ttk.Button(btns, text="⌨️ Paste ke HP", command=self.paste).pack(side="left", padx=3)
        ttk.Button(btns, text="Clear", command=lambda: self.txt.delete("1.0", "end")).pack(side="left", padx=3)

        self.logbox = scrolledtext.ScrolledText(self, height=7, font=("Consolas", 9), state="disabled")
        self.logbox.pack(fill="both", padx=10, pady=(0, 10))

        hint = ("Fokuskan input di HP dulu (tap kolom teks sampai keyboard GBoard muncul), "
                "baru klik Paste. Teks panjang otomatis di-split.")
        ttk.Label(self, text=hint, wraplength=530, foreground="gray").pack(padx=10, pady=(0, 8))

        self.refresh()

    def log(self, msg: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        self.update_idletasks()

    def refresh(self):
        prev = self.cb_dev.get().strip()
        devs = list_devices()
        self.cb_dev["values"] = devs
        if devs:
            self.cb_dev.set(prev if prev in devs else devs[0])
            self.log(f"Device ketemu: {', '.join(devs)}")
        else:
            self.cb_dev.set("")
            self.log("Tidak ada device. Cek kabel USB + `adb devices` + Allow USB debugging di HP.")

    def reconnect(self):
        self.log("Restart ADB server...")
        run([ADB, "kill-server"])
        time.sleep(1)
        run([ADB, "start-server"])
        time.sleep(1)
        self.refresh()
        self.log("Reconnect selesai. Cek popup 'Allow USB debugging?' di HP kalau muncul.")

    def from_clipboard(self):
        try:
            s = self.clipboard_get()
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", s)
            self.log(f"Clipboard PC diambil ({len(s)} char).")
        except Exception as e:
            messagebox.showwarning("Clipboard kosong", f"Tidak bisa baca clipboard PC:\n{e}")

    def paste(self):
        serial = self.cb_dev.get().strip()
        if not serial:
            messagebox.showerror("No device", "HP belum kepilih. Klik Refresh dulu.")
            return
        if self.var_clip.get():
            try:
                text = self.clipboard_get()
                self.txt.delete("1.0", "end")
                self.txt.insert("1.0", text)
            except Exception as e:
                messagebox.showwarning("Clipboard", f"Gagal baca clipboard PC:\n{e}")
                return
        else:
            text = self.txt.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("Kosong", "Teks masih kosong.")
            return
        self.log(f"Mengirim {len(text)} char ke {serial} ... (pastikan kursor aktif di HP)")
        # pre-flight: pastikan device beneran nyambung sebelum kirim
        st_ok, st_out = run([ADB, "-s", serial, "get-state"])
        if not st_ok or st_out.strip() != "device":
            detail = st_out or "(tidak ada respon)"
            self.log(f"❌ Koneksi bermasalah ({classify_error(detail)}): {detail}")
            messagebox.showerror(
                "HP tidak nyambung",
                f"ADB tidak bisa ngomong ke {serial}.\n\nRespon: {detail[:300]}\n\n"
                "Coba:\n1. Cek kabel / colok ulang USB\n"
                "2. Pastikan device yang kepilih bener\n"
                "3. Klik '🔌 Reconnect'\n"
                "4. Cek popup 'Allow USB debugging?' di HP -> Allow"
            )
            return
        trail: list[str] = []  # jejak mode yg gagal, utk dialog akhir
        mode = self.cb_mode.get()
        if mode == "Module (root)":
            # 1. module version (Magisk + manager com.faa.fcbmod era):
            #    faacb-root dulu, lalu helper APK, lalu ketik.
            use_faacb = has_root(serial) and has_faacb(serial)
            use_helper = helper_available(serial)
            if not use_faacb:
                self.log("⚠️ Mode Module: root/module tak tersedia, turun mode...")
                trail.append("faacb-root (tak tersedia)")
        else:
            # 2. non-root version (APK default com.faa.fcbclip):
            #    helper APK, lalu ketik.
            use_faacb = False
            use_helper = helper_available(serial)
            if not use_helper:
                self.log("⚠️ Mode Helper: APK tak terinstall, pakai ketik biasa...")
                trail.append("helper-APK (tak tersedia)")
        # mode faacb-root: tercepat + terkuat (butuh root + module).
        if use_faacb:
            self.log("Mode faacb-root (module terdeteksi)...")
            ok, reason, detail = faacb_paste(serial, text, self.log, self.var_enter.get())
            if ok:
                self.log("✅ Selesai via faacb-root (emoji utuh).")
                return
            if reason == "helper-paste":
                self.log(f"⚠️ {detail}")
                messagebox.showinfo("Sudah di clipboard HP", detail)
                return
            trail.append(f"faacb-root: {reason}")
            self.log(f"⚠️ mode faacb gagal ({reason}), coba mode helper APK...")
        # mode helper: teks ber-emoji + APK ada -> kirim utuh (tanpa ketik)
        if use_helper:
            self.log("Mode helper (emoji terdeteksi, APK tersedia)...")
            ok, reason, detail = helper_paste(serial, text, self.log, self.var_enter.get())
            if ok:
                self.log("✅ Selesai via helper (emoji utuh).")
                return
            if reason == "helper-paste":
                self.log(f"⚠️ {detail}")
                messagebox.showinfo("Sudah di clipboard HP", detail)
                return
            trail.append(f"helper-APK: {reason}")
            self.log(f"⚠️ mode helper gagal ({reason}), lanjut mode ketik biasa...")
        ok, reason, detail, dropped = send_text(serial, text, self.log, self.var_enter.get())
        if ok:
            self.log("✅ Selesai.")
            if dropped:
                uniq = "".join(dict.fromkeys(dropped))
                self.log(f"⚠️ {len(dropped)} char tak bisa diketik ADB (dibuang): {uniq!r}")
                messagebox.showwarning(
                    "Terkirim tanpa emoji",
                    f"Teks sudah masuk HP, tapi {len(dropped)} karakter tidak bisa "
                    f"diketik otomatis via ADB (keterbatasan Android, bukan bug):\n{uniq}\n\n"
                    "Tambahkan manual di HP — biasanya sudah ada di recent emoji GBoard.\n\n"
                    "Mau full-otomatis? Install APK helper FCB "
                    "(lihat tab Manual) agar emoji terkirim utuh."
                )
            return
        self.log(f"❌ Gagal ({reason}). Detail asli dari HP: {detail}")
        if trail:
            self.log("Jejak mode: " + " -> ".join(trail) + " -> ketik")
        if reason == "security":
            messagebox.showerror(
                "Izin inject ditolak HP",
                "HP nolak perintah ketik (INJECT_EVENTS).\n\n"
                "Cek ini (DUA toggle beda!):\n"
                "1. Settings > Additional settings > Developer options\n"
                "2. 'USB debugging' = ON\n"
                "3. 'USB debugging (Security settings)' = ON  <-- yg ini!\n"
                "4. Reboot HP, coba lagi.\n\n"
                "Catatan Xiaomi: toggle no.3 suka OFF SENDIRI habis "
                "reboot/update/cabut SIM (butuh SIM + akun Mi biar nempel)."
            )
        elif reason == "connection":
            messagebox.showerror(
                "Koneksi ADB putus sesaat",
                f"HP kepilih: {serial}\nRespon: {detail[:300]}\n\n"
                "Coba:\n1. Colok ulang kabel USB\n"
                "2. Klik '🔌 Reconnect'\n"
                "3. Pastikan device yang kepilih bener"
            )
        elif reason == "adb-missing":
            messagebox.showerror("ADB hilang", detail)
        elif reason == "badchar":
            messagebox.showwarning(
                "Ada karakter yg tidak bisa diketik",
                f"{detail[:400]}\n\nCek log: karakter pengganti/pembuangan dicantumkan di sana.\n"
                "Tips: ketik ulang tanda kutip/emoji bermasalah jadi karakter biasa, lalu Paste lagi."
            )
        else:
            messagebox.showerror(
                "Gagal (penyebab lain)",
                f"Respon mentah dari HP:\n{detail[:500]}\n\n"
                "Screenshot pesan ini + isi log buat debugging."
            )


class HtpTab(ttk.Frame):
    """Tab HP -> PC."""
    def __init__(self, master):
        super().__init__(master)
        self.last = ""
        self.auto_job = None

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Device:").pack(side="left")
        self.cb_dev = ttk.Combobox(top, state="readonly", width=28)
        self.cb_dev.pack(side="left", padx=6)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(top, text="🔌 Reconnect", command=self.reconnect).pack(side="left", padx=4)

        self.txt = scrolledtext.ScrolledText(self, wrap="word", height=12, font=("Consolas", 11))
        self.txt.pack(fill="both", expand=True, padx=10, pady=6)
        self.txt.insert("1.0", "Copy teks di HP dulu (blok -> Salin),\nlalu klik 'Ambil dari HP'.")

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill="x")
        self.var_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(mid, text="Auto-monitor HP (2 dtk)", variable=self.var_auto,
                        command=self.toggle_auto).pack(side="left")
        ttk.Label(mid, text="Interval: 2s", foreground="gray").pack(side="left", padx=8)

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="📥 Ambil dari HP", command=self.fetch_once).pack(side="left", padx=3)
        ttk.Button(btns, text="📋 Copy ke Clipboard PC", command=self.to_pc_clip).pack(side="left", padx=3)
        ttk.Button(btns, text="Clear", command=lambda: self.txt.delete("1.0", "end")).pack(side="left", padx=3)

        self.logbox = scrolledtext.ScrolledText(self, height=7, font=("Consolas", 9), state="disabled")
        self.logbox.pack(fill="both", padx=10, pady=(0, 10))
        ttk.Label(self, text="Begitu ketarik, teks otomatis masuk clipboard PC — tinggal Ctrl+V.",
                  foreground="gray").pack(padx=10, pady=(0, 8))
        self.refresh()

    def log(self, msg: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        self.update_idletasks()

    def refresh(self):
        prev = self.cb_dev.get().strip()
        devs = list_devices()
        self.cb_dev["values"] = devs
        if devs:
            self.cb_dev.set(prev if prev in devs else devs[0])
            self.log(f"Device: {', '.join(devs)}")
        else:
            self.cb_dev.set("")
            self.log("Tidak ada device. Cek kabel + `adb devices` + Allow USB debugging.")

    def reconnect(self):
        self.log("Restart ADB server...")
        run([ADB, "kill-server"])
        time.sleep(1)
        run([ADB, "start-server"])
        time.sleep(1)
        self.refresh()
        self.log("Reconnect selesai. Cek popup 'Allow USB debugging?' di HP kalau muncul.")

    def set_pc_clip(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()  # wajib agar clipboard nempel di Windows

    def fetch_once(self):
        serial = self.cb_dev.get().strip()
        if not serial:
            messagebox.showerror("No device", "HP belum kepilih. Klik Refresh.")
            return
        ok, text = read_hp_clipboard(serial)
        if not ok:
            self.log(f"❌ {text}")
            return
        if not text:
            self.log("Clipboard HP kosong (copy dulu di HP, lalu klik lagi).")
            return
        if text != self.last:
            self.last = text
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", text)
            self.set_pc_clip(text)
            self.log(f"✅ Ketarik {len(text)} char -> clipboard PC (tinggal Ctrl+V).")
        else:
            self.log("Sama seperti sebelumnya, skip.")

    def to_pc_clip(self):
        t = self.txt.get("1.0", "end-1c")
        if t.strip():
            self.set_pc_clip(t)
            self.log("Disalin ke clipboard PC.")
        else:
            messagebox.showwarning("Kosong", "Textbox masih kosong.")

    def toggle_auto(self):
        if self.var_auto.get():
            self.log("Auto-monitor ON (polling tiap 2 dtk)...")
            self._poll()
        else:
            self.log("Auto-monitor OFF.")
            if self.auto_job:
                self.after_cancel(self.auto_job)
                self.auto_job = None

    def _poll(self):
        if not self.var_auto.get():
            return
        serial = self.cb_dev.get().strip()
        if serial:
            ok, text = read_hp_clipboard(serial)
            if ok and text and text != self.last:
                self.last = text
                self.txt.delete("1.0", "end")
                self.txt.insert("1.0", text)
                self.set_pc_clip(text)
                self.log(f"✅ Baru dari HP ({len(text)} char) -> clipboard PC.")
        self.auto_job = self.after(2000, self._poll)


MANUAL_TEXT = """FAA CLIPBOARD COPAS — Manual / How to Use
================================================

SYARAT
------
- ADB (Android SDK Platform-Tools) ada di PATH
- HP nyolok USB, USB Debugging ON, tap Allow di popup HP
- Klik Refresh kalau HP tidak muncul di daftar Device
- Xiaomi/Redmi WAJIB: DUA toggle ON di
  Settings > Additional settings > Developer options:
    1. 'USB debugging'
    2. 'USB debugging (Security settings)'  <-- yg ini sering kelupaan!
  lalu REBOOT HP. Toggle no.2 suka OFF SENDIRI habis
  reboot / update / cabut SIM (butuh SIM + akun Mi biar nempel).

TAB PTH (PC -> HP)
------------------
1. Di HP: tap kolom teks sampai keyboard GBoard muncul (kursor aktif).
2. Di app: ketik/paste teks, atau centang 'Pakai Clipboard PC'
   agar otomatis mengambil Ctrl+C terakhir.
3. Klik 'Paste ke HP'. Teks panjang (>400 char) otomatis di-split.
4. Kutip " dan ' aman terkirim utuh.
5. Emoji / karakter aneh otomatis disanitasi (kutip lengkung
   diluruskan, emoji dibuang) — detailnya ada di log tab ini.
6. Punya emoji? Install APK helper (poin APK HELPER di bawah),
   maka teks ber-emoji otomatis dikirim utuh. GBoard tetap.
7. Dropdown Mode (2 pilihan):
   - Module (root): era module Magisk + manager com.faa.fcbmod.
     faacb-root dulu, lalu helper APK, lalu ketik.
   - Helper APK: non-root era APK default com.faa.fcbclip.
     helper APK, lalu ketik.
   Gagal di mode pilihan = turun otomatis sambil dicatat di log.

TAB HTP (HP -> PC)
------------------
1. Di HP: blok teks -> Salin (seperti biasa).
2. Di app: klik 'Ambil dari HP'. Teks otomatis masuk
   clipboard PC — tinggal Ctrl+V di mana saja.
3. Atau centang 'Auto-monitor HP' agar tiap ada copy-an baru
   di HP langsung masuk clipboard PC tiap 2 detik.

APK HELPER FCB (opsional, untuk emoji full-otomatis)
---------------------------------------------------
ADB tidak bisa mengetik emoji (keterbatasan Android). APK mini ini
(8 KB, source di folder fcb-helper) menerima teks via broadcast
lalu menaruhnya ke clipboard HP lewat Java API (unicode 100% utuh),
disusul tombol PASTE otomatis. GBoard tidak berubah sama sekali.

1. Install: `adb install fcb-helper/build/fcb-helper.apk`
   (atau build sendiri: jalankan `build.bat` di folder fcb-helper —
   butuh JDK + Android SDK build-tools).
2. Buka aplikasi "FCB Helper" di HP SEKALI (agar broadcast jalan).
3. Tab PTH otomatis pakai mode helper kalau teks mengandung emoji
   dan APK terdeteksi. Tanpa APK: teks tetap terkirim minus emoji
   (dilaporkan di log + dialog).

MODE FAACB-ROOT (tercepat, butuh HP root + module Magisk faacb)
---------------------------------------------------------------
Urutan otomatis tab PTH: faacb-root dulu, lalu helper APK, lalu ketik.
Mode root tidak butuh APK helper, tidak butuh izin (jalan sebagai root),
dan lolos INJECT_EVENTS. Terdeteksi otomatis tiap paste (di-cache per sesi).

ARTI ERROR (TAB PTH)
--------------------
- 'HP tidak nyambung' : kabel kendor / device salah / popup
  Allow belum di-OK. Coba colok ulang + 'Reconnect'.
- security : HP nolak perintah ketik. Cek toggle Security
  settings (DUA toggle, lihat SYARAT) + reboot.
- connection : ADB putus sesaat. Retry otomatis 3x; kalau masih
  gagal klik 'Reconnect' dan cek pilihan device.
- badchar : teks hanya berisi karakter yg tak bisa diketik
  (mis. full emoji). Ganti jadi karakter biasa.
- other : screenshot dialog + log untuk debugging.

TOMBOL
------
- Refresh : scan ulang device (pilihan device tidak ke-reset).
- Reconnect : restart ADB server (kill-server + start-server).
"""


class ManualTab(ttk.Frame):
    """Tab Manual / How to Use."""
    def __init__(self, master):
        super().__init__(master)
        box = scrolledtext.ScrolledText(self, wrap="word", font=("Consolas", 10),
                                        state="normal")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", MANUAL_TEXT)
        box.configure(state="disabled")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FAA Clipboard Copas (PC <-> HP)")
        self.geometry("600x640")
        self.resizable(True, True)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=4, pady=4)
        nb.add(PthTab(nb), text="PTH (PC → HP)")
        nb.add(HtpTab(nb), text="HTP (HP → PC)")
        nb.add(ManualTab(nb), text="Manual / How to Use")


if __name__ == "__main__":
    App().mainloop()
