# 從 Windows 操作 Arch Linux 建構節點

Windows 是日常開發與操作端，裝有 Arch Linux 的 MacBook Pro 是遠端建構節點。初次啟用 SSH 和核對主機金鑰需要在筆電上操作。之後可在 Windows 開 PowerShell，透過 SSH 更新程式、重建 Docker 映像、跑測試及查看結果。兩台先接在同一個區域網路。

## 在 Arch 筆電做一次設定

用日後要登入的 Arch 帳號執行。若套件已安裝，可跳過安裝。

```bash
sudo pacman -Syu openssh git python docker
sudo systemctl enable --now sshd docker
sudo usermod -aG docker "$USER"
ip -br -4 address
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
.\scripts\Send-ArchDevelopmentSnapshot.ps1
```

Docker 尚未就緒時，使用 `-UploadOnly` 先驗證上傳與解包。若尚未設定 `build-arch`，先在 Arch 終端執行 `whoami` 確認帳號，再用 `ip -br -4 address` 查詢 IPv4。選擇和 Windows 連到同一區網的 Wi-Fi 或有線介面。例如輸出為 `192.168.0.103/24`，SSH 位址只填 `192.168.0.103`。

回到 Windows PowerShell 執行以下範例，將 `user332` 和 `192.168.0.103` 換成自己的 Arch 帳號與區網 IPv4。

```powershell
.\scripts\Send-ArchDevelopmentSnapshot.ps1 -UploadOnly -SshHost user332@192.168.0.103
```

命令會印出遠端目錄，測試輸出直接回到 Windows。封裝檔保留在 Windows 暫存目錄與 Arch 登入帳號的家目錄。筆電需要保持開機與連網。腳本若連不上 Docker，先透過 SSH 確認 `id -nG` 包含 `docker`，以及 `systemctl is-active docker` 回傳 `active`。

## 合蓋、睡眠與休眠

電源設定由 Arch 節點管理員手動套用。Windows 的 `Send-ArchDevelopmentSnapshot.ps1` 負責上傳開發檔案及執行測試。筆電平常也要使用時，先選「只停用合蓋睡眠」。若這台機器專供遠端派工，且需要避免桌面或閒置程式要求睡眠，再套用「專用節點停用 systemd 睡眠與休眠」。

在 Arch 上檢查 systemd 的設定、桌面是否接管合蓋事件，以及這次開機的睡眠紀錄。

```bash
cat /sys/class/dmi/id/product_name
cat /proc/acpi/button/lid/*/state
systemd-analyze cat-config systemd/logind.conf
systemd-analyze cat-config systemd/sleep.conf
systemd-inhibit --list
journalctl -b -u systemd-logind --no-pager | grep -Ei 'lid|suspend|hibernate'
```

若日誌有 `Lid closed.` 隨即出現 `Suspending...`，代表合蓋後確實進入睡眠。若 inhibitor 清單有 `handle-lid-switch`，先查持有者的桌面電源設定。桌面也可能透過自己的快捷鍵或閒置程式另外要求睡眠，日誌會顯示請求來源。[systemd logind 設定](https://www.freedesktop.org/software/systemd/man/latest/logind.conf.html)

### 只停用合蓋睡眠

在 Arch 建立 `/etc/systemd/logind.conf.d/90-openbuildnet-lid.conf`。可先用 `sudo mkdir -p /etc/systemd/logind.conf.d` 建立目錄，再用 `sudoedit` 編輯檔案。

```ini
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
```

### 專用節點停用 systemd 睡眠與休眠

若也要關閉桌面或閒置程式透過 systemd 要求的睡眠，在 `/etc/systemd/sleep.conf.d/90-openbuildnet-awake.conf` 加入以下設定。可先用 `sudo mkdir -p /etc/systemd/sleep.conf.d` 建立目錄，再用 `sudoedit` 編輯檔案。[systemd 睡眠設定](https://www.freedesktop.org/software/systemd/man/255/systemd-sleep.conf.html)

```ini
[Sleep]
AllowSuspend=no
AllowHibernation=no
AllowHybridSleep=no
AllowSuspendThenHibernate=no
```

### 多系統重新開機與驗收

如果同一台筆電安裝了 Arch、macOS 或 Windows，執行 `sudo reboot` 前先確認開機選單的預設項目，或安排人在現場選擇 Arch。重新開機會中斷 SSH。若韌體開到另一個系統、停在選單或等待磁碟解鎖，Arch 的 SSH 服務就還沒上線。

Intel MacBook Pro 2014 可在啟動時按住 **Option（⌥）** 進入 Apple 的啟動管理程式，選擇能進入 Arch 的 EFI 啟動項目。若該項目再開啟 GRUB 等開機選單，依實際配置選 Arch。[Apple 的 Intel Mac 開機組合鍵](https://support.apple.com/en-us/102603)

若打算靠 Wake-on-LAN 遠端喚醒，先針對實際使用的網路介面、韌體設定及電源狀態驗證。喚醒成功後仍依現有開機設定選擇系統。第一次測試請在能接觸筆電時進行。

重新進入 Arch 後，從 Windows 執行 `ssh -o BatchMode=yes build-arch 'cat /etc/os-release; uptime'`，確認連回的是 Arch。接上電源、保持通風，合蓋後再執行同一條指令，並於開蓋後核對本次開機的 logind 日誌。若要還原，移走上述新增的設定檔後重新開機。

2026-11-17 實測 MacBookPro11,1 在套用合蓋忽略設定並重開後，筆電維持合蓋時，Windows 的 `ssh -o BatchMode=yes build-arch uptime` 成功回傳。此結果確認當次合蓋時 SSH 可用。

## 進入雙節點驗收

上述流程可從 Windows 完成 Arch 上的單節點測試。要從 Windows 派工給 Arch，在 Arch 的驗收版本目錄中執行以下指令。入口使用登入帳號的 `~/.local/bin/openbuildnet-peer`，因此無須將該目錄加到 SSH 工作階段的 `PATH`。

```bash
mkdir -p ~/.local/bin ~/.local/share/openbuildnet
install -m 600 scripts/openbuildnet.py scripts/openbuildnet_peer.py ~/.local/share/openbuildnet/
cat > ~/.local/bin/openbuildnet-peer <<'EOF'
#!/bin/sh
exec python "$HOME/.local/share/openbuildnet/openbuildnet_peer.py" "$@"
EOF
chmod 700 ~/.local/bin/openbuildnet-peer
```

之後更新接收端時，在新的遠端版本目錄重新執行上面的 `install` 指令。回到 Windows 查詢能力，再依回報的平台提交範例。其他操作見[開發手冊](Development.md#雙節點協作)。

```powershell
python scripts/openbuildnet_peer.py capabilities build-arch
python scripts/openbuildnet_peer.py submit build-arch examples/single-node-task.json --platform linux/amd64
```

要驗收 Arch 反向派工給 Windows，Windows 也要啟用 OpenSSH Server、設定 Arch 端的公鑰與 Windows 的接收入口。這個方向仍可坐在 Windows 前操作，先 SSH 登入 Arch，再從 Arch 執行提交指令。初次連線同樣要從可信管道核對 Windows 主機金鑰。
