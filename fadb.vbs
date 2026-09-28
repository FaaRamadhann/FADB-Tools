' fadb - ADB Tools launcher by Faa Ramadhan (tanpa jendela console)
' Taruh folder ini di PATH (lihat install.bat), lalu Win+R > ketik: fadb
Option Explicit
Dim fso, sh, scriptDir, appPath, args, i
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
appPath = fso.BuildPath(scriptDir, "app.py")
If Not fso.FileExists(appPath) Then
  MsgBox "app.py tidak ditemukan di:" & vbCrLf & scriptDir, 16, "fadb"
  WScript.Quit 1
End If
args = ""
For i = 0 To WScript.Arguments.Count - 1
  args = args & " """ & WScript.Arguments(i) & """"
Next
sh.Run "pythonw.exe """ & appPath & """" & args, 0, False
Set sh = Nothing
Set fso = Nothing
