@echo off
setlocal EnableExtensions
REM === ADB Tools by Faa Ramadhan - hapus folder ini dari System PATH ===
REM Klik kanan uninstall.bat > Run as administrator
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

powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=$env:FADB_TARGET; $c=[Environment]::GetEnvironmentVariable('Path','Machine'); $p=@($c -split ';' | Where-Object { $_ -ne '' -and $_.TrimEnd('\') -ine $t }); if (($c -split ';') -inotcontains $t) { Write-Output '[OK] Folder ini memang tidak ada di System PATH.' } else { [Environment]::SetEnvironmentVariable('Path', ($p -join ';'), 'Machine'); Write-Output ('[OK] Dihapus dari System PATH: ' + $t) }; Remove-Item -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\fadb.exe' -Recurse -Force -ErrorAction SilentlyContinue; Write-Output '[OK] App Paths fadb.exe dihapus.'; Add-Type -Namespace Win32 -Name EnvNotify -MemberDefinition '[DllImport(\"user32.dll\", SetLastError=true, CharSet=CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);'; $r=[UIntPtr]::Zero; [Win32.EnvNotify]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r) | Out-Null; Write-Output 'Selesai.'"

endlocal
pause
