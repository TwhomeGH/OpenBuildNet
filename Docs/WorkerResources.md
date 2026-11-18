# Worker 資源與接單規格

本規格定義第一版 Docker worker 的資源資料與接單行為。SSH CLI、單機診斷頁與日後的 REST API 共用這些欄位。現有 `available` 欄位繼續表示可用的遠端執行名額。

## 量測與配額

| 資源 | 量測範圍 |
| --- | --- |
| `memory` | Docker 實際執行容器的環境。在 Arch 量測 Linux 的可用 RAM，在 Windows 上量測 Docker Desktop 執行環境的可用 RAM。Docker 回報的記憶體總量只能當容量上限。 |
| `state_disk` | 任務狀態、日誌和產物所在檔案系統的可用空間。若輸入暫存位於另一個檔案系統，也要檢查該位置。 |
| `runtime_disk` | Docker 的可寫層與容器儲存所在檔案系統的可用空間。在 Docker Desktop 上同時考慮虛擬磁碟內部空間與 Windows 上承載該磁碟的磁碟區。 |

兩個路徑共用檔案系統時，將它們的需求相加後檢查一次。Docker Desktop 中同一筆寫入若同時會消耗虛擬磁碟與 Windows 磁碟區，兩層限制都需通過。

目前 Arch 的唯讀量測由 `diagnostics` 的 `resources` 回傳。RAM 讀取 `/proc/meminfo` 的 `MemAvailable`，磁碟依實際路徑查可用空間，並以檔案系統裝置 ID 標記共同容量。輸入暫存列為 `snapshot_disk`。使用本機系統 Docker socket 時，另依 Docker 回報查詢資料目錄。若使用 containerd snapshotter，`containerd_disk` 預設量測 `/var/lib/containerd`。containerd 使用自訂資料目錄時，請在接收端設定 `OPENBUILDNET_CONTAINERD_ROOT`。目前 Windows 接收端回傳 `resources: null`。

各資源以非負整數 byte 表示。節點管理員可針對各檢查範圍設定系統保留量、worker 總配額與單任務上限。診斷至少提供下列欄位。

| 欄位 | 意義 |
| --- | --- |
| `scope` | 量測範圍與來源。共用檔案系統的路徑指向同一個容量範圍。 |
| `measured_at` | 最近成功量測的 UTC 時間。 |
| `measurement_status` | `ok`、`unknown` 或 `stale`。 |
| `available_bytes` | 系統實測的剩餘量。無讀值時為 `null`。 |
| `system_reserve_bytes` | 留給系統的最低餘裕。 |
| `worker_limit_bytes` | 管理員設定的總配額。未設時為 `null`。 |
| `per_task_limit_bytes` | 管理員設定的單任務上限。 |
| `reserved_bytes` | 已承諾給執行中任務的容量，含已使用部分。 |
| `effective_available_bytes` | 實測剩餘量扣除系統保留與尚未反映在讀值中的保留量，再取 worker 剩餘配額的較小值，最低為零。缺少可信讀值時為 `null`。 |

worker 剩餘配額須計入執行中的保留額，以及仍占空間的歷史日誌和產物。同一筆使用量和保留額在各自的計算中只扣一次。實作若無法區分已使用與尚未使用的保留量，應保守估算並在診斷標記。

## 接單判定

1. 驗證任務的平台、映像和資源需求。舊任務描述未填資源時套用節點的預設需求。其中 RAM 初始值沿用目前 Docker 的 128 MiB 上限，磁碟預設值由節點設定。
2. 在同一接單鎖內重新檢查名額、量測結果與單任務上限。全部符合後，同時保留名額及資源，再啟動容器。診斷讀值只供觀察，接單以這次重新檢查為準。
3. 拒絕時回報穩定的原因和受影響的資源。第一版使用 `busy`、`unsupported_environment`、`insufficient_memory`、`insufficient_disk`、`task_limit_exceeded` 和 `resource_unavailable`。必要資源量測未知或過期時回報 `resource_unavailable`。
4. 將接單保留量與執行環境的硬上限分別呈現。記憶體上限由 Docker 套用。磁碟硬上限須由支援的儲存後端實施，日誌及產物另有限量。任務結束並確認清理後釋放相應保留額，留下的檔案繼續佔用配額。

## Arch 第一階段實作順序

目前 Arch 的遠端接單已檢查固定建構需求。每筆以 128 MiB RAM、16 MiB 暫存、16 MiB 狀態資料與 128 MiB 容器儲存作為接單估算。暫存涵蓋輸入快照及收集產物時的有界封存檔。Docker 和 containerd 共用檔案系統時，容器儲存需求只算一次。檔案系統有多種用途時，需求相加並採用其中最大的系統保留量。單一執行名額在任務與結果回傳期間持續占用。

