# Hermes 私人 MCP 使用指南

Hermes MCP 預設供 ChatGPT Web 使用。Web 對話涉及 Hermes、Codex 或 Windows 本機操作時，先呼叫 `hermes_context`，再依任務選擇工具；ChatGPT 負責推理，Hermes 提供本機工具。

本機原生 Codex 預設使用自己的檔案、終端、瀏覽器及對話工具；只有人類使用者明確要求使用 Hermes 整合時，才調用此 MCP。獲得此明確要求後，同樣先呼叫 `hermes_context` 初始化。以下操作指南適用於已選用本整合的對話。

## 開始任務

```json
{"cwd":"C:/Example/Project","task":"修復 Windows 命令結果的判讀"}
```

以上是合成路徑範例；cwd 改成實際專案的絕對路徑。`hermes_context` 的 task 必填；未填 cwd 時使用 MCP repo 目錄，回執會列出實際目錄。先讀 harness.steps，再讀 prerequisites.pending；workflow 的 SKILL.md 是核心與主題索引，只讀本次操作對應的 references。context 各領域的 guide 是 hermes_skill_read 參數，不需預讀其他領域；完整技能目錄由 skills_list 分頁展開，完整工具 schema 使用 MCP tools/list。

初始化回傳 `bootstrap.bootstrap_id`。後續每個 MCP 工具都帶同一個 `bootstrap_id`；切換任務或 cwd 時，以舊 ID 呼叫 `hermes_context(cwd,task,bootstrap_id)`，改用回傳的新 ID。舊 ID 隨即失效，技能／規範閱讀狀態重新計算，已啟動的背景工作不會因此被停止。更換 MCP 程序後重新初始化，沿原操作回執查證，不重送結果不明的寫入。

例如 task="準備 merge 上游最新版"，先依 workflow.next_step 讀取首選專用技能，再確認來源／目標、branch、dirty files、適用授權與驗證證據，最後執行專用流程。通用開發技能作為補充。對有候選技能的已宣告高風險任務，先連續讀完首選 SKILL.md 到 eof，寫入工具才會開始執行；讀取、搜尋仍可用。

每個 Web 對話分別初始化。有 Web session metadata 時，ID 綁定該 session；沒有 metadata 時，以本次顯式 bootstrap ID 選取獨立任務，不沿用 transport 狀態。ID 用於流程關聯，不替代 Tunnel 或 Windows 權限，也不要從其他對話複製 ID。重新初始化由 Agent 在新任務／改 cwd 時呼叫，server 不會自行讀取使用者訊息判斷任務切換。

`bootstrap.status=ready` 表示已初始化；`bootstrap.execution_ready`／`prerequisites.execution_ready` 表示前置條件是否完成。pending 列出技能、AGENTS.md 及必要搜尋；技能依 next_offset 讀到 eof，規範以 read_file 從 offset=1 連續讀至結尾，閱讀回執會更新清單。未完成時仍可唯讀探索，寫入會回傳 skill_read_required／instructions_read_required／skill_search_required。高風險任務沒有匹配技能時，以 pending 指定的 query 呼叫 skills_list（offset=0）；找到技能就先讀完，仍無匹配則記為 searched_no_match。

## 按需讀取 Skills

1. 使用 `hermes_skills_list`，以 query 搜尋任務相關技能。
2. 依 role、match_reason、source、scope、description 選擇技能，將原樣 id 傳給 `hermes_skill_read`。query 可填任務句、名稱或近似拼法，例如 merge-upstream-safety。
3. 需要分頁時指定 limit，依 next_offset 接續，讀到 eof 後開始工作。
4. 依 SKILL.md 指引，以相同 skill_id 和 file_path 讀取 references。

清單整合 Hermes、Codex user/system/agents，以及指定專案的 `.agents/skills`。程式碼任務先讀適用的 Codex 技能與專案 AGENTS.md，再使用 Hermes 工具修改、測試和操作程序。

技能內容變更使用 `hermes_skill_manage`，以 `operations` 呼叫原生 create、patch、write_file、remove_file、delete；不要用一般檔案 patch 代替。先讀原文，再提供精確的 old_string/new_string。回執保留原生驗證、lint、批次回滾與核准 staging；`approval_required` 代表尚待原生批准，並非寫入完成。Codex／專案技能仍由原有維護入口處理。

### 主動學習

