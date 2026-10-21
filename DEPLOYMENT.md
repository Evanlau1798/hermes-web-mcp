# Windows 重建與遷移

這份 runbook 重建 ChatGPT Web → 私人 Tunnel → Hermes 工具的環境，包括 Codex Desktop 橋接、Computer Use、共用記憶介面及登入啟動。官方原始碼、依賴與工具由 `deploy/versions.json` 指定的公開來源和 lockfile 還原；自訂實作在此 repo。

本庫不保存金鑰、登入憑證、私人外掛／Tunnel ID、人設、記憶、對話或個人專案。CLI／Gateway 的模型、Discord、語音及其他服務需另外配置，不屬於 Web MCP 執行鏈。

## 檔案與配置邊界

| 相對位置 | 來源／用途 |
| --- | --- |
| `<home>/hermes-agent/` | 官方 commit `d923a5eb487a17c0a68801bba9ef0f56c81040a8`（v0.22.2） |
| `<home>/web-mcp/` | 此 repo；資料夾可改名，父目錄仍須是 Hermes home |
| `<home>/tools/`、`installs/` | 官方 PM 依 `pm/lock.json`、`uv.lock` 還原，不直接複製舊 venv |
| `<repo>/runtime/` | 本機 Tunnel binary/profile、routing UUID、DPAPI key、健康網址、回執和 log；全數忽略 |
| `<home>/config.yaml` | 本機工具設定；範例在 `deploy/hermes-tools.example.yaml` |

`runtime.py` 從自己的檔案位置決定 repo 與父目錄，將 `HERMES_HOME` 設為該父目錄，保留既有目錄／ACL 檢查，不 bootstrap、不初始化人設。搬移時保留此結構；不要把其他 home 的私人設定帶進 repo。

可重現版本為 Windows x64、Python 3.14.7、MCP SDK 2.0.0、jsonschema 4.26.0、PM cua-driver 0.21.0；Node CLI 無 npm 依賴。完整版本與公開 Tunnel hash 見 `deploy/versions.json`。Desktop 內部 IPC 更新後需另外驗證。

## 1. 取得原始碼與依賴

先安裝下列命令列工具並開啟 PowerShell 7；不要求特定磁碟或 Windows 帳號。