### 輸入、日誌與產物的大小限制

本機輸入快照最多 64 個檔案，UTF-8 文字總量最多 4 MiB，超出時在配置任務目錄前拒絕。SSH 派工另外維持整份請求 1 MiB 上限。

執行器直接擷取容器輸出，`build.log` 最多寫入 1 MiB。超出時停止容器、保留已寫入的日誌，回報 `failed` 與 `log_limit_exceeded`。此執行器建立的容器使用 `--log-driver none`，日誌保存於任務結果目錄。

單一產物最多 8 MiB。收集時的暫存封存檔最多 8 MiB 加 64 KiB tar 開銷，輸出超限便停止傳輸並回報 `artifact_limit_exceeded`，因此過大產物會在完整下載前中止。產物本身仍需通過檔名、普通檔案類型與內容大小驗證。

目前實測 Arch 的 Docker 使用 containerd snapshotter，資料所在 ext4 掛載未啟用 project quota。容器工作區寫入量仍由接單估算與資源監看觀察，工作區硬配額的儲存方案待實作。日誌、輸入及產物收集上限已各自驗收。

| 接收端環境變數 | 預設系統保留量 |
| --- | --- |
| `OPENBUILDNET_MEMORY_RESERVE_BYTES` | 256 MiB RAM |
| `OPENBUILDNET_PERSISTENT_DISK_RESERVE_BYTES` | 每個持久儲存檔案系統 1 GiB |
| `OPENBUILDNET_SNAPSHOT_DISK_RESERVE_BYTES` | 暫存檔案系統 64 MiB |

環境變數的值以 byte 為單位。上述磁碟需求是接單估算，執行期間的磁碟寫入上限待完成。128 MiB RAM 與零 swap 額度已在 Arch 核對 cgroup，並在 Arch 與 Windows Docker 通過 OOM 及清理後恢復執行的驗收。Windows 接收端沿用原接單行為。

### 執行中的資源監看

Arch 遠端任務執行時，透過 PSI、容器 cgroup 事件及週期量測監看 RAM 與各檔案系統，持續到執行器完成容器清理。主機低於系統保留線時回報 `pressure`，接近保留線時回報 `warning`，必要讀值失效時回報 `unknown`。讀值恢復時回報 `healthy`，接單仍需通過恢復觀察期。目前的動作是告警與拒絕資源不足的新任務，既有任務仍受容器原有的 RAM 和時間限制。

`diagnostics` 的 `resource_health` 表示本次查詢量到的系統餘裕，`admission.accepting` 表示名額和新任務估算需求都符合。`available` 繼續只表示名額。可能出現 `healthy` 且有名額，但資源只夠維持系統保留量、無法再容納新任務，此時 `admission` 仍回報拒絕原因。

執行中的 `active_task.resource_monitor` 提供最近一次監看時間與狀態，以及去除連續重複狀態的事件清單。任務完成後同一份報告存入執行節點的 `result.json`，並回傳提交端。監看寫入或量測發生錯誤時，在最終報告記錄 `error` 與 `unknown` 狀態。

可用 Linux 的 `monitor` 命令或附帶的 systemd 使用者服務提供空閒期間的監看，診斷中的 `worker_monitor` 回傳其最近報告。報告超過完整核對間隔加 10 秒仍未更新時標為 `stale`，新診斷與接單仍會另取新讀值。多任務資源保留與磁碟寫入硬上限仍各自驗收。

