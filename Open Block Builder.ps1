$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$virtualPython = Join-Path $projectDirectory '.venv\Scripts\pythonw.exe'
if (Test-Path -LiteralPath $virtualPython) {
    $pythonExecutable = $virtualPython
} else {
    $pythonCommand = Get-Command python.exe -ErrorAction Stop
    $windowlessPython = Join-Path (Split-Path $pythonCommand.Source) 'pythonw.exe'
    $pythonExecutable = if (Test-Path -LiteralPath $windowlessPython) { $windowlessPython } else { $pythonCommand.Source }
}
# This is the interactive application itself; show its main window.
Start-Process -FilePath $pythonExecutable -ArgumentList '-m', 'block_builder' -WorkingDirectory $projectDirectory -WindowStyle Normal
