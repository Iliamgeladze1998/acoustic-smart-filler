' Launch app only (no Chrome), without a CMD window.
Option Explicit

Dim sh, fso, dir, appPy, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
appPy = dir & "\app.py"

If Not fso.FileExists(appPy) Then
  MsgBox "app.py not found.", vbCritical, "Acoustic Smart Filler"
  WScript.Quit 1
End If

pyw = FindPythonW()
If pyw = "" Then
  MsgBox "Python (pythonw/pyw) was not found. Run SETUP.bat in the folder ABOVE App.", vbCritical, "Acoustic Smart Filler"
  WScript.Quit 1
End If

sh.Run """" & pyw & """ """ & appPy & """", 0, False
WScript.Quit 0

Function FindPythonW()
  Dim candidates, c, p, folder, subf, file
  FindPythonW = ""
  If WhereOk("pyw") Then
    FindPythonW = "pyw"
    Exit Function
  End If
  If WhereOk("pythonw") Then
    FindPythonW = "pythonw"
    Exit Function
  End If
  candidates = Array( _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python312\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python311\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python310\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python313\pythonw.exe") _
  )
  For Each c In candidates
    If fso.FileExists(c) Then
      FindPythonW = c
      Exit Function
    End If
  Next
  p = sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python")
  If fso.FolderExists(p) Then
    Set folder = fso.GetFolder(p)
    For Each subf In folder.SubFolders
      file = subf.Path & "\pythonw.exe"
      If fso.FileExists(file) Then
        FindPythonW = file
        Exit Function
      End If
    Next
  End If
End Function

Function WhereOk(cmdName)
  On Error Resume Next
  Dim rc
  rc = sh.Run("cmd /c where " & cmdName & " >nul 2>nul", 0, True)
  WhereOk = (rc = 0)
  On Error GoTo 0
End Function
