# 從 Windows 操作 Arch Linux 建構節點

Windows 是日常開發與操作端，裝有 Arch Linux 的 MacBook Pro 是遠端建構節點。初次啟用 SSH 和核對主機金鑰需要在筆電上操作。之後可在 Windows 開 PowerShell，透過 SSH 更新程式、重建 Docker 映像、跑測試及查看結果。兩台先接在同一個區域網路。

## 在 Arch 筆電做一次設定

用日後要登入的 Arch 帳號執行。若套件已安裝，可跳過安裝。

```bash
sudo pacman -Syu openssh git python docker
sudo systemctl enable --now sshd docker
sudo usermod -aG docker "$USER"
ip -br address
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

記下 Arch 帳號、區域網路 IP 和主機金鑰指紋。新 SSH 工作階段會取得更新後的群組權限。Docker 群組具備相當於 root 的主機權限，只應加入受你信任的本機帳號。[ArchWiki OpenSSH](https://wiki.archlinux.org/title/OpenSSH) · [Docker 官方文件](https://docs.docker.com/engine/install/linux-postinstall/)

## 從 Windows 設定登入

PowerShell 中執行 `Get-Command ssh, scp, ssh-keygen`。若找不到指令，先安裝 Windows 選用功能 **OpenSSH 用戶端**。在 `$env:USERPROFILE\.ssh\config` 加入以下段落，將 IP 與帳號換成上一步記下的值。若已有設定檔，將段落加到既有內容中。

```sshconfig
Host build-arch
    HostName 192.168.1.50
    User archuser
    IdentityFile ~/.ssh/openbuildnet_arch
    IdentitiesOnly yes
```

在 PowerShell 建立有密碼片語的金鑰。若同名金鑰已存在，沿用該金鑰或改用新檔名。

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE/.ssh/openbuildnet_arch"
Get-Content "$env:USERPROFILE/.ssh/openbuildnet_arch.pub" |
    ssh build-arch 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys'
ssh build-arch 'hostname; python --version; docker info'
```

首次連線先比對指紋，再接受主機金鑰並輸入 Arch 帳號密碼。若 Arch 禁用密碼登入，可從筆電終端將 Windows 的 `.pub` 公鑰追加到該帳號的 `~/.ssh/authorized_keys`。私鑰留在 Windows。需要讓 OpenBuildNet 的批次 SSH 連線使用有密碼片語的金鑰時，先啟動 Windows 的 `ssh-agent` 並用 `ssh-add` 載入私鑰。[Microsoft OpenSSH 金鑰管理](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)

## 從 VS Code 連線與開啟遠端資料夾

1. 在 Windows 的 VS Code 安裝 Microsoft 的 **Remote - SSH** 擴充功能（`ms-vscode-remote.remote-ssh`）。
2. 先在 PowerShell 確認 `ssh build-arch` 能登入。若設定檔已有 `Host build-arch`，VS Code 可直接沿用。也可在命令面板執行 `Remote-SSH: Add New SSH Host...`，輸入 SSH 指令並選擇 Windows 使用者的 `.ssh\config`。
3. 按 `Ctrl+Shift+P`，執行 `Remote-SSH: Connect to Host...`，選擇 `build-arch`。若詢問平台，選 Linux。首次連線會在筆電上設定 VS Code Server。
4. 左下角顯示 `SSH: build-arch` 後，選「檔案 → 開啟資料夾」，開啟 Arch 上的專案目錄。也可以先照下一節上傳，再開啟命令印出的遠端測試目錄。
5. 在遠端視窗開啟整合式終端機。可執行 `hostname` 確認所在主機，再跑 `docker build -t openbuildnet-dev:v0.1 docker/dev` 和 `python scripts/test_openbuildnet.py`。

遠端視窗儲存的變更會直接寫到 Arch 上。若以 Windows 的 `F:\OpenBuildNet` 為主要開發目錄，請在本機視窗編輯，再按下一節上傳要測的版本。Remote - SSH 不會自動同步這兩份檔案。[VS Code Remote - SSH 官方文件](https://code.visualstudio.com/docs/remote/ssh)

## 從 Windows 傳送目前版本並遠端測試

從 Windows 的 repo 根目錄執行。這個方式會將目前工作目錄中的開發檔案傳到筆電，因此也能測試尚未提交的變更。每次解壓到獨立目錄，方便核對是哪一批測試。

```powershell
$archive = Join-Path ([IO.Path]::GetTempPath()) ("openbuildnet-" + [guid]::NewGuid().ToString("N") + ".tar.gz")
$remoteArchive = Split-Path -Leaf $archive
tar -czf $archive scripts docker examples
if ($LASTEXITCODE -ne 0) { throw "打包失敗" }
scp $archive "build-arch:$remoteArchive"
if ($LASTEXITCODE -ne 0) { throw "上傳失敗" }
ssh build-arch ('set -eu; d=$(mktemp -d "$HOME/openbuildnet.XXXXXX"); printf "%s\n" "$d"; tar -xzf "$HOME/{0}" -C "$d"; cd "$d"; docker build -t openbuildnet-dev:v0.1 docker/dev; python scripts/test_openbuildnet.py' -f $remoteArchive)
```

命令會印出遠端測試目錄，測試輸出直接回到 Windows。筆電需要保持開機、連網並避免在執行期間睡眠。腳本若連不上 Docker，先透過 SSH 確認 `id -nG` 包含 `docker`，以及 `systemctl is-active docker` 回傳 `active`。

## 進入雙節點驗收

上述流程可從 Windows 完成 Arch 上的單節點測試。要用 OpenBuildNet 從 Windows 派工給 Arch，還需照[開發手冊](Development.md#雙節點協作)安裝遠端的 `openbuildnet-peer receive` 入口，指向要驗收的版本。

要驗收 Arch 反向派工給 Windows，Windows 也要啟用 OpenSSH Server、設定 Arch 端的公鑰與 Windows 的接收入口。這個方向仍可坐在 Windows 前操作，先 SSH 登入 Arch，再從 Arch 執行提交指令。初次連線同樣要從可信管道核對 Windows 主機金鑰。
