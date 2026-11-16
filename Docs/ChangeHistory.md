# ChangeHistory

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
