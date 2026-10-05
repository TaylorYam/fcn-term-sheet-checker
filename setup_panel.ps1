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
    & '.\.venv\Scripts\python.exe' -m fcn_checker.version --record-installation $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw '安裝版本紀錄失敗。' }
    $openWith = 'launch_panel.cmd'
    try {
        # 桌面與專案資料夾各放一個捷徑；捷徑記錄本機完整路徑，不進 Git
        foreach ($folder in @([Environment]::GetFolderPath('Desktop'), $PSScriptRoot)) {
            $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $folder 'Term Sheet 核對.lnk'))
            $shortcut.TargetPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
            $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'launch_panel.pyw') + '"'
            $shortcut.WorkingDirectory = $PSScriptRoot
            $shortcut.IconLocation = (Join-Path $PSScriptRoot 'src\fcn_checker\assets\panel.ico') + ',0'
            $shortcut.Description = 'FCN Term Sheet 核對'
            $shortcut.Save()
        }
        $openWith = '桌面或專案資料夾裡的「Term Sheet 核對」捷徑'
    } catch {
        Write-Host ('捷徑建立失敗（不影響安裝）：' + $_.Exception.Message)
    }
    Write-Host ('安裝完成。請雙擊 ' + $openWith + ' 開啟 PANEL。')
    exit 0
} catch {
    Write-Host ('安裝未完成：' + $_.Exception.Message)
    exit 1
}
