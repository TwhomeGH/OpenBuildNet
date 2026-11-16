# OpenBuildNet

跨平台建構與測試的例子見[應用場景](Docs/UseCases/README.md)。

## 單節點開發測試

從 repo 根目錄執行以下命令。首次建置需要能下載 Alpine 基礎映像，Dockerfile 以 digest 固定基礎版本。

```powershell
docker build -t openbuildnet-dev:v0.1 docker/dev
pwsh -File scripts/Test-SingleNodeDocker.ps1
```

驗證腳本預設使用本機的 `openbuildnet-dev:v0.1`，每次建立獨立的測試容器並停用容器網路。測試完成後可從輸出的目錄查看日誌和產物。更新 Dockerfile 後，用同一條 build 命令重建映像。

VS Code 任務與結果排查見[開發手冊](Docs/Development.md)。
