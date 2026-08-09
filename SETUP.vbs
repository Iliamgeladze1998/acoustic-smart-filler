' Acoustic Smart Filler — visual setup (no CMD, no "press any key")
Option Explicit

Dim sh, fso, root, wizard, pyw

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
wizard = root & "\setup_wizard.py"

If Not fso.FileExists(wizard) Then
  MsgBox "setup_wizard.py not found:" & vbCrLf & wizard, vbCritical, "Setup"
  WScript.Quit 1
End If
If Not fso.FolderExists(root & "\App") Then
  MsgBox "App folder missing next to SETUP.vbs.", vbCritical, "Setup"
  WScript.Quit 1
End If

pyw = FindPythonW()
If pyw = "" Then
  If MsgBox( _
    "Python was not found." & vbCrLf & vbCrLf & _
    "Install Python 3 and tick ""Add python.exe to PATH""." & vbCrLf & vbCrLf & _
    "Open download page now?", vbYesNo + vbExclamation, "Setup") = vbYes Then
    sh.Run "https://www.python.org/downloads/windows/", 1, False
  End If
  WScript.Quit 1
End If

' Launch GUI; window style 0 = no console parent
On Error Resume Next
If LCase(Right(pyw, 3)) = ".exe" Or InStr(1, pyw, "\", vbTextCompare) > 0 Then
  sh.Run """" & pyw & """ """ & wizard & """", 0, False
ElseIf LCase(pyw) = "py" Or LCase(pyw) = "pyw" Then
  sh.Run "pyw -3 """ & wizard & """", 0, False
  If Err.Number <> 0 Then
    Err.Clear
    sh.Run "py -3w """ & wizard & """", 0, False
  End If
Else
  sh.Run """" & pyw & """ """ & wizard & """", 0, False
End If
On Error GoTo 0
WScript.Quit 0

Function FindPythonW()
  Dim candidates, file, folder, subf, rc
  FindPythonW = ""

  If WhereOk("pythonw") Then FindPythonW = "pythonw": Exit Function
  If WhereOk("pyw") Then FindPythonW = "pyw": Exit Function

  candidates = Array( _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python313\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python312\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python311\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python310\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python39\pythonw.exe"), _
    "C:\Python313\pythonw.exe", "C:\Python312\pythonw.exe", "C:\Python311\pythonw.exe" _
  )
  For Each file In candidates
    If fso.FileExists(file) Then FindPythonW = file: Exit Function
  Next

  folder = sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python")
  If fso.FolderExists(folder) Then
    For Each subf In fso.GetFolder(folder).SubFolders
      file = subf.Path & "\pythonw.exe"
      If fso.FileExists(file) Then FindPythonW = file: Exit Function
    Next
  End If

  If WhereOk("python") Then FindPythonW = "python": Exit Function
  If WhereOk("py") Then FindPythonW = "py": Exit Function
End Function

Function WhereOk(cmdName)
  On Error Resume Next
  ' /c ... & exit — never leave cmd waiting
  rc = sh.Run("cmd /c where " & cmdName & " >nul 2>nul", 0, True)
  WhereOk = (rc = 0)
  On Error GoTo 0
End Function
