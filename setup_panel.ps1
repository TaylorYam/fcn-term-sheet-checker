$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonExe = $pythonCommand.Source; $pythonArgs = @('-3') }
    else { $pythonExe = (Get-Command python -ErrorAction Stop).Source; $pythonArgs = @() }
    & $pythonExe @pythonArgs -c "import sys, tkinter; assert sys.version_info >= (3,11), 'Python 3.11 or later required'"
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11 以上及 Tcl/Tk。' }
    & $pythonExe @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '無法建立本機虛擬環境。' }
    & '.\.venv\Scripts\python.exe' -m pip install -c constraints.txt .
    if ($LASTEXITCODE -ne 0) { throw '套件安裝失敗，請確認公司網路或套件來源。' }
    & '.\.venv\Scripts\python.exe' -c "import tkinter, fcn_checker.panel"
    if ($LASTEXITCODE -ne 0) { throw '安裝驗證失敗。' }
    Write-Host '安裝完成。請雙擊 launch_panel.cmd 開啟 PANEL。'
    exit 0
} catch {
    Write-Host ('安裝未完成：' + $_.Exception.Message)
    exit 1
}
