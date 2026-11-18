# ChangeHistory

## 2026.11.18 08:29 補錄節點服務契約與資料結構草案

**類型**: 新增 · **檔案**: `Docs/ServiceContract.md`、`schemas/service-description.schema.json`、`examples/services/`、`TODO.md`

### 修改

- 定義以能力為基礎的聯邦協作，模型規模、能力與 Agent 角色分別宣告，服務涵蓋建構、推論、Agent、協調及 MCP。
- 整理版本協商、政策修訂、授權交集、委派邊界及請求生命週期，標明現有 build 介面的實作差距。
- 新增 0.1-draft1 服務描述 Schema 與 build.runner、ai.inference 範例。結構及日期格式驗證通過，十種錯誤描述被拒絕。接收端語意驗證與請求授權契約仍待實作。

**相關文件**: Docs/ServiceContract.md

---

## 2026.11.18 08:29 補錄輸入與輸出容量保護

**類型**: 改善 · **檔案**: `scripts/bounded_process.py`、`scripts/openbuildnet.py`、`scripts/openbuildnet_peer.py`、`scripts/test_openbuildnet.py`

### 修改

- 輸入快照限制為 64 個檔案與 4 MiB，超出時在建立任務目錄與容器前拒絕。
- 建構日誌限制為 1 MiB，超限停止容器並回報 log_limit_exceeded。容器使用 none 日誌驅動，由執行器保存日誌。
- 產物限制為 8 MiB，收集暫存封存檔亦設上限，超出時回報 artifact_limit_exceeded；接單暫存需求調整為 16 MiB。
- Windows 與 Arch 通過超限、原有執行案例及清理驗收，另經 SSH 確認日誌超限後能執行下一筆正常建構。工作區寫入硬配額仍待選定儲存方案。

**相關文件**: Docs/WorkerResources.md、Docs/Development.md

---

## 2026.11.18 08:29 補錄資源事件監看與常駐服務

**類型**: 新增 · **檔案**: `scripts/worker_events.py`、`scripts/openbuildnet-monitor.service`、`scripts/openbuildnet_peer.py`、`scripts/test_peer_resources.py`

### 修改

- Arch 接入 PSI 記憶體／I/O 壓力通知及容器 cgroup memory.events，記錄事件計數、監看方式與訂閱失敗原因。
- 執行中預設每 10 秒輕量核對容量，接近保留線或資源壓力時改為 2 秒。空閒服務以 15 分鐘完整核對保底，可配置為 30 分鐘，事件失效時降級為短週期。
- 以 SQLite 共用停接與恢復判定，預設恢復餘裕為 64 MiB、觀察期為 30 秒，報告隨任務結果保存。
- Arch 七項回歸通過；Windows 通過可執行項目，Linux inotify 案例略過。實際取得容器 OOM 事件，常駐使用者服務已啟動。長週期採縮短間隔驗證，開機未登入的服務存續待驗收。

**相關文件**: Docs/WorkerResources.md、Docs/WindowsArchSSH.md

---

## 2026.11.18 08:29 補錄 RAM 與 swap 限制驗收

**類型**: 改善 · **檔案**: `scripts/openbuildnet.py`、`scripts/test_openbuildnet.py`

### 修改

- 容器 RAM 上限為 128 MiB，memory-swap 設為相同值，容器 swap 額度為零。
- 依 Docker 的 OOMKilled 判定記憶體超限，任務回報 failed 與 memory_limit_exceeded。
- Arch 已核對 cgroup memory.max 與 memory.swap.max，Windows 與 Arch 均驗證 OOM、容器清理及後續正常任務。

**相關文件**: Docs/Development.md

---

## 2026.11.18 08:29 補錄 Arch 資源量測與接單檢查

**類型**: 新增 · **檔案**: `scripts/openbuildnet_peer.py`、`Docs/WorkerResources.md`

### 修改

- 診斷回報 MemAvailable、任務資料、輸入暫存、Docker 及 containerd 所在檔案系統的可用容量，附來源、時間及共同容量範圍。
- 接單鎖內檢查 RAM 與磁碟餘裕，共用檔案系統合併計算。支援系統保留量設定及資源不足的拒絕原因。
- 已核對 Arch 本機讀值並完成派工驗收。Windows Docker Desktop 的資源量測仍待接入，完整配額及工作區硬上限仍有待辦。

**相關文件**: Docs/WorkerResources.md

---

## 2026.11.18 08:29 補錄雙節點派工與操作文件

**類型**: 新增 · **檔案**: `scripts/openbuildnet_peer.py`、`scripts/Send-ArchDevelopmentSnapshot.ps1`、`Docs/WindowsArchSSH.md`、`Docs/UseCases/`

### 修改

- 透過手動授權的 SSH 節點查詢能力、提交任務及回傳日誌和產物，提供單一遠端名額與滿載拒絕。
- Windows → Arch 與 Arch → Windows 的實際派工已通過，另驗證滿載拒絕、執行中任務 ID、狀態及開始時間。
- 新增 Windows 快照上傳與遠端測試腳本，補充 VS Code Remote SSH、合蓋睡眠、多系統重新開機及服務操作指南；已實測指定 Arch 筆電合蓋後 SSH 可用。
- 收錄 iOS／macOS 與 Windows → Linux 應用場景，補充設備、網路、電源、工具鏈與 IDE 診斷的後續規劃。
- 修正產物 extractfile 回傳空值時的處理，既有 Docker 執行驗收通過。

**相關文件**: Docs/Development.md、Docs/WindowsArchSSH.md、Docs/UseCases/README.md

---

