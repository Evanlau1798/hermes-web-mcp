---
name: hermes-web-mcp-workflows
description: Hermes Web MCP：browser use、技能更新、Codex 傳訊與持續追蹤。
---

# Hermes Web MCP 工作流程

ChatGPT Web 先呼叫 hermes_context，再使用此入口。本機原生 Codex 僅依人類明確要求使用 Hermes 整合。

## Harness Bootstrap

每個新任務先呼叫 hermes_context(cwd,task)，task 描述具體意圖；讀 harness.steps、prerequisites.pending 與候選技能。後續每個工具帶回 bootstrap.bootstrap_id。改任務／cwd 時，以舊 ID 重新呼叫 context，立即改用新 ID；技能與規範閱讀狀態重新開始，背景程序則沿原 session_id 查詢。
status=ready 表示已初始化，execution_ready 才表示前置條件完成。先用 hermes_skill_read 連續讀 required SKILL.md 到 eof，再按正文讀必要 references；AGENTS.md 用 read_file 從 offset=1 依分頁連續讀到結尾。每次閱讀的 prerequisites 回報尚待項目，清空後執行寫入。skill_match_status=no_match 時，以 pending 指定 query 呼叫 hermes_skills_list（offset=0）；找到技能就先讀完，仍無匹配才記為 searched_no_match，不將空候選視為沒有規範。
依「定位 → 讀取 → 操作 → 完整回執 → 驗證」執行，不用口頭描述代替工具操作。檔案用 read/search/patch/write；命令用 PowerShell terminal，長命令沿 session_id 查 poll/log；網站先讀 browser guide，桌面先擷取再操作；Codex 依精確目標與 operation_id；結尾按 learning 指引更新已驗證的可重用技能。
新 Web 對話自行初始化，不複製其他對話的 bootstrap ID。metadata 存在時 ID 綁定 session；缺少時 ID 是任務關聯憑證，不共用 transport 狀態。server 不自動讀取人類訊息判斷換任務；Agent 在新意圖／改目錄時主動重新初始化。Bootstrap 不讀入人設、記憶或啟動 Hermes 模型。

## 瀏覽器 browser use

網站任務使用 browser_use。先 action=guide 完整讀取本機目前版本的 Ego 官方指南，再以 action=run、code 執行官方 JavaScript。
同一任務建立一次 TaskSpace，印出 spaceId；後續用 taskSpace(spaceId) 和既有分頁標籤接續。每次 run 都是新 Node.js 程序，變數不跨次保存。
開頁後讀 snapshot，使用觀測到的元素參照 click/fill 等，等待預期結果並回傳下一個 snapshot。中文直接寫在 code，工具會以 UTF-8 腳本執行。
截圖使用 await page.screenshot({path: browserScreenshotPath})，工具直接回傳圖片。長工作沿用回執 session_id，以 hermes_process_read(action=poll/log) 查詢，最後一次查詢會附上截圖；需要停止時用既有 hermes_process_manage。
每次 run 先完整讀取工具回執，若是內容參照先取得原文。status=exited 時直接使用該回執的圖片；僅 running/timeout 才查詢程序。圖片隨首次完成回執回傳，後續重複 poll 不再回傳同張圖片；log 用於完整文字輸出，poll 用於完成狀態與圖片。
宿主若以 content_items 包裝 MCP 內容，依實際結構取出 type=image 的區塊，在同一次宿主執行中以圖片 helper 顯示其 data URL（data:<mimeType>;base64,<data>）；不要對非陣列 content 呼叫 map，也不要只回報圖片路徑。顯示後再核對畫面。
guide 結果若有截短先查完整輸出，不將缺少指南結尾視為讀完。結果不明先查程序與頁面，不重送原腳本。
先決定本步要確認的資料，再用官方 evaluate 回傳有界 JSON，例如 URL、標題、主區段與必要連結；需要元素參照才取 snapshot。遇到 popup，依官方事件／回執取得新頁標籤，再操作該頁；原頁 handle 不會自動變成新頁。SPA 操作後等待預期元素或狀態，並核對 URL 與渲染內容。
執行回執的 observation 說明截短範圍；文字不全時沿 log 參數回讀。log 的 offset/limit 選行，char_offset/max_chars 再切長行；沿 next_char_offset 接續，content_id 改變時重新確認選定內容。日誌是保留中的滾動緩衝。timings_ms 量測本機準備、啟動與等待，不代表網站或 Tunnel 的網路延遲。
使用者接管時按官方指南 handOff，完成後再續接同一個 Space；任務成功最後呼叫 finish({keep:[]})，只保留使用者需要的結果頁。

## 技能更新