`hermes_context.skill_updates.learning` 提供學習訊號，`skill_updates.guide` 指向按需讀取的完整維護規則。Web Agent 在收到人類對風格／流程的修正、找到已驗證可重用的解法，或發現已載入技能錯誤／過時時主動判斷更新，並在完成任務前再檢查一次。依序優先更新本次載入的 Hermes 技能、既有類別技能、主題支援檔；都不適用才建立類別技能。沒有新規則或已有等價內容就不製造寫入。

更新前重新完整讀取確切目標，搜尋去重，再以 `hermes_skill_manage` 精確 patch，成功後讀回核對。常用偏好放 SKILL.md，主題細節放 references 並補上入口；只提煉規則與原因，不保存整段對話、秘密、臨時故障或未驗證嘗試。自動學習不刪除技能，也不自動上傳本機技能。原生驗證、lint、回滾與核准機制照常生效；等待批准或結果不明時如實回報，不繞過防護或盲目重送。

這是由 ChatGPT Web 執行的任務指引，不另啟 Hermes／背景模型；是否產生更新取決於當次學習訊號。其他既有對話的凍結提示不會即時刷新。

## 檔案與 PowerShell

Browser 完成回執若只含原生尾端預覽，會從保留日誌補讀開頭最多 6,000 字元；`next_action` 指向原程序的下一段文字或狀態查詢。保留日誌不可用會明確標示，補讀不重新執行瀏覽器動作。`reading_metrics` 記錄本任務實際讀取次數、主題、回傳字元與重複次數，不保存正文，也不代表 token 或模型閱讀耗時；達統計界限會標示 bounded。

- `hermes_read_file`：讀取檔案；既有檔案準備整份覆寫時，先完整讀取。
- `hermes_search_files`：依名稱或內容搜尋。
- `hermes_write_file`：建立或整份寫入；檔案變更後重新讀取、合併再寫。
- `hermes_patch`：精確修改指定片段。
- `hermes_terminal`：預設 PowerShell 7，必要時使用 Windows PowerShell 5.1；以 shell=bash 選擇 Bash。

```json
{"bootstrap_id":"<本任務 bootstrap_id>","command":"Write-Output '繁體中文 English'","workdir":"C:/Example/Project"}
```

使用 UTF-8 中文輸出。明確 exit 優先；否則保留最後的原生命令非零退出碼，PowerShell 錯誤回傳 1。非零 shell 退出仍是正常 MCP 回執：outcome=shell_exit，依 exit_code 判讀；原生執行錯誤、參數錯誤與防護拒絕則回傳工具錯誤。

output 保留原生合併輸出，stdout 提供同值別名，streams_merged=true、stderr=null；依 timed_out 與 partial_output 判讀逾時和截短。長輸出保留頭尾，避免丟失回執欄位。automaticRetrySafe=false 表示先查證已有結果，再決定後續行動。rg 無匹配通常 exit_code=1，應按查詢語意判讀。Git CRLF 提示與命令錯誤保留在輸出中，回報時集中摘要一次。

工作目錄使用 workdir；已在 Bootstrap 指定 cwd 時，terminal 沿用該目錄作為預設。檔案工具使用完整路徑。

長命令設定 background=true，再以回傳 session_id 呼叫 `hermes_process_read` 查詢：

```json
{"bootstrap_id":"<本任務 bootstrap_id>","action":"poll","session_id":"<啟動回執的 session_id>"}
```

`hermes_process_read` 是唯讀入口：list 列出本入口的程序、poll 讀取狀態與輸出預覽、log 以 offset／limit 分頁讀取日誌。輸入、送出 Enter、關閉 stdin 或停止，使用 `hermes_process_manage` 的 write／submit／close／kill。原入口的 list／poll／log 仍相容，新的工作流程優先用唯讀入口輪詢。

需要短暫等候時，用 `hermes_process_manage(action=wait)`；每次等待最多 30 秒，回執的 wait_window_seconds 表示本次等待視窗，程序尚在執行時再查狀態。橋接忙碌時短暫等鎖後回報 bridge_busy、executionStarted=false，本次沒有排入延後執行；先查證已有工作，再決定後續操作，寫入不自動重送。前景長命令改用 background=true，避免佔住入口。

## 電腦操作

所有桌面操作共用一個 `hermes_computer_use`，以 action 選擇動作。