## 2026.11.17 03:11 補上指定產物缺失的驗收

**類型**: 新增 · **檔案**: `scripts/test_openbuildnet.py`

### 修改 - 命令退出碼為 0 但缺少指定產物時，驗證任務失敗且仍可依 ID 查回退出碼與日誌

---

## 2026.11.17 03:09 加入本機執行器端到端驗收

**類型**: 新增 · **檔案**: `scripts/test_openbuildnet.py`

### 修改 - 透過 CLI 驗證成功、非零退出與逾時，並以任務 ID 重新查詢結果

**相關文件**: Docs/Development.md

---

## 2026.11.17 03:00 新增本機 JSON 任務執行器

**類型**: 新增 · **檔案**: `scripts/openbuildnet.py`

### 修改 - 使用專用 Docker 映像執行本機任務，依任務 ID 讀取狀態、日誌、退出碼與產物路徑

**相關文件**: Docs/Development.md

---

## 2026.11.17 02:55 完成單節點 Docker 測試基線驗收

**類型**: 新增 · **檔案**: `scripts/Test-SingleNodeDocker.ps1`

### 修改 - 專用映像實跑通過成功（退出碼 0 並核對 result.txt）、非零退出（7）、逾時（timed_out）及隔離（isolation_verified）四個案例

---

## 2026.11.17 02:52 新增容器內隔離測試

**類型**: 新增 · **檔案**: `scripts/Test-SingleNodeDocker.ps1`

### 修改 - 檢查主機測試檔、Docker socket 與容器網路介面的可見性

**相關文件**: Docs/Development.md

---

## 2026.11.17 02:51 檢查單節點測試容器的隔離設定

**類型**: 新增 · **檔案**: `scripts/Test-SingleNodeDocker.ps1`

### 修改 - 啟動前確認網路模式為 none、未啟用 privileged mode，且沒有掛載

**相關文件**: Docs/Development.md

---

## 2026.11.17 02:41 設定 ChangelogKit 文件分組與顏色

**類型**: 新增 · **檔案**: `.changelog.toml`

### 修改 - 開發指南使用藍色，架構與規格使用紫色，其餘文件顯示在其他

---

## 2026.11.17 02:36 新增單節點逾時驗證

**類型**: 新增 · **檔案**: `scripts/Test-SingleNodeDocker.ps1`

### 修改 - 驗證逾時後停止容器並保留日誌

- 以 `-TestTimeout` 啟動預計執行 60 秒的任務，3 秒後確認仍在執行，再停止容器並確認已結束。
- 通過時回報 `timed_out`，且 `build.log` 包含 `build-started`。
- 開發手冊新增執行指令。此案例已在 Docker Desktop 實跑通過。

---

## 2026.11.17 02:32 新增單節點非零退出驗證

**類型**: 新增 · **檔案**: `scripts/Test-SingleNodeDocker.ps1`

### 修改 - 驗證容器退出碼 7 與標準錯誤日誌

- `-TestFailure` 讓容器向標準錯誤輸出 `build-failed`，並以 7 結束。
- 驗證腳本比對容器退出碼與日誌，吻合時測試通過。7 是測試指定值。

---

## 2026.11.17 02:20 加入 VS Code 開發任務與操作手冊

**類型**: 新增 · **檔案**: `.vscode/tasks.json`

### 修改 - 提供映像建置、單節點測試及依序執行的入口

- 提供獨立的映像建置與單節點測試 Task，以及先建置再測試的預設測試任務。
- 開發手冊涵蓋環境需求、終端指令、輸出目錄與 Docker 連線排查。README 加入手冊連結。

**相關文件**: Docs/Development.md

---

## 2026.11.17 02:18 固定單節點開發測試映像

**類型**: 新增 · **檔案**: `docker/dev/Dockerfile`

### 修改 - 固定 Alpine 基礎映像並將 Docker 驗證腳本預設指向專用映像

- 新增單節點成功案例。在停用網路的 Linux 容器內將固定輸入寫成 `result.txt`，並驗證產物內容與退出碼 0。
- 腳本輸出任務 ID 與日誌、產物所在目錄，每次使用獨立容器並在結束後清理。
- `docker/dev/Dockerfile` 以 digest 固定 Alpine 3.22。腳本預設使用本機的 `openbuildnet-dev:v0.1`，禁止執行測試時隱式拉取映像。
- 原始 Alpine 成功案例已實跑通過。切換至專用映像後已重跑通過。

---

## 2026.11.17 02:13 定義 v0.1 單節點執行規格

**類型**: 新增 · **檔案**: `Docs/SingleNodeExecution.md`

### 修改 - 定義任務輸入、執行狀態、隔離邊界與驗收條件

- 定義固定輸入快照、執行環境、命令參數、時限、產物路徑，以及任務 ID 與執行 ID。
- 規格涵蓋排隊、執行、成功、失敗與逾時狀態，以及日誌、退出碼和產物的交付。
- 從 TODO.md 連到規格，單節點里程碑待實作與驗收。

---

## 2026.11.17 01:51 建立 OpenBuildNet v0.1 專案待辦

**類型**: 新增 · **檔案**: `TODO.md`

### 問題 - 需要在 repo 根目錄記錄 v0.1 的開發順序與驗收條件

### 修改 - 建立 TODO.md 並記錄專案基線、里程碑與後續規劃

- 記錄 Git repo 與 Docs/ 基線，依序追蹤單節點執行、雙節點派工與節點失聯重試。
- 使用 ChangelogKit 建立 `Docs/ChangeHistory.md` 並讀回首筆紀錄。

---
