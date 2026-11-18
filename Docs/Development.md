# OpenBuildNet 開發手冊

## 本機環境

準備 Git、PowerShell 7，以及已啟動的 Docker Desktop（Linux 容器模式）。在同一個 VS Code 終端執行 `docker info --format '{{.OSType}}'`，應回傳 `linux`。

## VS Code 任務

開啟 OpenBuildNet repo 根目錄，從命令面板選擇 **Tasks: Run Task**。

| 任務 | 用途 |
| --- | --- |
| OpenBuildNet: Build dev image | 用 `docker/dev/Dockerfile` 建置本機映像 `openbuildnet-dev:v0.1`。 |
| OpenBuildNet: Run single-node smoke test | 沿用已建置的映像，執行單節點成功案例。 |
| OpenBuildNet: Build and test | 依序建置映像並執行測試。首次使用或改過 Dockerfile 時可直接跑這個任務。 |

也可在 repo 根目錄執行：

```powershell
docker build -t openbuildnet-dev:v0.1 docker/dev
pwsh -NoProfile -File scripts/Test-SingleNodeDocker.ps1
```

## 查看測試結果

測試建立獨立的 Linux 容器，停用容器網路，複製固定輸入並回收產物。成功時終端顯示 `TaskId`、`ExitCode`（應為 0）與 `OutputDirectory`。到該目錄查看 `build.log` 與 `result.txt`。

Docker 回報連線權限錯誤時，先用 `docker info` 確認 VS Code 終端可存取引擎。找不到本機映像時，先執行 **Build dev image** 或 **Build and test**。

後續測試依[單節點執行規格](SingleNodeExecution.md)加入非零退出、逾時及隔離驗收。新增案例時沿用目前的開發映像與獨立測試容器。

## 非零退出測試

建置開發映像後，在 repo 根目錄執行：

```powershell
pwsh -NoProfile -File scripts/Test-SingleNodeDocker.ps1 -TestFailure
```

容器應回傳退出碼 7，且輸出目錄的 `build.log` 應包含 `build-failed`。兩者吻合時，驗證腳本本身成功結束。

## 逾時測試

```powershell
pwsh -NoProfile -File scripts/Test-SingleNodeDocker.ps1 -TestTimeout
```

案例啟動後等待 3 秒，確認容器仍在執行，再停止並確認容器已結束。成功時顯示 `timed_out`，輸出目錄中的 `build.log` 含有 `build-started`。

## 容器隔離設定檢查

每次測試都在啟動容器前檢查 Docker 回報的設定，要求網路模式為 `none`、未啟用 privileged mode，且沒有掛載。設定不符會中止測試。此檢查涵蓋容器配置，實際檔案與網路隔離仍需另行驗收。

## 容器內隔離測試

```powershell
pwsh -NoProfile -File scripts/Test-SingleNodeDocker.ps1 -TestIsolation
```

腳本在主機輸出目錄建立測試檔，容器內確認同名檔未出現在 /tmp、Docker socket 不可見，且只有 loopback 介面。成功時顯示 `isolation_verified`，日誌包含 `isolation-ok`。

## 本機任務執行器

準備 Python 3.10 以上及已建置的開發映像。從 repo 根目錄提交範例：

```powershell
python scripts/openbuildnet.py run examples/single-node-task.json
python scripts/openbuildnet.py show <task_id>
```

將第二條指令的 `<task_id>` 換成第一條輸出的 ID。結果包含狀態、退出碼、日誌路徑及產物路徑。本版使用 JSON 內嵌文字檔作為輸入快照，支援一組命令參數及單一檔案產物。執行會等待任務結束。

### 執行器端到端驗收

建置開發映像後執行 `python scripts/test_openbuildnet.py`。驗收經由 CLI 提交成功、非零退出、逾時與產物缺失案例，並逐筆用 `show` 比對持久化結果、檢查日誌與產物。驗收使用臨時狀態目錄。

驗收另包含受限容器內的記憶體超限，以及超限後重新執行成功任務。執行器使用 128 MiB RAM 上限並將容器 swap 額度設為零。Docker 回報 `OOMKilled` 時，結果為 `failed`，原因為 `memory_limit_exceeded`。退出碼 137 本身不足以判定 OOM。測試會確認各次執行的容器已移除。

驗收也包含日誌超過 1 MiB、產物超過 8 MiB，以及輸入快照超過 64 個檔案或 4 MiB 的案例。執行器的容器採用 `none` 日誌驅動，建構日誌由本機有界串流保存到任務結果目錄。收集產物時的暫存資料也受大小限制。

## 雙節點協作

使用 Windows 開發並以 Arch Linux 筆電作為遠端節點時，先參考[從 Windows 操作 Arch Linux 建構節點](WindowsArchSSH.md)。

兩端都需有 Python、SSH 和本機開發映像。由管理員在各端的 SSH 帳號配置對方的公鑰，並讓遠端 SSH 工作階段可執行 `openbuildnet-peer receive`，其入口應轉交給 `python scripts/openbuildnet_peer.py receive`。專用金鑰可在 `authorized_keys` 設定強制命令與 `restrict`，只授權對方使用派工入口。兩端提交前都需預先驗證並登錄對方的 SSH 主機金鑰。

```powershell
python scripts/openbuildnet_peer.py capabilities build-node-b
python scripts/openbuildnet_peer.py submit build-node-b examples/single-node-task.json --platform linux/amd64
```

`--platform` 使用目標節點回報的值。每個接收端提供一個遠端執行名額，滿載或平台與映像標籤不符時拒絕派工。接受的任務同步回傳狀態、退出碼、日誌及指定產物，並在提交端目前目錄建立結果資料夾。可用 `--output` 指定既有資料夾。請求上限為 1 MiB，回傳日誌上限為 1 MiB，完整日誌留在執行節點。

驗收時先由 A 提交到 B，再由 B 提交到 A。每次核對本機收到的 `result.json`、日誌及產物，另以不相符的平台提交一次，確認節點拒絕。

### Worker 唯讀診斷

磁碟、RAM 量測與接單配額的設計見 [Worker 資源與接單規格](WorkerResources.md)。

Arch 查詢會回傳 `resource_health` 和 `admission`，分別顯示系統資源餘裕與目前是否可接單。任務執行期間的 `active_task.resource_monitor` 會定期更新並記錄狀態變化，完成後隨結果保留。可用 `python scripts/test_peer_resources.py` 驗證資源壓力、恢復、拒絕與監看結果保存，測試使用模擬讀值。

```powershell
python scripts/openbuildnet_peer.py diagnostics build-arch
```

查詢回傳節點的建構平台、映像和可用名額。執行遠端派工時，`active_task` 會包含任務 ID、`running` 狀態及 UTC 開始時間。空閒時或建構已完成且正在回傳結果時，此欄位可為 `null`。本機直接執行的任務不佔遠端名額。連線失敗會回報錯誤，接收端也需更新至支援診斷的版本。
