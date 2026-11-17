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

目前 Arch 的遠端接單已檢查固定建構需求。每筆以 128 MiB RAM、4 MiB 輸入暫存、16 MiB 狀態資料與 128 MiB 容器儲存作為接單估算。Docker 和 containerd 共用檔案系統時，容器儲存需求只算一次。檔案系統有多種用途時，需求相加並採用其中最大的系統保留量。單一執行名額在任務與結果回傳期間持續占用。

| 接收端環境變數 | 預設系統保留量 |
| --- | --- |
| `OPENBUILDNET_MEMORY_RESERVE_BYTES` | 256 MiB RAM |
| `OPENBUILDNET_PERSISTENT_DISK_RESERVE_BYTES` | 每個持久儲存檔案系統 1 GiB |
| `OPENBUILDNET_SNAPSHOT_DISK_RESERVE_BYTES` | 暫存檔案系統 64 MiB |

環境變數的值以 byte 為單位。上述磁碟需求是接單估算，執行期間的寫入上限與 RAM／swap 強制限制驗收仍待完成。Windows 接收端沿用原接單行為。

1. **設定接單保留量。** 每個檔案系統各設定系統保留量。Arch 的根檔案系統可先用 1 GiB 作為試行值，同一檔案系統只扣一次，`/tmp` 獨立計算。RAM 保留量由節點管理員配置。接單只採計 `MemAvailable`，swap 剩餘量可另列供診斷。
2. **設定容器的記憶體硬上限。** 第一版維持預設 RAM 128 MiB，將 `--memory-swap` 設為與 `--memory` 相同，讓任務容器的 swap 額度為零。主機維持原有 swap 設定。驗收時核對 Linux cgroup v2 的 `memory.max` 與 `memory.swap.max`。若節點無法強制套用必要限制，回報資源不可用。
3. **接上拒絕與清理流程。** 接單鎖內確認資源及名額。以模擬低 RAM、根檔案系統不足和暫存空間不足的方式驗證拒絕，保留主機可用資源。在受限容器內觸發記憶體超限，以 Docker 的 OOM 狀態辨識失敗原因，並驗證清理後可再接單。
4. **補執行期間的主機保護。** 主機餘裕低於保留線時暫停接單，恢復後再開放。工作區磁碟硬上限與資源不足改派各自驗收，診斷需顯示目前後端是否支援強制限制。

## 驗收

- Arch 和 Windows 分別核對 RAM 與磁碟的量測來源。涵蓋共用檔案系統與 Docker Desktop 虛擬磁碟的限制。
- 名額仍空閒時，分別模擬 RAM 不足、磁碟不足、超過單任務上限與量測失效，核對拒絕原因。
- 以並行提交驗證保留額及名額不會重複分配，並核對完成或失敗後的容量。
- 驗證舊任務仍可使用預設需求，診斷欄位能顯示實測值、配置值與未知狀態。
