#Requires -Version 5.1

<#
.SYNOPSIS
從 Windows 將目前的 OpenBuildNet 開發檔案傳送至 Arch 節點。

.DESCRIPTION
封裝 scripts、docker 和 examples，透過 SCP 傳送到 SSH 帳號的家目錄。
每次使用獨立的遠端目錄。預設會重建 Docker 映像並執行單節點驗收。

.PARAMETER SshHost
Windows SSH 設定中的主機別名，或 user@host。預設為 build-arch。

.PARAMETER UploadOnly
上傳並解包，略過 Docker 建置和測試。

.EXAMPLE
.\scripts\Send-ArchDevelopmentSnapshot.ps1 -UploadOnly
#>
[CmdletBinding()]
param(
    [ValidatePattern('\A(?:[a-zA-Z0-9_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9_][a-zA-Z0-9_.-]*\z')]
    [string]$SshHost = 'build-arch',

    [switch]$UploadOnly
)

$ErrorActionPreference = 'Stop'
foreach ($tool in 'tar.exe', 'scp.exe', 'ssh.exe') {
    Get-Command -Name $tool -CommandType Application | Out-Null
}

$root = Split-Path -Parent $PSScriptRoot
$snapshot = 'openbuildnet-' + [guid]::NewGuid().ToString('N')
$archiveName = "$snapshot.tar.gz"
$archivePath = Join-Path ([IO.Path]::GetTempPath()) $archiveName

Write-Host "正在封裝至 $archivePath"
& tar.exe -czf $archivePath -C $root scripts docker examples
if ($LASTEXITCODE -ne 0) {
    throw "封裝失敗（退出碼 $LASTEXITCODE）。"
}

Write-Host "正在上傳至 $SshHost"
& scp.exe $archivePath "${SshHost}:$archiveName"
if ($LASTEXITCODE -ne 0) {
    throw "上傳失敗（退出碼 $LASTEXITCODE）。"
}

# 目錄與檔名由本機產生，只含 ASCII 字母、數字、連字號及句點。
$remoteCommand = "umask 077 && mkdir $snapshot && tar -xzf $archiveName -C $snapshot && cd $snapshot && pwd"
if (-not $UploadOnly) {
    $remoteCommand += ' && docker build -t openbuildnet-dev:v0.1 docker/dev && python scripts/test_openbuildnet.py'
}

& ssh.exe $SshHost $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "遠端作業失敗（退出碼 $LASTEXITCODE）。請查看上方輸出。"
}