| 工具 | 官方來源 | 已驗收版本 |
| --- | --- | --- |
| PowerShell 7 | [PowerShell releases](https://github.com/PowerShell/PowerShell/releases/latest) Windows x64 MSI／ZIP | 7.6.5 |
| Git | [Git for Windows](https://git-scm.com/download/win) | 2.40.0；PM 另固定 2.53.0 |
| GitHub CLI | [CLI releases](https://github.com/cli/cli/releases/latest) Windows amd64 MSI／ZIP | 2.87.3 |
| Node.js | [Node.js 發行版](https://nodejs.org/dist/v22.17.0/) Windows x64 MSI／ZIP | 22.17.0 |

執行 `pwsh --version`、`git --version`、`gh --version`、`node --version` 確認 PATH。選用不同受支援版本時記錄版本並完成驗收。非預設 Node／PowerShell 安裝可設定 `HERMES_MCP_NODE`／`HERMES_MCP_PWSH` 為現有執行檔的絕對路徑；不包含引號或 shell 參數。無效明確設定會拒絕操作，不偷偷使用其他安裝。terminal adapter 只有在未指定 PowerShell 且 PATH 找不到 `pwsh` 時，才嘗試 `%SystemRoot%/System32/WindowsPowerShell/v1.0/powershell.exe`；launcher 必須使用 PowerShell 7。

以可讀取私人 repo 的帳號執行 `gh auth login --hostname github.com --git-protocol https --web`，再用 `gh auth status` 確認；不把 token 貼進文件。以下只適用新機空目錄。替換 `OWNER/REPOSITORY`，`$homePath` 是可自行選擇的範例位置：

```powershell
$homePath = Join-Path $env:LOCALAPPDATA 'HermesTools'
$repo = Join-Path $homePath 'web-mcp'
$source = Join-Path $homePath 'hermes-agent'
New-Item -ItemType Directory -Path $homePath -Force | Out-Null
gh repo clone OWNER/REPOSITORY $repo
git clone https://github.com/NousResearch/hermes-agent.git $source
git -C $source checkout --detach d923a5eb487a17c0a68801bba9ef0f56c81040a8
$env:HERMES_HOME = $homePath
$env:HERMES_RUNTIME_DIR = Join-Path $homePath 'tools'
$env:UV_CACHE_DIR = Join-Path $homePath 'cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $homePath 'bootstrap/python'
$env:TEMP = $env:TMP = Join-Path $homePath 'tmp'
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
& (Join-Path $source 'setup-hermes.ps1') -RuntimeOnly
```

每個原生命令退出碼必須為 0 才執行下一步；安裝失敗不改用全域 pip。setup 使用官方工具 hash，可能下載依賴。接著啟用已安裝的 PM Python，補足 extras：

```powershell
$versions = Get-Content -LiteralPath (Join-Path $repo 'deploy/versions.json') -Raw | ConvertFrom-Json
$python = Join-Path $homePath ('tools/python-{0}-win32-x64/python.exe' -f $versions.python)
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw '請改用此次 PM 實際安裝的 Python 絕對路徑' }
$env:PYTHONPATH = $source
$env:HERMES_MCP_PYTHON = $python
& $python -m pm.cli install --extra mcp --extra computer-use
Set-Location -LiteralPath $repo
```

`HERMES_MCP_PYTHON` 是 CLI send/resume 使用的 Python 執行檔；未設定時使用 PATH 的 `python`，不猜版本目錄。新 shell 需重新設定，或由部署者在本機使用者環境保存。Tunnel profile 另記錄下節產生的完整 command，不假設 YAML 會展開環境變數。

## 2. 準備工具用 home 與技能

只在全新 home 建立最小配置；保留既有配置，不整份覆蓋：

```powershell
foreach ($name in @('cron','sessions','logs','logs/curator','memories','pairing','hooks','image_cache','audio_cache','skills')) {
    New-Item -ItemType Directory -Path (Join-Path $homePath $name) -Force | Out-Null
}
$config = Join-Path $homePath 'config.yaml'
if (-not (Test-Path -LiteralPath $config)) {
    Copy-Item -LiteralPath (Join-Path $repo 'deploy/hermes-tools.example.yaml') -Destination $config
}
```

範例是合法 YAML 的 JSON 形式，只開啟 local terminal 與 standard Computer Use。不要將 `.env`、SOUL、私人 JSON 放進 repo。把 repo 的 `skills/` 按相同相對路徑安裝至 `<home>/skills/`；同名檔先比較，不直接覆寫。額外 Codex skills 由原有來源安裝，不匯出整個使用者目錄。

## 3. 設定本機 Codex 路由

在本機安裝、開啟並登入 Codex Desktop，取得一個確實存在的閒置對話 UUID 作為 App Tools caller routing context。不要沿用舊機 UUID 或偽造 ID。

```powershell
$runtime = Join-Path $repo 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'deploy/codex-context.example.json') -Destination (Join-Path $runtime 'codex-context.json')
notepad (Join-Path $runtime 'codex-context.json')
```

將 placeholder 換成本機 UUID。它只是路由背景，不是 Web session 或預設操作目標。入口驗證 JSON／UUID，缺檔或格式錯誤回 `CODEX_CONTEXT_REQUIRED`／`not-sent`，不找舊 bridge。格式正確不代表目標存在或閒置，部署者需在已知閒置對話驗證只讀 list/status；失敗不換 ID 或重送寫入。CLI 設定見 `codex-status/README.md`。

## 4. 安裝 Tunnel client 與私人連線

依 [官方 Secure MCP Tunnel 指南](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) 建立專用 Tunnel，關聯正確的 ChatGPT workspace。管理階段需要 Read／Manage；runtime key 只給 Tunnels Read／Use，不重用其他專案的 Tunnel 或工作佇列。

從 [官方 releases](https://github.com/openai/tunnel-client/releases/latest) 或 Platform 取得 Windows amd64 套件。精確還原時選 0.0.16 的 `tunnel-client-v0.0.16-windows-amd64.zip`，核對官方 SHA256SUMS 與 `deploy/versions.json` 的 archive／binary hash；新版需重新驗收。只將 binary、LICENSE、NOTICE 放在忽略的 `runtime/`。

產生本機 profile：替換 ID，將 Python 與 server 各自加引號後，以 JSON 字串編碼成有效 YAML scalar，支援空白與非預設安裝位置：

```powershell
$profile = Join-Path $runtime 'hermes-local.yaml'
if (Test-Path -LiteralPath $profile) { throw '已有本機 profile，請比較後手動更新，勿覆寫' }
$tunnelId = Read-Host 'Tunnel ID（不填 API key）'
if ($tunnelId -notmatch '^[A-Za-z0-9_-]+$') { throw 'Tunnel ID 格式不符，請核對平台資料' }
$command = '"{0}" "{1}"' -f $python, (Join-Path $repo 'server.py')
$template = Get-Content -LiteralPath (Join-Path $repo 'deploy/hermes-local.example.yaml') -Raw
$template = $template.Replace('REPLACE_WITH_YOUR_TUNNEL_ID', $tunnelId)
$template = $template.Replace('"REPLACE_WITH_QUOTED_PYTHON_AND_SERVER_COMMAND"', (ConvertTo-Json -InputObject $command -Compress))
[IO.File]::WriteAllText($profile, $template, [Text.UTF8Encoding]::new($false))
& (Join-Path $repo 'save-key.ps1')
```

保留 `api_key: env:CONTROL_PLANE_API_KEY`，不在 YAML 填金鑰。`save-key.ps1` 隱藏輸入並以目前 Windows 使用者的 DPAPI 加密、限制 ACL；新機／新帳號需重新輸入，不搬舊 DPAPI 檔。key 不進 WMI 或子程序命令參數。`health.url` 相對 repo 根目錄，由 `start.ps1` 的 WorkingDirectory 固定。

## 5. 啟動、健康檢查與 ChatGPT Web

```powershell
& (Join-Path $repo 'start.ps1') -IfNotRunning
$healthUrl = (Get-Content -LiteralPath (Join-Path $runtime 'health.url') -Raw).Trim().TrimEnd('/')
Invoke-WebRequest "$healthUrl/readyz" -TimeoutSec 10
```

健康檢查需回 200；PID／啟動回執只證明程序存在。`health.url` 隨程序更新，不固定埠。失敗先查 `runtime/launcher.error.log` 與程序狀態，不反覆啟動相同 Tunnel。WMI 隱藏 launcher 在目前帳號下啟動，保留路徑核對、mutex 與來源 Job 隔離；只額外傳遞呼叫端的 PATH 與 `HERMES_MCP_*` 執行檔設定，不將 API key 放進 WMI 命令列。

到 ChatGPT Plugins → Add custom MCP server → Connection: Tunnel，選專用 Tunnel，名稱 `Hermes`，保持私人、不分享。身分驗證沿用 Tunnel，不另建 OAuth；工具選 Allow all，產品端若仍確認，不藉此移除本機防護。

外掛簡介：

> Hermes Harness 是 ChatGPT Web 的本機工具框架。處理 Hermes、Codex、Windows 或網站任務時，先 hermes_context(cwd,task)，依 harness.steps 與 prerequisites.pending 完成閱讀，後續帶回 bootstrap_id；換任務或 cwd 重新初始化。按 context 各主題 guide 與 workflow 技能索引，只讀本次適用 references。網站用 browser_use：完整讀目前版本官方 guide，再 run；截圖前讀 mcp-images 範例。長工作沿 session_id 查 hermes_process_read。修正與已驗證解法依 skill_updates.guide 維護技能。本機原生 Codex 使用自己的工具，僅依人類明確要求調用 Hermes。

新增工具後若 context 已顯示 browser_use，但 Web 無法發現可呼叫的工具，到現有 Hermes 外掛的 Manage → Refresh tools 同步目錄，核對只新增 browser_use，再開獨立 Web 對話驗證；沿用原有連線與權限。

在新 Web 對話先 `hermes_context(cwd,task)`，確認 27 個工具包含 prepare／confirm／operation_status、process_read、computer_use、browser_use。每個後續工具皆帶回 bootstrap.bootstrap_id；新任務或改目錄時帶舊 ID 重新初始化，改用新 ID。MCP tools/list 的 schema 也會標示必填欄位；舊客戶端同步工具清單後再測。檔案與程序測試使用獨立 fixture；記憶測試使用可辨識條目並刪除；Computer Use 使用獨立測試視窗。Codex 先只讀 list/status，控制只用人類另行授權的測試對話，不用原工作或 Pro 模型。

Bootstrap 驗收另測：同一 transport 的兩個 metadata-free context 取得不同 ID，省略 ID 不可沿用先前狀態；跨 metadata session 的 ID 被拒絕；重新初始化後舊 ID 失效。使用測試 AGENTS.md 及高風險技能驗證 pending → 連續讀完 → execution_ready，再切換任務確認 pending 重設。ID 是流程關聯，沒有擴大本機權限；未讀 references、是否理解指引及是否主動重初始化仍須觀察 Agent 實際行為。

browser use 另需同一 Windows 帳號已安裝並初始化 Ego Lite。執行檔依 `HERMES_MCP_EGO_BROWSER`（有效絕對 `.exe` 路徑）、PATH、使用者目錄下 `.local/share/ego/active_version_dir/ego-browser.exe` 尋找，無需固定版本目錄。launcher 會轉送此非機密設定。依官方指南操作獨立 TaskSpace，測試 guide、開頁、快照、點擊、繁體中文填寫與截圖；原有 MCP 新增一個工具即可，不另建 Tunnel。執行中的腳本留在忽略的 `tmp/browser-use/`，正常完成並查詢後清除；結果不明先查程序，不重新執行腳本。MCP 異常退出留下的目錄僅能在確認相關操作已停止後清除。

相容的手動 `codex_confirm_send` 仍要求人類已明確選擇，`confirmed_by_user` 是代理對該選擇的聲明，不是身分憑證／宿主批准。預設 Web 傳訊使用 prepare_send 自動流程，不另發 Compact 選擇詢問或 elicitation。送達以回傳的 `delivery_operation_id` 查證，不自行組合 ID。

## 6. 登入自啟動

在 PowerShell 7、repo 根目錄產生目前帳號的 Startup VBS。repo 內的 `Hermes_Local_Tools.vbs` 只適合留在 `start.ps1` 旁執行；不能直接複製到 Startup。

```powershell
$repo = (Get-Location).Path
$launcher = (Resolve-Path -LiteralPath (Join-Path $repo 'start.ps1')).Path
$pwsh = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $pwsh -PathType Leaf)) { throw '請使用 PowerShell 7 執行' }
$vbs = 'Option Explicit' + "`r`n" + 'CreateObject("WScript.Shell").Run """' + $pwsh + '"" -NoProfile -NonInteractive -WindowStyle Hidden -File ""' + $launcher + '"" -IfNotRunning", 0, False'
$entry = Join-Path ([Environment]::GetFolderPath('Startup')) 'Hermes_Local_Tools.vbs'
[IO.File]::WriteAllText($entry, $vbs, [Text.Encoding]::Unicode)
```

這是登入後自啟動，不是未登入即啟動的服務；部署磁碟需已可用。搬移 repo／PowerShell 後需重新產生此本機入口。其他非預設執行檔設定需存在於登入使用者的環境；只在目前 shell 設定不會永久保存。停用時只移除 Startup entry，不刪私人 runtime。`-IfNotRunning` 保留相同路徑的既有程序，預設手動啟動拒絕 duplicate。

## 7. 驗證與更新

在 repo 根目錄執行，先設定 `$env:HERMES_MCP_PYTHON` 為上述 PM Python：

```powershell
New-Item -ItemType Directory -Path .\tmp -Force | Out-Null
& $env:HERMES_MCP_PYTHON -m unittest discover
node --test test_codex_input.cjs codex-status/test.cjs codex-status/test_diagnostics.cjs codex-status/test_cli.cjs
& $env:HERMES_MCP_PYTHON smoke.py
```

測試使用合成訊息、暫時管道及可丟棄程序，不控制真實 Desktop session。`smoke.py` 另啟本機 stdio，不更新 Web 工具快取；本機通過不等於 Web／登入自啟動驗收。純路徑修正不需要重啟 Desktop、Gateway 或其他服務。

本機設定、DPAPI、profile、`.env` 與 override 值不可提交。重載專用 Tunnel 前核對呼叫、背景程序、`runtime/compact-jobs.sqlite`，有 compacting／sending 時不要重啟。只停止 `runtime/tunnel.pid` 且 ExecutablePath 符合本入口的程序，再啟動；不碰其他 Tunnel。schema 變更才需刷新 Web 工具清單。Desktop 登入切換後先 status，context 失效則更新本機 JSON，不重送結果不明的操作。

更新 browser use 導引後，將 repo 的 hermes-web-mcp-workflows 技能經原生 skill_manage 精確更新至本機並讀回核對，再重載專用 MCP。工具描述快取仍舊時在現有外掛 Refresh tools；不另建連線或修改權限。獨立 Web 驗證使用可丟棄、跨網站概念的測試頁：延遲主內容、導航已生效但 load 逾時、引用／回覆先於主欄位。確認有界等待、同 Space 恢復、未重送動作，以及主實體欄位來源正確；結束釋放 Space，移除暫存測試服務。成功瀏覽不等同所有擷取欄位正確，逐項保留回執證據。

同步技能時以 skill_manage 精確 patch 核心 SKILL.md，並安裝或更新其連結的全部 references（browser、mcp-images、processes、codex-send、tracking、skill-learning），逐份讀回與 repo 核對。context 的 guide 僅是 hermes_skill_read 參數；Web Agent 依本次操作載入，切換任務重新 bootstrap。官方 Ego guide 仍須完整讀取至結尾，不以摘要取代。圖片驗收先取得截圖回傳並在同一次 Web 宿主執行顯示，實際讀圖後才另呼叫 finish；同時檢查原生圖片區塊、helper 執行與可見結果。只產生路徑／image_count 或只在本機 stdio 測通，均不能代替 Web 視覺驗收。不以文字結果、公開 URL、額外端點或取消原防護繞過圖片通道。

本次 source 更新需在適當時機重載專用 Tunnel 才會套用；此文件不代表授權自動重載。搬移後另外更新忽略的 profile 中 Python／server 路徑及本機 Startup entry。私人資料如需搬移，使用另外的離線備份，禁止加入 Git。CLI／Gateway 可依固定官方版本重新 setup，登入入口保留 Scheduled Task／Startup fallback 其中一種避免重複。
