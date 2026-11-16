' Doubao Pet - silent launcher (no console window)
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Run """" & root & "\.venv\Scripts\pythonw.exe"" main.py", 0, False