網站操作另用單一 `browser_use`（顯示名稱 **browser use**），沿用同一 MCP／Tunnel。先 `{"action":"guide"}` 讀本機 Ego 官方指南，再 `{"action":"run","code":"..."}` 執行 JavaScript；同一任務沿用一個 TaskSpace ID。Ego 每次執行都是新 Node.js 程序，JS 變數不保留。截圖用 `await page.screenshot({path: browserScreenshotPath})`，MCP 直接回傳 PNG。長工作以回執 `session_id` 使用既有 process_read 查詢，結束時回傳截圖；原生命令防護與程序所有權照常保留，結果不明不重送。此官方 JS runtime 具本機 Node.js 能力，並非瀏覽器沙箱。

網站操作依任務條件有界等待目標內容，不能只以 URL 或 load 判定就緒。逾時先查原程序與原分頁目前狀態，已達成就接續，仍載入則在總預算內等待；不盲目重播結果不明的動作。資料擷取逐欄核對同一主實體的來源，排除引用／回覆混入作者、時間或正文；來源矛盾標記未確認。完整流程見 [browser use 技能](skills/software-development/hermes-web-mcp-workflows/references/browser.md)，採跨網站規則，不保存特定網站 DOM。

Web 截圖顯示使用 [MCP 圖片範例](skills/software-development/hermes-web-mcp-workflows/references/mcp-images.md)：保留完整回傳，在同一次宿主執行取 `content_items`（或標準 `content` 陣列）的原生圖片，呼叫圖片 helper 並核對；之後再結束 TaskSpace。`structuredContent`／JSON 文字只含狀態，圖片不在文字日誌中；`image_count=1` 不代表已顯示。不新增圖片快取或讀檔接口。

| action | 使用方式 |
| --- | --- |
| list_apps / list_windows | 找到應用程式及視窗的 app_name、pid、window_id |
| capture | 以目標 app，或 pid + window_id 擷取畫面 |
| click / double_click / right_click | 使用當次畫面的 element 編號點擊 |
| type / key / set_value | 輸入文字、快捷鍵或設定元素值 |
| scroll / drag | 捲動、拖曳 |
| focus_app / wait | 選定目標應用程式、等待畫面更新 |

capture 的 mode=som 回傳畫面與元素編號，mode=vision 回傳畫面，mode=ax 回傳無障礙樹。以最近一次擷取結果的元素編號操作；capture_after=true 可直接取得操作後畫面，接續下一步。

常用流程：`list_windows → capture(mode=som) → element 操作 → capture_after`。

## Codex 對話

- `codex_list`：預設簡表，limit / offset 控制顯示；長標題以 title_preview 呈現。
- `codex_status`：取得完整名稱、即時 idle / active / notLoaded，以及最新 turn 狀態。
- `codex_read`：以 cursor、turn_limit、message_limit、max_chars 控制讀取；依 page.nextCursor 接續，並取得子代理 ID。
- `codex_send` / `codex_resume` / `codex_stop`：先由同一筆查詢複製 thread_id 與完整 expected_title，再執行使用者要求的操作。

傳訊 receipt 的 accepted 表示受理。依 turnBaseline 與 followUp，先確認新訊息／對應 turn 已出現，再以 codex_status 追蹤；完成時核對該 turn 的 completed 狀態。需要內容時再呼叫 codex_read。

send／resume 必填 `operation_id`，例如本次產生的 UUID。相同操作沿用同一 ID；即使連線中斷或 MCP 重啟，也只查回既有紀錄，不再派送。以 `codex_delivery_status` 查閱回執；unknown 時核對 status/read 與 turnBaseline，不能改 ID 盲目重送。只有新的人類操作才使用新 ID。重複 ID 不比對或保存訊息／標題內容指紋，只回傳原紀錄並標記 messageContentVerified=false，本次內容不派送。送達紀錄存於忽略的 `runtime/codex-delivery.sqlite`，不保存訊息或標題。

使用者要求傳訊後，Web Agent 對閒置對話以 `codex_prepare_send(thread_id, expected_title, message, operation_id)` 啟動自動流程。最新已完成 turn 不是原生 Compact、目前上下文嚴格大於 80,000 tokens，且最後完成時間距今嚴格超過 30 分鐘時，先原生 Compact，再傳原訊息。不額外提醒或詢問是否壓縮；未滿足全部條件則直接傳送。

`contextUsage.contextTokens` 取自本機原生用量紀錄中，與最新 turn ID 相符的 `last_token_usage.total_tokens`，不是歷史累計 `total_token_usage`。查詢僅讀取有界的紀錄尾段，對外不回傳對話內容。讀不到、紀錄不完整或 turn 不符時回傳 null，不猜測、不自動壓縮。`codex_confirm_send` 保留給人類明確指定的手動操作，相容既有客戶端。

