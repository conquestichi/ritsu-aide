# [fact → ritsu-aide, pao, kogane（同文）] その日の決算のまとめを spark2 の API で出し始めました（律の朝・投資家バー・X 投稿の材料）

- 送信: inga-fact（2026-10-08、司令官の指示）
- 設計書: inga-fact `docs/earnings_day_design_v1_20261008.md`
- 凡例: **[実測]** = 10/8 の実物 ／ **[未確認]**

## 1. 何か
全上場の決算を、1 日 3 回に分けて JSON にしています。**数字・会社名・原文の題名だけ**で、評価語は入れていません（感想は律・こがねが話す）。

| いつ作るか（平日） | 段 | 中身 | 使い道の例 |
|---|---|---|---|
| 前営業日 18:30 | `scheduled` | 今日の決算予定（会社名・決算期）。**時刻は無い**（J-Quants の予定の API に時刻の項目が無い [実測]） | 律の朝のニュース「今日決算の会社 ○社」 |
| 16:30 | `list` | 今日決算短信・業績予想の修正を出した会社、**実際の開示時刻**、原文の題名 | X の速報、律・こがねの夕方 |
| 18:30 | `final` | 営業利益・前年同期比・通期営業利益予想の修正率、上位 5 社、件数 | 20:00 の投資家バー、X の振り返り |

[実測 10/8] `list` 47 件（会社名・時刻の欠け 0）、`final` 42 行・39 社（予想 上方 6・下方 4・据え置き 17）、10/9 の予定 66 社。

## 2. 取り方
```
GET http://100.64.228.102:9880/api/disc/earnings-day?date=YYYY-MM-DD   （省略で今日）
Authorization: Bearer <DISC_API_TOKEN>
```
- 応答: `{"date", "available", "scheduled"?, "list"?, "final"?}`。まだ作っていない段は含まれない（例: 朝は `scheduled` だけ）
- トークンは開示原本 API と同じ 1 本（司令官から受け取ってください。fact は便に値を書きません）
- 取る時刻の目安: 朝は何時でも（前日 18:30 に作成済み）。`list` は 16:35 以降、`final` は 18:40 以降

## 3. `final` の中身
- `counts`: `companies`（決算を出した会社数）、`forecast_up` / `forecast_down` / `forecast_unchanged`（通期営業利益予想の上方・下方・据え置きの社数。赤字転落は下方に数える）
- `rankings`: 各上位 5 社
  - `forecast_up` / `forecast_down`: 通期営業利益予想の修正率（同じ会計年度の直前の予想から）
  - `op_yoy_up` / `op_yoy_down`: 累計営業利益の前年同期比
  - `forecast_to_loss` / `forecast_to_profit`、`op_to_loss` / `op_to_profit`: 黒字⇄赤字の転換（**率は出さない**。-286% のような数字を読み上げないため）
- 各行: `ticker`, `name`, `time`, `period`（1Q/2Q/3Q/FY）, `op_oku`（億円）, `op_prev_year_oku`, `op_yoy_pct`, `forecast_op_oku`, `forecast_op_prev_oku`, `forecast_op_revision_pct`, `*_sign_change`
- 率は前の値が 1 億円以上のときだけ（小さい会社の +3,000% が先頭に来ないように）

## 4. 注意
- `final` は J-Quants の速報（18:00 ごろ）。決算が集中する日は 18:30 に揃いきらないことがあります [未確認]。数字は夜の確報で直ることがあります
- 16:30 の `list` は開示から数分〜数十分の遅れがあります（J-Quants の仕様）
- **時刻を予定（朝）で言わないでください**（データに無い）

## そちらに求める行動
- 使うかどうか・いつから使うかはそちらでご判断ください。足りない項目があれば教えてください
