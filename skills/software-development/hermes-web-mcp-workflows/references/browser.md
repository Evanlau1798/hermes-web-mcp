# browser use

網站任務使用 browser_use。先 action=guide 完整讀取本機目前版本的 Ego 官方指南，再以 action=run、code 執行官方 JavaScript。
同一任務建立一次 TaskSpace，印出 spaceId；後續用 taskSpace(spaceId) 和既有分頁標籤接續。每次 run 都是新 Node.js 程序，變數不跨次保存。
開頁後讀 snapshot，使用觀測到的元素參照 click/fill 等，等待預期結果並回傳下一個 snapshot。中文直接寫在 code，工具會以 UTF-8 腳本執行。
截圖前讀 [MCP 圖片顯示範例](mcp-images.md)，用 await page.screenshot({path: browserScreenshotPath}) 寫入本次 MCP 注入的專屬路徑；依範例取得原生圖片、在同一次宿主執行顯示並核對，之後才另呼叫 finish。長操作依 [程序回執](processes.md) 沿原 session_id 查詢；不重送腳本。
guide 結果若有截短先查完整輸出，不將缺少指南結尾視為讀完。結果不明先查程序與頁面，不重送原腳本。
先決定本步要確認的資料，再用官方 evaluate 回傳有界 JSON，例如 URL、標題、主區段與必要連結；需要元素參照才取 snapshot。遇到 popup，依官方事件／回執取得新頁標籤，再操作該頁；原頁 handle 不會自動變成新頁。SPA 操作後等待預期元素或狀態，並核對 URL 與渲染內容。

載入等待：操作前定義本步完成條件。URL 變更、load 事件、snapshot 或 article 已出現，不等於目標內容可讀；需要導航時先用官方 waitForURL 確認目標 URL；再以指向主實體與必要欄位的 waitForSelector／waitForFunction，在有界 timeout 內確認內容及應用狀態已就緒。waitForURL 不能代替內容就緒條件。SPA 切換需確認舊內容已換成目標內容，再擷取；不以固定 sleep 或全站 network idle 代替任務條件。依目前版本 guide 核對 API 參數。

逾時恢復：先區分 MCP／程序等待逾時、導航等待逾時與腳本執行結果不明。程序尚未完成時沿原 session_id 查詢，不平行重啟腳本；完成後在原 TaskSpace／分頁檢查目前 URL、popup／dialog、主內容及預期結果。導航等待逾時不代表導航失敗：結果已成立就接續，尚在載入則在本步剩餘預算內等待，目標不符則先診斷。採有限嘗試與總等待預算，耗盡時回報最後狀態；不因逾時換 Space、無限增加 timeout 或重播整段流程。表單提交、付款、發訊等可能寫入的動作，結果不明時先查應用回執，不自動重送。evaluate 若回報 executionStopped／mayHaveLateEffects，依官方指南處理晚到效果，確認安全後才做有副作用的後續操作。

主內容歸屬：先由目前頁面觀測主內容的穩定身分，例如 canonical／permalink、記錄 ID 或詳情標題，再將作者、時間、正文等欄位綁定同一主實體。僅限制在 article 容器仍可能混入引用、回覆、推薦或隱藏副本；逐欄核對來源節點、所屬實體及連結，不取全頁／容器內第一個 time、作者或文字當主資料。必要時回傳有界 JSON 的 entity_id／source_url、欄位值與各欄位的來源依據。來源衝突或缺漏就標記未確認，不用鄰近引用補值；多篇逐項驗證，不以點擊成功或 URL 正確推論全部欄位正確。時間保留原始 datetime／時區，顯示時才轉換；翻譯與原文分開標示。

只將經獨立核實、可跨網站重用的解法納入框架導引；網站特定 DOM、帳號、貼文與臨時載入時間留在本次任務，不寫成通用規則。
使用者接管時按官方指南 handOff，完成後再續接同一個 Space；任務成功最後呼叫 finish({keep:[]})，只保留使用者需要的結果頁。