啟動後立即回傳背景操作回執，使用唯讀 `codex_operation_status(operation_id)` 等待。受理不代表壓縮完成；背景只在新的原生 contextCompaction turn 成功完成、前一個 turn 正是準備階段的 baseline、目標仍閒置時，才傳送 RAM 中保存的原訊息。最後的 delivery receipt 仍以 accepted／unknown 等實際狀態解讀，用回傳的獨立 `delivery_operation_id` 查 `codex_delivery_status`，不另行 send。重複呼叫與查詢不重送；MCP 重啟不恢復尚未傳送的訊息，需重新檢查並由人類重新要求傳訊。

active session 保留正常傳訊行為。原生 Compact 派送前核對兩次 idle 與 baseline；Desktop 協定沒有原子 expected-turn 條件，無法保證其他客戶端不在兩次檢查後介入。不要同時操作同一目標。狀態不明、介入或逾時時不傳送原訊息。

list／status／read 失敗回傳 `stage`、`errorCode`、`requestId`、`observedAt`、`source`／`tool`、`requestState`、`deliveryState`、逾時／斷線旗標與建議處置。明確失敗、尚未送出與結果不明分開；底層私密錯誤只保留分類、可安全揭露的原生錯誤碼及指紋。notLoaded 不代表閒置，查詢失敗不回退成歷史快照。

## 持續追蹤

以 `hermes_tracking(action=start)` 啟動實際背景 worker，回傳 tracking_id。Codex 指定 kind=codex 與 thread_id；電腦指定 kind=computer 與精確 pid、window_id，僅擷取 AX 元素。預設每 30 秒查詢，可用 interval 調整；現有接口未提供可驗證的事件訂閱，因此採有界輪詢。

mode=manual 持續到 `action=stop`；mode=task_complete 在觀測到指定／正在執行 turn 完成時停止。Computer Use 的 task_complete 另提供明確的 `completion_predicate`，例如 `{"path":["elements",0,"label"],"contains":"完成"}`，依可見文字判斷，不猜測任務完成。可用 deadline_seconds 設期限。

使用唯讀 `hermes_tracking_status(tracking_id,after_sequence)` 取得心跳、alive、current_state、錯誤、結束原因與去重事件；after_sequence 接上一批最後一筆 sequence，missed_events 表示有事件超出緩衝區。停止回傳 stopping 且 alive=true 時仍有唯讀取樣尚未返回，後續查詢至 terminal。電腦事件只回報畫面變化，不保留或回傳私訊文字、圖片；需要內容時另行擷取指定視窗。

Worker 由長期運作的 MCP 程序持有，同一私人入口的新 Web 對話可帶 tracking_id 接續查詢。MCP 重啟會結束追蹤，須重新開始；此功能不建立排程、不主動向 Web 或 Discord 發訊，也不保證結束的 Web agent 自行醒來。Discord 背景快捷鍵支援由外部應用決定。

## 回執與觀察

程序日誌可用 log 的 char_offset／max_chars（最多 16000）分頁長行；依 log_page.next_char_offset 接續，以 content_id 核對內容。offset／limit 仍先選行，緩衝可能滾動，並非持久完整日誌。browser use 的 observation 和 log 提供截短資訊與回讀參數；timings_ms 僅量測本機準備、啟動及等待。外層輸出超限仍保留 error、exit_code 等結構化欄位。

追蹤 start 最多等待 25 秒取得 baseline，依 baseline_ready 判讀；status 加 wait_seconds=25 等事件、完成取樣心跳或結束，wait.reason=timeout 不停止 manual worker。持續觀察時於目前回合接續查詢，預設 120 秒觀察預算；結束回合須交代最後觀測與背景狀態。工作流程見 skills/software-development/hermes-web-mcp-workflows/SKILL.md。

Codex 背景操作先讀 summary：compact=not_required 表示跳過，delivery=accepted 只代表受理。status 加 observe_turn=true 可唯讀觀測 baseline 之後的新 turn；相關性僅為 newer_than_baseline_only，task_completion 仍需按任務結果核實。

## 共用記憶

`hermes_memory_read(target)` 讀取 memory 或 user 條目與容量。`hermes_memory_edit` 使用原生 add、patch、remove 或 operations 批次語意。先讀取，再精確更新長期偏好或事實。

