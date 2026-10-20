Option Explicit
Dim sh, fs, pwsh, launcher
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
launcher = fs.BuildPath(fs.GetParentFolderName(WScript.ScriptFullName), "start.ps1")
If Not fs.FileExists(launcher) Then WScript.Quit 1
pwsh = sh.Environment("PROCESS")("HERMES_MCP_PWSH")
If pwsh = "" Then
    pwsh = "pwsh.exe"
Else
    If InStr(pwsh, Chr(34)) > 0 Then WScript.Quit 1
    If Not fs.FileExists(pwsh) Then WScript.Quit 1
    If LCase(fs.GetExtensionName(pwsh)) <> "exe" Then WScript.Quit 1
    If LCase(fs.GetAbsolutePathName(pwsh)) <> LCase(pwsh) Then WScript.Quit 1
End If
sh.Run Chr(34) & pwsh & Chr(34) & " -NoProfile -NonInteractive -WindowStyle Hidden -File " & Chr(34) & launcher & Chr(34) & " -IfNotRunning", 0, False
