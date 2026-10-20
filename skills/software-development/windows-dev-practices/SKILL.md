---
name: windows-dev-practices
description: Windows 操作與 CLI 排錯的高效率工作方式.
platforms: [windows]
---

# Windows 操作與 CLI 排錯


使用情境：在這台 Windows 電腦操作 CLI、安裝或診斷工具、處理編碼/路徑/背景程序問題時載入。此技能不要求 Hermes 接手開發專案，也不維護專案清單。

## 使用者習慣

- 使用繁體中文對話，進度與結論簡潔、有可核實的證據。把觀測、推測與尚未驗證的範圍分開說明。
- 先讀已知設定、README 或既有 checkpoint，沿用有效的路徑、程序 ID 與檢查結果，不每次從頭探索。
- 先做最便宜且能區分原因的檢查；優先既有工具和最小修正，不為一次性操作安裝新依賴或建立複雜腳本。
- 保護使用者既有修改，不覆蓋無關檔案。任務真涉及程式碼時才讀該目錄 AGENTS、Git 狀態與相關驗證規範，不把一般 CLI 排錯變成專案開發流程。
- 使用者明確授權在原範圍持續有效。一般可逆操作不重複問；缺漏影響正確性、範圍或對外授權才詢問。
- Git 推送、部署、傳訊與付費服務仍依當次任務授權；技能不是常駐的對外操作授權。
- 不為排錯批次掃描憑證或私人對話；任務確實需要憑證時使用原有安全儲存，不印出值。

## PowerShell、路徑與文字

- 先確認工具實際使用的 shell。PowerShell 不直接套用 Bash heredoc、export 或 Unix 指令；只有確認安裝且需要時才用 WSL/Bash。
- 路徑有空白時，以 `& 'G:\Some Folder\tool.exe'` 執行；檔案操作優先 `-LiteralPath`，不把路徑字串串成另一層 cmd 指令。
- 優先使用可用的檔案編輯工具。PowerShell 寫檔指定 UTF-8；Windows PowerShell 5.1 與 PowerShell 7 的 BOM 預設不同，保留目標格式。
- JSON-RPC/stdin 需要無 BOM 時明確指定 UTF8Encoding(false)，勿讓 BOM 混入協定；暫改 Console 編碼後在 finally 還原。
- Python 管線需要中文時可對該程序設定 `PYTHONIOENCODING=utf-8`。先以小量內容驗證，不批次重編碼整個專案。
- PowerShell 雙引號會展開 `$變數`、`$()`；JSON.stringify 不是 shell escaping。含金鑰或使用者文字時優先結構化參數或資料檔，避免拼接命令。
- rg 用 `rg --files` 找檔、`rg -n -- 'pattern' '完整路徑'` 找文字；檔案篩選使用 `-g`。向原生程式傳多個檔案時確保是陣列 `@(...)`，單一字串被 splat 可能拆成字元；不要在輸入路徑錯誤時意外搜尋整個工作目錄。
- 批次獨立讀取；依賴、修改與共用輸出依序執行。控制輸出範圍，發現截斷後縮小讀取，不能宣稱已讀完。
- 查 CLI 不存在、參數或版本問題時，先用 `Get-Command <工具> -ErrorAction SilentlyContinue`、確定的執行檔路徑與必要的 `--help`/`--version`。不要在已知參數不相容後原樣反覆重試。
- Python 優先使用已確認的 `.venv\Scripts\python.exe`；套件命令用該 Python 的 `-m pip`，避免 PATH 的 Python/pip 指向不同環境。不因 import 失敗就全域安裝。
- 多行 JSON、PR 內容或複雜命令資料優先 UTF-8 檔與工具支援的 file 參數，避免多層引號。不要用常見系統變數如 HOME/CODEX_HOME 當臨時變數。

## 程序與診斷

- 區分原始碼目錄、安裝目錄、執行檔、實際程序與連接埠。程序名稱或檔案存在不能證明服務健康。
- 背景 helper 使用 `Start-Process -WindowStyle Hidden`；只把父 PowerShell 設為 Hidden 不能確保子主控台隱藏。需要時將 stdout/stderr 導向專案 log，保留 PID 與退出碼。
- 以確定的 PID、ExecutablePath、CommandLine 與監聽連接埠確認目標，再停止或重啟；不要依模糊名稱批次殺程序。
- wrapper timeout、沒有輸出、陳舊狀態或子代理逾時都不等於程序崩潰。沿用既有 process ID，讀取有限的 log 與退出資訊後判斷。
- 已有工作在跑就等待或讀它的結果，不重複啟動；輪詢沒有變化時拉長間隔。單次失敗先查退出碼、stderr、cwd、runtime 與輸入，不先重裝或重啟全部服務。
- 連線問題先確認監聽 PID、協定、實際路徑及狀態碼：401 表示認證未滿足，404 表示該路徑不存在；兩者都不能單獨證明整個服務停止。以任務相關的端點做最小檢查，勿把 token 印進命令/輸出。
- Windows 登入後啟動與開機未登入即啟動不同。排程註冊成功、手動啟動成功及重新登入成功是三份不同證據。
- 遞迴刪除或移動前確認解析後的絕對路徑落在指定範圍；同一 shell 完成路徑驗證與操作。
- Hermes 新工具、cache 與暫存放在部署者指定的 Hermes home；不任意改動其他專案既有配置。

## 節省驗證時間

- 行為新增與缺陷修復採 RED → 最小 GREEN → 必要重構；先跑受影響測試。純文件不製造測試。
- 建置與 unit tests 分開，前一個程序完成後才執行下一個，避免 Windows 共用輸出與鎖檔；Android Gradle 同樣適用。
- 既有證據在輸入、環境與程式未變時可重用。新的 commit 或 reviewer 本身不要求重跑全部測試；實際變更、失敗與明訂門檻才擴大驗證。
- 本機測試、UI 操作、部署與真機/多人驗收分別報告，不用 mock 或局部成功代替未測邊界。
- 僅在專案既有 `.codegraph/` 時優先使用可用的 CodeGraph；不可用或不足時改用定向 rg，不自行建索引。
- 真有程式修改時才依該專案規範提交與審查；只 stage 本次檔案，遵守 ignore，英文 conventional commits，禁止推送 codex/*。
- 暫時工作放該專案忽略的 tmp/；只讀一次的檔案用後移除。工作結束說明成果、證據及未驗證事項。
- 需要獨立 reviewer 時僅用 Hermes 當下可用且已授權的機制；不可把自我檢查稱為獨立通過，亦不可假設 Codex 的派工工具在 Hermes 存在。

## 本機 Hermes

- 此 Web MCP 的 HERMES_HOME 為 repo 父目錄，官方 checkout 為其中的 `hermes-agent/`。先啟用 PM dependency environment，不 import hermes_bootstrap。
- 人設與私人記憶另由本機管理，不由工具入口載入。
