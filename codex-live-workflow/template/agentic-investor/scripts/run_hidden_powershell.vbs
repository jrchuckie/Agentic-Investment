Option Explicit

Dim shell, command, i, exitCode

If WScript.Arguments.Count < 1 Then
    WScript.Quit 2
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & QuoteArg(WScript.Arguments(0))

For i = 1 To WScript.Arguments.Count - 1
    command = command & " " & QuoteArg(WScript.Arguments(i))
Next

Set shell = CreateObject("WScript.Shell")
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode

Function QuoteArg(value)
    QuoteArg = Chr(34) & Replace(value, Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function