## 連線與啟動

新機重建、版本、無密鑰設定範例、私人外掛與登入啟動流程見 [DEPLOYMENT.md](DEPLOYMENT.md)。Codex 底層實作位於 [codex-status/](codex-status/README.md)，本機路由設定放在忽略的 `runtime/codex-context.json`。自訂 Windows／Codex 技能快照位於 `skills/`。

在 ChatGPT 新增私人 custom MCP server，使用本機既有 Tunnel，工具權限選 Allow all。啟動專用 MCP Tunnel：

```powershell
pwsh -NoProfile -File .\start.ps1
```

以上命令在 repo 根目錄執行。PowerShell／Node 由 PATH 探索，非預設安裝可設 `HERMES_MCP_PWSH`／`HERMES_MCP_NODE` 為現有執行檔的絕對路徑；不填 shell 參數。PowerShell 7 未安裝時，terminal adapter 才使用 `%SystemRoot%` 下的 Windows PowerShell 5.1；launcher 仍要求 PowerShell 7。明確指定但無效的執行檔會拒絕操作。

Hermes Gateway 的本機操作入口（只有另外配置 Gateway 時才使用）：

```powershell
$env:HERMES_HOME = Split-Path -Parent (Get-Location).Path
& (Join-Path $env:HERMES_HOME 'bin/hermes.exe') gateway status
```

憑證由現有 DPAPI 檔載入；需要更新 runtime key 時使用 `save-key.ps1`。本機操作依原有 Hermes 執行與封鎖設定處理。

`start.ps1` 透過本機 WMI 在目前 Windows 使用者身分下隱藏啟動，再由獨立啟動程序讀取 DPAPI 金鑰。Tunnel 不再繼承 Codex 終端的程序 Job，避免因該 Job 清理而連帶結束；金鑰不會放入 WMI 命令列。隔離測試涵蓋來源 Job 被終止後的存活，不代表所有 Desktop 更新、網路或帳號情境都已驗收。啟動回執只確認新程序與 PID 檔一致，使用前仍須確認健康狀態。若啟動未確認，先檢查 `runtime/launcher.error.log` 與程序狀態，再決定後續操作。

repo 內的 `Hermes_Local_Tools.vbs` 依自身位置尋找 `start.ps1`，隱藏呼叫 `-IfNotRunning`。已有相同路徑的 Tunnel 程序時直接完成，不重啟連線。登入自啟動請依 [部署步驟](DEPLOYMENT.md#6-登入自啟動) 生成包含目前安裝路徑的本機 Startup entry，不把 repo 內的相對入口直接複製到 Startup。

Gateway 與其他服務沿用各自的官方登入入口，不屬於 Web MCP 啟動鏈。部署磁碟需在登入時可用。要停用 MCP 自啟動，只移除啟動資料夾內的 `Hermes_Local_Tools.vbs`。

Codex 工具每次呼叫會重新探索 Desktop 連線；Desktop 關閉或登入切換期間暫時不可用，重新開啟並完成登入後再查詢。橋接使用同一 Windows 使用者的本機 Desktop 資料，不持有獨立的 Codex 登入憑證；更換 OpenAI 帳號不等同切換 Windows 使用者或搬移對話。跨帳號的指定對話可見性以即時查詢結果為準。

更新程序查詢入口後，於既有工作可安全結束時重載專用 Tunnel，再到 ChatGPT 外掛設定重新整理 Hermes 工具清單，確認出現 `hermes_process_read`，以新對話先 Bootstrap 再驗證輪詢。重載前先核對本入口的背景工作與正在使用的連線；本機 stdio 測試不會更新已運作的 Tunnel 或 Web 工具快取。此更新調整程序查詢分類與等待行為，不代表已修復 ChatGPT Web 的 Allow all 權限套用問題。

## 本機驗證

```powershell
& $env:HERMES_MCP_PYTHON -m unittest discover
node --test test_codex_input.cjs codex-status/test.cjs codex-status/test_diagnostics.cjs codex-status/test_cli.cjs
& $env:HERMES_MCP_PYTHON smoke.py
```

先依部署文件將 `HERMES_MCP_PYTHON` 設為已安裝的 PM Python。CLI 傳訊同樣使用此設定，未設定才使用 PATH 的 `python`；測試不控制真實 Desktop session。`smoke.py` 另啟本機 stdio 與可丟棄程序，不會重載已運作的 Tunnel。
