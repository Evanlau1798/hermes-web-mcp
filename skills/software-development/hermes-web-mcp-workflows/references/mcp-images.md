# MCP 圖片在 Web 宿主顯示

browser_use 與首次完成的 process_read poll 都可能回傳圖片。截圖前先讀本文件；以下處理的是 MCP 回傳格式，與網站 DOM 無關。

## 保留完整回傳，立即顯示

將工具的完整回傳存為 `r`，在同一次宿主執行中接著執行以下片段。此處的 `image` 是目前 Web 宿主提供、接受 data URL 的圖片 helper；其他宿主依自身 helper 文件使用，不能把 helper 寫進 Ego 的 `code`。

已核實 Web 包裝的 `r.content` 是 JSON 字串，`r.content_items` 才是原生文字／圖片區塊陣列。標準 MCP 則可直接以 `r.content` 陣列回傳。`r.structuredContent` 是狀態資料，不包含原圖。不要先把 `r` 換成其中的文字或 structuredContent。

```javascript
const blocks = Array.isArray(r.content_items) ? r.content_items
  : Array.isArray(r.content) ? r.content : [];
const pictures = blocks.filter(block => block?.type === 'image'
  && typeof block.mimeType === 'string' && typeof block.data === 'string');
if (!pictures.length) {
  throw new Error('No MCP image block: inspect the original envelope and process state; do not replay the screenshot.');
}
for (const picture of pictures) {
  image('data:' + picture.mimeType + ';base64,' + picture.data);
}
```

`data` 是原始 base64，只加一次 `data:<mimeType>;base64,` 前綴；不再次編碼。不用遞迴掃描器取代已知的區塊陣列，也不把整個陣列或狀態物件傳給 image。

直接調用 browser_use 的宿主執行範式：先 `const r = await <目前已發現的 browser_use 工具>(參數)`，緊接上方片段；需要 poll 時把同一片段放在首次取得圖片的 poll 回傳之後。工具名稱依實際發現結果，不照抄不存在的 namespace。完成圖片顯示後，只輸出 session_id、狀態與圖片數等精簡文字，不輸出整個 r、pictures 或 base64。內容參照先取得原文，再檢查實際區塊；不能只讀 JSON 文字而丟掉原生圖片。

## 核對、失敗及清理順序

1. Ego 腳本截圖後先回傳；在 Web 宿主立即顯示並實際核對圖片中的內容，再以後續 browser_use 呼叫結束同一 TaskSpace。不要把 finish 放在截圖腳本裡，然後才嘗試解析圖片。
2. 尚在 running／等待逾時時，沿原 session_id 查到首次完成回執；不要平行重啟腳本。僅有 image_count=1 或路徑不證明圖片已顯示。
3. 已收到圖片但 helper 失敗時，優先在同一次宿主執行用原始區塊修正顯示呼叫；若宿主有跨次保留 API，可按其文件暫存原回傳。不可假設每個宿主都有 store，也不能以後續 poll／log 取代已消耗的首次圖片。
4. 診斷只列外層 key、陣列長度與圖片欄位的型別／長度；不要把陣列元素或 data 直接放進診斷 JSON。若原回傳已不可用，回報缺口，不自動重跑瀏覽動作或宣稱已目視驗證。確認不再需要測試頁面後，再清理 Space。

驗收須同時確認 MCP 有圖片區塊、helper 已執行、Web 中實際出現圖片，以及讀圖結果正確。只有截圖成功時標記「已擷取，尚未視覺核對」。圖片不經一般文字日誌回傳，不新增圖片快取或額外讀檔入口。
