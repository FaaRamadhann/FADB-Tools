# 🚀 ADB & Fastboot Tools — by Faa Ramadhan

**Versi 5.0.0 — Light Blue Sea**

🛠️ Toolkit utility Android berbasis GUI menggunakan Python + Tkinter untuk menangani operasi ADB dan Fastboot secara aman dan efisien.

Project ini menggabungkan:
- 📱 Tools utilitas ADB
- ⚡ Tools flashing Fastboot
- 🔥 Sistem Multi Flash batch
- 🔓 Utilitas root
- 📂 File explorer
- 🧩 Toolkit boot image
- 🔐 Permission manager
- 🧹 Debloat manager
- 🛡️ Sistem safety guard
- 📊 Engine simulasi flashing

---

# ✨ Fitur

## 🔧 Fitur Utama

### 📱 Utilitas ADB
- ✅ Deteksi device
- 💻 Eksekusi ADB shell
- 📜 Viewer logcat
- 📦 Package manager
- 🔐 Permission manager
- 🧪 Root checker
- ⚙️ Multi command ADB

### ⚡ Utilitas Fastboot
- 🔓 Universal bootloader unlock
- 🔒 Relock bootloader
- 📦 Auto flash firmware ZIP
- 🔥 Multi flash partition
- 💻 Raw fastboot commands
- 📁 Support dynamic partition
- 🔄 Support A/B slot

### 🛡️ Sistem Keamanan Flashing
- 🔋 Cek baterai
- 📂 Validasi partisi
- 📏 Verifikasi ukuran image
- 🚫 Proteksi active slot
- 🧪 Simulasi dry-run
- 📊 Estimasi risiko flashing

### 🧩 Toolkit Boot Image
- 📦 Unpack boot image
- 🪄 Deteksi Magisk
- 🔄 Repack boot image
- ⚙️ Integrasi `magiskboot`

### 📂 Manajemen File
- 📁 ADB File Explorer
- 🔓 Root mode explorer
- 📤📥 Push/Pull banyak file
- ❌ Hapus file remote
- 🧭 Navigasi direktori

### 🎨 Fitur GUI
- 🖥️ GUI berbasis Tkinter
- 🌊 Default theme Light Blue Sea
- 🌙 Support ttkbootstrap
- 🎨 Sistem tema (customizer + import/export JSON)
- 💾 Penyimpanan konfigurasi
- 🪟 Multi-window workflow
- 📡 Output terminal realtime
- 🖥️ Scrcpy GUI (mirror + visual command builder)
- 📋 Clipboard PC ↔ HP (ketik otomatis + baca clipboard HP)

---

# 📥 Instalasi

## 📌 Kebutuhan
- 🐍 Python 3.9+
- ⚡ ADB & Fastboot sudah masuk PATH

## 📦 Dependency Opsional
```bash
pip install ttkbootstrap
```

## 🛣️ Tambah ke System PATH (buka dari mana saja via Win+R > `fadb`)
1. Klik kanan `install.bat` > **Run as administrator**.
2. Buka terminal BARU (atau tekan `Win+R`, ketik `fadb`, Enter).
Untuk menghapus: klik kanan `uninstall.bat` > **Run as administrator**.
Alternatif manual: tambahkan folder ini ke environment variable `Path` (System).

## ▶️ Menjalankan Program
```bash
python app.py
```
Tanpa jendela console:
```bash
pythonw app.py
```
Atau (setelah install PATH): tekan `Win+R`, ketik `fadb`, Enter.

> `fadb.vbs` adalah launcher tanpa console (pakai `pythonw.exe`), jadi tidak ada jendela/cmd yang berkedip saat dibuka.

---

# 📁 Struktur Project

```text
project/
│
├── app.py
├── fadb.vbs
├── install.bat
├── uninstall.bat
├── config_manager.py
├── config.json
├── Theme/
│   ├── light_blue_sea_theme.json
│   ├── light_blue_theme.json
│   └── lime_theme.json
└── README.md
```

---

# ⚠️ Peringatan Keamanan

🔓 Unlock bootloader atau flashing partisi dapat:
- 🗑️ Menghapus data user
- 📄 Membatalkan garansi
- 💀 Menyebabkan brick jika salah penggunaan

✅ Selalu:
- 💾 Backup data penting
- 🔍 Pastikan firmware sesuai
- 🧪 Gunakan mode dry-run terlebih dahulu

---

# ⚙️ Operasi yang Didukung

## 📱 ADB
```bash
adb devices
adb shell
adb push
adb pull
adb reboot
adb logcat
```

## ⚡ Fastboot
```bash
fastboot devices
fastboot flash
fastboot reboot
fastboot flashing unlock
fastboot flashing lock
```

---

# 👨‍💻 Author

✨ Faa Ramadhan

---

# 📜 Lisensi

MIT License — Copyright (c) 2026 Faa Ramadhan

```text
MIT License

Copyright (c) 2026 Faa Ramadhan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
