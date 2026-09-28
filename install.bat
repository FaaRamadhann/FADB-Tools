@echo off
setlocal EnableExtensions
REM === ADB Tools by Faa Ramadhan - tambah folder ini ke System PATH ===
REM Klik kanan install.bat > Run as administrator
REM (otomatis minta UAC kalau belum admin)

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Meminta hak Administrator...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "TARGET=%~dp0"
if "%TARGET:~-1%"=="\" set "TARGET=%TARGET:~0,-1%"
set "FADB_TARGET=%TARGET%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=$env:FADB_TARGET; $c=[Environment]::GetEnvironmentVariable('Path','Machine'); $p=@($c -split ';' | Where-Object { $_ -ne '' }); if ($p -inotcontains $t) { [Environment]::SetEnvironmentVariable('Path', (($p + $t) -join ';'), 'Machine'); Write-Output ('[OK] Ditambahkan ke System PATH: ' + $t) } else { Write-Output '[OK] Sudah ada di System PATH.' }; Add-Type -Namespace Win32 -Name EnvNotify -MemberDefinition '[DllImport(\"user32.dll\", SetLastError=true, CharSet=CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);'; $r=[UIntPtr]::Zero; [Win32.EnvNotify]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r) | Out-Null; Write-Output 'Selesai. Buka terminal BARU, atau tekan Win+R lalu ketik: fadb'"

endlocal
pause
