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
