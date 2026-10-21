# 程序與完整回執

命令預設 PowerShell，可明確指定 shell=bash。長命令 background=true；以回傳 session_id 用 hermes_process_read(action=poll/log) 接續，輸入或停止才用 hermes_process_manage；只操作此入口建立的程序。單次等待最多 30 秒，running／timeout 沿原 ID 等待，不平行重啟。bridge_busy 表示尚未執行，先查狀態。

以 status、exit_code、outcome 與完整輸出判定成功、等待批准、部分結果或結果不明；結果不明的寫入不得自動重送。讀取結果參照後再判斷，圖片與 JSON 文字分開處理，不能只讀 structuredContent。

observation 說明截短範圍；文字不全用 log 回讀：offset/limit 選行，char_offset/max_chars 切長行，沿 next_char_offset 接續；content_id 改變需重新確認內容。日誌是保留中的滾動緩衝，並非無限歷史。timings_ms 是本機準備、啟動與等待，不代表網站或 Tunnel 網路延遲。
優先調用回執的 next_action（另帶本任務 bootstrap_id）；完成回執會在需要時補讀開頭最多 6000 字元，仍截短則沿原日誌分頁，不重做擷取。next_line_offset 表示本批選行結束；只有 next_char_offset 已空才換行批次。log_availability=unavailable 表示保留日誌不可讀，不能宣稱完整還原或自動重跑。

browser use 截圖只在首次完成回執附上：status=exited 直接使用該回執；running／timeout 沿 poll 等完成，log 讀文字。後續 poll 不重送同張圖片，按 [圖片範例](mcp-images.md) 在取得原回傳的同一次宿主執行顯示。
