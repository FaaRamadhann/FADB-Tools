# 🚀 ADB & Fastboot Tools — by Faa Ramadhan

**Versi 5.0.1 — Light Blue Sea**

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
- 📋 Clipboard PC ↔ HP (ketik otomatis + baca clipboard HP, source: [FCB-Magisk](https://github.com/FaaRamadhann/FCB-Magisk))

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

GNU General Public License v3.0 — Copyright (C) 2026 Faa Ramadhan

```text
FADB Tools — Copyright (C) 2026 Faa Ramadhan

Program ini free software: kamu bisa mendistribusikan dan/atau
memodifikasinya di bawah ketentuan GNU General Public License
yang diterbitkan Free Software Foundation, versi 3 atau
(opsional) versi yang lebih baru.

Program ini didistribusikan dengan harapan berguna, tapi TANPA
GARANSI apa pun. Lihat GNU General Public License untuk detail.
Teks lengkap ada di file LICENSE.
```
