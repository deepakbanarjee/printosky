If WScript.Arguments.Count = 0 Then WScript.Quit
Dim cmd : cmd = WScript.Arguments(0)
Dim shell : Set shell = CreateObject("WScript.Shell")
shell.Run "cmd /c " & cmd, 0, False
