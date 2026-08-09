' Creates "Acoustic Smart Filler" on the desktop, pointing at App\START.vbs
' (silent launcher: Chrome + app). Called by SETUP.bat at the folder root.

Option Explicit

Dim sh, fso, root, appDir, startVbs, desktop, linkPath, sc, iconPath, chrome

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
appDir = root & "\App"
startVbs = appDir & "\START.vbs"

If Not fso.FileExists(startVbs) Then
  WScript.Echo "START.vbs not found in App folder: " & startVbs
  WScript.Quit 1
End If

desktop = sh.SpecialFolders("Desktop")
linkPath = desktop & "\Acoustic Smart Filler.lnk"

Set sc = sh.CreateShortcut(linkPath)
sc.TargetPath = "wscript.exe"
sc.Arguments = "//nologo """ & startVbs & """"
sc.WorkingDirectory = appDir
sc.WindowStyle = 1
sc.Description = "Open Acoustic Smart Filler (debug Chrome + app)"

' Prefer Chrome icon; fall back to shell default
iconPath = ""
chrome = sh.ExpandEnvironmentStrings("%ProgramFiles%\Google\Chrome\Application\chrome.exe")
If fso.FileExists(chrome) Then
  iconPath = chrome & ",0"
Else
  chrome = sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe")
  If fso.FileExists(chrome) Then
    iconPath = chrome & ",0"
  Else
    chrome = sh.ExpandEnvironmentStrings("%LocalAppData%\Google\Chrome\Application\chrome.exe")
    If fso.FileExists(chrome) Then
      iconPath = chrome & ",0"
    End If
  End If
End If

If iconPath <> "" Then
  sc.IconLocation = iconPath
ElseIf fso.FileExists(appDir & "\app.ico") Then
  sc.IconLocation = appDir & "\app.ico"
Else
  sc.IconLocation = "%SystemRoot%\System32\shell32.dll,13"
End If

sc.Save
WScript.Echo "Desktop shortcut OK: " & linkPath
WScript.Quit 0