以 hermes_skill_read 讀取原文，使用 hermes_skill_manage 的 operations 呼叫原生 create、patch、write_file、remove_file 或 delete。
patch 使用精確 old_string/new_string；支援檔放在 references/、scripts/、templates/ 或 assets/。
回執的 validation、lint、rollback 及 approval_required 按實際結果處理；待批准不等於完成。不用一般檔案 patch 代替正式技能維護。

## 持續追蹤

使用者要求持續追蹤時，以 hermes_tracking(action=start) 啟動真正背景 worker，取得 tracking_id 後再表示追蹤已開始。
Codex 用 kind=codex、thread_id；Computer Use 用 kind=computer、由 list_windows 取得的精確 pid + window_id，背景讀取 AX。
mode=manual 追蹤至使用者停止；mode=task_complete 依指定／觀測到的 turn 完成，或明確的電腦文字條件自動停止。可設 deadline_seconds。
電腦條件例如 completion_predicate={"path":["elements",0,"label"],"contains":"完成"}，先擷取並核對欄位，不能臆測完成條件。

start 最多等待 25 秒取得 baseline；以 baseline_ready 判斷有無首份有效狀態。以 hermes_tracking_status(tracking_id,after_sequence,wait_seconds=25) 查詢存活、心跳、current_state、degraded、錯誤與增量事件。
要求持續觀察時，在目前 Web 回合沿同一 tracking_id 和 sequence 接續等待；先採 120 秒觀察預算，使用者指定期間則依其要求。每次檢查 events、wait.reason、alive 與 terminal，重要變化才回報，單純心跳不重複敘述。timeout 只結束本次等待；manual worker 仍存活。達到回合預算時交代最後觀測時間、背景狀態與 tracking_id，不能把背景取樣說成仍有 Web Agent 監看或會自動語音通知。使用 UTC 時間戳計算先後，面向使用者以 Asia/Taipei 顯示，不以 UI 相對時間推算精確時刻。
同樣畫面不重複產生變化事件，missed_events 提示已淘汰的事件。畫面變化後按任務另行擷取，事件不保存私訊文字或圖片。
結束用 hermes_tracking(action=stop,tracking_id=...)；仍有取樣未返回時依 stopping/alive 查到 terminal，不能提前宣稱已完全停止。

背景 worker 可供同一 MCP 程序中的其他 Web 對話接續查詢；重啟後需重新開始。此機制不建立定時排程、不主動通知 Web／Discord，也不能讓已結束的 Web agent 自行醒來。
單次查詢、單張截圖或 wait 後結束都不算持續追蹤。Discord 背景快捷鍵限制依該應用支援處理，不以 focus 或前景操作擅自繞過。

## Codex 傳訊

逐字核對同一筆查詢的 thread_id 與完整 expected_title；send／resume 使用唯一 operation_id。
相同操作沿用 operation_id，以 codex_delivery_status 查回執；結果不明不換 ID 重送。
accepted 表示受理，以 codex_status/read 對照 turnBaseline 查證後續，不將「有新 turn」直接當作此訊息送達的證明。

傳訊前使用最新完成 turn 的真正 completedAt 判斷 30 分鐘閒置；notLoaded、缺少時間或矛盾狀態都回報未派送。
使用者要求傳訊後，閒置對話用 codex_prepare_send(thread_id,expected_title,message,operation_id) 啟動自動流程，不額外提醒或詢問是否 Compact。
最新完成 turn 非 Compact、目前上下文 >80000 tokens 且完成時間距今 >30 分鐘，三者都成立才先壓縮；否則直接傳。用量來自原生 last_token_usage，未知不猜測。閒置對話的發訊或續跑意圖都優先走 prepare_send，不另呼叫 send／resume。
啟動後以唯讀 codex_operation_status(operation_id) 等背景工作。需要壓縮時，只有新的原生 contextCompaction turn 成功完成，才由背景傳送原訊息；不要另呼叫 codex_send。
先讀 summary：compact=not_required 表示本次流程跳過壓縮，compact_complete=false 此時不代表仍在等待；delivery=accepted 表示受理。需要後續 turn 狀態時用 codex_operation_status(observe_turn=true)，turn_observation 只證明 baseline 之後有新 turn，不證明由本訊息觸發，也不代表使用者任務已完成。需要獨立送達回執時，再用 delivery_operation_id 查 codex_delivery_status。逾時、狀態不明、其他 turn 介入或 MCP 重啟均不自動重送；重新檢查並由人類重新要求傳訊。
查詢失敗依 stage/errorCode/requestId/suggestedAction 排錯，不以舊資料庫快照或 generic exception 推論 idle。