監看採用事件通知搭配週期量測。Linux 可透過 PSI 門檻通知提早發現記憶體或 I/O 阻塞壓力，透過容器 cgroup 的 memory.events 得知限制與 OOM 事件。事件觸發後重新量測實際餘裕，接單仍在鎖內檢查。[Linux PSI 文件](https://docs.kernel.org/accounting/psi.html) · [Linux cgroup v2 文件](https://docs.kernel.org/admin-guide/cgroup-v2.html)

### 取樣排程與設定

以下排程已接入 Arch 接收端及監看服務，間隔可由節點管理員調整。

| 觸發條件 | 初始排程與處理 |
| --- | --- |
| 啟動、接單及任務結束 | 重新量測相關資源。接單判定在鎖內完成。 |
| 記憶體壓力、I/O 壓力或容器超限事件 | 立即安排量測，合併密集通知，並保留事件計數。 |
| 任務執行中 | 每 10 秒輕量檢查 RAM 與磁碟剩餘容量，避免每輪都重新查詢 Docker 資料目錄。 |
| 接近保留線或已發生壓力 | 縮短到每 2 秒量測，並暫停資源不足的新任務。 |
| 無任務且事件介面正常 | 每 15 分鐘完整核對資源與事件訂閱，允許配置為 30 分鐘。 |
| 事件介面不可用或回報錯誤 | 明確標記降級，恢復較短的週期量測，初始為每 10 秒。重新連接成功後再恢復正常排程。 |

15～30 分鐘的完整核對負責補查狀態及重新驗證訂閱。即使長時間沒有事件，也要依排程執行。執行中的短週期容量檢查持續核對磁碟與 RAM 保留線，因為 I/O 阻塞事件和剩餘容量代表不同的量測。

事件介面若失效但沒有回報錯誤，執行中的容量檢查仍會持續運作，完整核對則嘗試重建訂閱。接收端必須記錄監看方式、最後量測時間、最後訂閱檢查時間與降級原因。單純沒有收到事件也可能表示系統健康，不能據此宣告事件介面失效。

資源恢復後，以連續有效讀值及可配置的恢復餘裕、觀察時間確認穩定，再開放接單，避免門檻附近反覆開關。驗收漏事件、事件風暴、訂閱中斷、無事件時的定期核對，以及低資源後恢復的排程。

目前恢復餘裕預設為各保留量外再多 64 MiB，恢復觀察期為 30 秒。`peer-health.sqlite3` 保存停接與恢復判定，讓不同 SSH 請求與監看服務共用狀態。監看報告包含 `mode`、`subscription_errors`、`event_sources`、`event_counts` 和 `last_subscription_check`。健康狀態轉換保留最近 128 筆，超出數量記為 `events_dropped`。

| 接收端與監看服務的環境變數 | 預設 |
| --- | --- |
| `OPENBUILDNET_MONITOR_ACTIVE_SECONDS` | 10 秒 |
| `OPENBUILDNET_MONITOR_PRESSURE_SECONDS` | 2 秒 |
| `OPENBUILDNET_MONITOR_FULL_SECONDS` | 900 秒，可設為 1800 秒 |
| `OPENBUILDNET_MONITOR_EVENT_SECONDS` | 密集事件的量測間隔下限 1 秒 |
| `OPENBUILDNET_MONITOR_RECOVERY_SECONDS` | 30 秒 |
| `OPENBUILDNET_MONITOR_RECOVERY_MARGIN_BYTES` | 67108864 bytes |
| `OPENBUILDNET_MONITOR_EVENTS` | 1，設 0 時改用週期量測 |

接收入口與 systemd 服務需使用一致的保留量和恢復設定。輕量量測沿用已辨識的儲存路徑，完整核對時重新查詢 Docker 路徑並重建事件訂閱。短命容器可能在 cgroup 訂閱前就結束，最終 OOM 判定仍採用 Docker 回報的 `OOMKilled`。

1. **設定接單保留量。** 每個檔案系統各設定系統保留量。Arch 的根檔案系統可先用 1 GiB 作為試行值，同一檔案系統只扣一次，`/tmp` 獨立計算。RAM 保留量由節點管理員配置。接單只採計 `MemAvailable`，swap 剩餘量可另列供診斷。
2. **設定容器的記憶體硬上限。** 第一版維持預設 RAM 128 MiB，將 `--memory-swap` 設為與 `--memory` 相同，讓任務容器的 swap 額度為零。主機維持原有 swap 設定。驗收時核對 Linux cgroup v2 的 `memory.max` 與 `memory.swap.max`。若節點無法強制套用必要限制，回報資源不可用。
3. **接上拒絕與清理流程。** 接單鎖內確認資源及名額。以模擬低 RAM、根檔案系統不足和暫存空間不足的方式驗證拒絕，保留主機可用資源。在受限容器內觸發記憶體超限，以 Docker 的 OOM 狀態辨識失敗原因，並驗證清理後可再接單。
4. **補執行期間的主機保護。** 主機餘裕低於保留線時暫停接單，恢復後再開放。工作區磁碟硬上限與資源不足改派各自驗收，診斷需顯示目前後端是否支援強制限制。

## 驗收

- Arch 和 Windows 分別核對 RAM 與磁碟的量測來源。涵蓋共用檔案系統與 Docker Desktop 虛擬磁碟的限制。
- 名額仍空閒時，分別模擬 RAM 不足、磁碟不足、超過單任務上限與量測失效，核對拒絕原因。
- 以並行提交驗證保留額及名額不會重複分配，並核對完成或失敗後的容量。
- 驗證舊任務仍可使用預設需求，診斷欄位能顯示實測值、配置值與未知狀態。
