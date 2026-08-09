' Acoustic Smart Filler — silent launcher (no CMD window)
' Double-click this file, or use START.bat (it calls this).

Option Explicit

Dim sh, fso, dir, chrome, profile, url, i, appPy, pyw

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
appPy = dir & "\app.py"

If Not fso.FileExists(appPy) Then
  MsgBox "app.py not found next to this launcher.", vbCritical, "Acoustic Smart Filler"
  WScript.Quit 1
End If

chrome = sh.ExpandEnvironmentStrings("%ProgramFiles%\Google\Chrome\Application\chrome.exe")
If Not fso.FileExists(chrome) Then
  chrome = sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe")
End If
If Not fso.FileExists(chrome) Then
  chrome = sh.ExpandEnvironmentStrings("%LocalAppData%\Google\Chrome\Application\chrome.exe")
End If

If Not fso.FileExists(chrome) Then
  MsgBox "Google Chrome was not found. Install Chrome and try again.", vbCritical, "Acoustic Smart Filler"
  WScript.Quit 1
End If

pyw = FindPythonW()
If pyw = "" Then
  MsgBox "Python (pythonw/pyw) was not found." & vbCrLf & "Run SETUP.bat in the folder ABOVE App (package root).", vbCritical, "Acoustic Smart Filler"
  WScript.Quit 1
End If

profile = sh.ExpandEnvironmentStrings("%LocalAppData%\AcousticSmartFiller\ChromeProfile")
url = "https://acoustic.ge/aco_st_admin.php?dispatch=products.update&product_id=15650"

' Chrome visible (style 1 = normal window)
sh.Run """" & chrome & """ --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir=""" & profile & """ """ & url & """", 1, False

' Wait for debug port (up to ~25s), then start app anyway
For i = 1 To 25
  If HttpOk("http://127.0.0.1:9222/json/version") Then Exit For
  WScript.Sleep 1000
Next

' App with pythonw — window style 0 = hidden console (none for pythonw)
sh.Run """" & pyw & """ """ & appPy & """", 0, False
WScript.Quit 0

Function HttpOk(url_)
  On Error Resume Next
  Dim x
  Set x = CreateObject("MSXML2.XMLHTTP")
  If Err.Number <> 0 Then
    Set x = CreateObject("Microsoft.XMLHTTP")
    Err.Clear
  End If
  x.Open "GET", url_, False
  x.Send
  HttpOk = (Err.Number = 0 And CInt(x.Status) = 200)
  On Error GoTo 0
End Function

Function FindPythonW()
  Dim candidates, c, pathEnv, parts, p, rc
  FindPythonW = ""

  ' 1) pyw / pythonw on PATH via where (hidden cmd)
  If WhereOk("pyw") Then
    FindPythonW = "pyw"
    Exit Function
  End If
  If WhereOk("pythonw") Then
    FindPythonW = "pythonw"
    Exit Function
  End If

  ' 2) Common install locations
  candidates = Array( _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python312\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python311\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python310\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python313\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python39\pythonw.exe"), _
    "C:\Python312\pythonw.exe", _
    "C:\Python311\pythonw.exe", _
    "C:\Python310\pythonw.exe" _
  )
  For Each c In candidates
    If fso.FileExists(c) Then
      FindPythonW = c
      Exit Function
    End If
  Next

  ' 3) Scan LocalAppData\Programs\Python\*\pythonw.exe
  p = sh.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python")
  If fso.FolderExists(p) Then
    Dim folder, subf, file
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
