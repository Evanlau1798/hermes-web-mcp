# 本機唯讀讀取

內部資料格式可能改版；先確認實際 schema。僅需 Python 標準庫，不使用 Hermes bootstrap。

## 定位對話

索引：`%USERPROFILE%\.codex\state_5.sqlite` 的 threads。
先 `PRAGMA table_info(threads)` 檢查欄位；只查必要欄位。以 Python 建立唯讀連線：

```python
from pathlib import Path
import sqlite3
root = Path.home() / '.codex'
conn = sqlite3.connect((root / 'state_5.sqlite').as_uri() + '?mode=ro', uri=True, timeout=3)
conn.row_factory = sqlite3.Row
```

參數化 SQL 範例（keyword 和 thread_id 為資料，不拼成 SQL）：

```sql
SELECT id, title, cwd, updated_at, archived
FROM threads WHERE instr(title, ?) > 0
ORDER BY updated_at DESC LIMIT 10;

SELECT id, title, rollout_path, history_mode, updated_at
FROM threads WHERE id = ?;
```

沒指定標題時最多列最近 10 個；不先印 first_user_message 或 preview。updated_at 在此版為 Unix 秒；*_ms 為毫秒，勿混用。呈現時間使用 Asia/Taipei（此機 UTC+08:00）。session_index.jsonl 可作備援標題索引，其欄位為 id、thread_name、updated_at；不是即時狀態。

## 讀近期歷史

`thread_history_1.sqlite` 同樣 mode=ro；先確認 schema。threads 有資料但歷史表沒有列，不代表沒有對話，改讀該 thread 的 rollout_path。

```sql
SELECT turn_id, status, started_at, completed_at
FROM thread_turns WHERE thread_id = ?
ORDER BY rollout_ordinal DESC LIMIT 5;

SELECT turn_id, item_type, item_json, created_at_ms
FROM thread_items
WHERE thread_id = ? AND item_type IN ('userMessage', 'agentMessage')
ORDER BY rollout_ordinal DESC LIMIT 12;
```

把查詢結果反轉為時間順序，解析 item_json 後再選公開文字；不要把 JSON 整筆直接印出。每則先限制約 1000 字，需要更多才縮小 turn 範圍續讀。資料庫 busy/locked 時報來源暫不可讀，不刪除 -wal/-shm，不以 immutable=1 忽略活動 WAL。

## Rollout 備援

rollout_path 來自精確 ID 查詢，不從標題推造路徑。Windows 路徑可能有長路徑前綴，使用 Path/open 原樣處理；確認解析後落在此 Codex 根目錄的 sessions 或 archived_sessions 內。檔案不存在就回報本機缺少內容，不掃描整台磁碟。

逐行 json.loads；只略過尚未寫完的最後一行，其他解析錯誤需標明位置與讀取不完整。以 deque(maxlen=12) 留近期公開訊息，避免整檔載入記憶體。大檔優先資料庫或由有界尾端區塊找完整行，不為單次詢問無上限重掃。

已確認兩種格式，依檔案實際事件選一種，勿重複計算同一訊息：

- `type=response_item`、payload.type=message：role=user / assistant；只取 content 中 type=input_text / output_text 的 text。assistant 若含 phase，排除分析；不取 role=system/developer 或 reasoning 事件。
- `type=event_msg`、payload.type=item_completed：payload.item.type 為 **UserMessage / AgentMessage**（大寫）；content 是含 type/text 的陣列。只取文字，不取 raw_content。若同一 rollout 也有 response_item，優先 item_completed，保留助理 phase（commentary / final）的區別。
- 舊版可能使用 event_msg 的 user_message / agent_message：先確認該筆實際結構，只讀公開 message 欄位，不猜未見格式。
- task_started / task_complete 以 turn_id 配對，優先事件內 started_at / completed_at，其次頂層 timestamp。中斷/失敗事件名稱依實際版本驗證；沒有 task_complete 不能直接判失敗。

任一讀取最後都關閉連線與檔案。只輸出摘要、最近公開訊息與來源時間，不建立全量同步副本。
