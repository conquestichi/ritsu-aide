# [hp → 全リポ] セッション開始時は、まず 8 リポをセッションに登録する（引き継ぎ文に必ず書いてください）

- 送信: inga-quants-hp (2026-09-26)
- 宛先: quants / fact / pao / kogane / ritsu-aide / stream / ritsu（同文）
- 依頼者: 司令官
- 凡例: **[実測]** = このセッションで実際にやって確かめた ／ **[未確認]**

## 何が変わったか
クラウドのセッションは、始めた時点では GitHub のリポが紐づいていないことがあります。
その状態では clone も push もできません。**作業の最初に、使うリポをセッションに登録する**必要があります。

## 手順 [実測: 2026-09-26 09:05〜09:10、HP のセッション]
1. リポごとに `add_repo`（owner=`conquestichi`、repo=各リポ名、**access=`push`**）を呼ぶ
2. 返ってきた指示どおり `git clone --depth 1 https://github.com/conquestichi/<repo> /home/claude/<repo>` を **1 本ずつ**実行する（並列にすると 429）。タイムアウトは長め（約 10 分）
3. `register_repo_root` で clone 先を登録する（各リポの CLAUDE.md が読み込まれる）
4. 対象の 8 リポ: `inga-quants-hp` `inga-quants` `inga-kogane` `inga-fact` `inga-ritsu-pao` `ritsu-aide` `inga-stream` `inga-ritsu`

結果: 8 リポとも clone でき、HP は **main へ直接 push が通り、Actions のデプロイも `completed success`**（`259a7cc`）。PAT は使っていません。

## 前の引き継ぎ文の誤り
「チャットの Project から始めたクラウドセッションは GitHub に一切届かない」と書いていましたが、
**Project から始めたセッションでも、上の登録をすれば届きました。** 届かなかったのは登録していなかったためと読んでいます。
[未確認] 登録なしで clone が本当に通らないかは、今回は試していません。

## そちらに求める行動
- 各リポの引き継ぎ文（`docs/handover_*.md` など、次のルームが最初に読むもの）の**冒頭の手順**に、上の「8 リポの登録」を必ず書いてください
- PAT を引き継ぎ文やリポに書く運用は不要になりました（書かない）
- 返信は不要です
