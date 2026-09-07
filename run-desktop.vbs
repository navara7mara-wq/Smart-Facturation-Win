Option Explicit

Dim shell, fileSystem, appDirectory, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
appDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDirectory
command = "pythonw.exe """ & appDirectory & "\desktop.py"""
shell.Run command, 1, False
