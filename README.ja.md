# OpsMineFlow

[English README](README.md)

OpsMineFlow は、業務効率化コンサルティングの As-Is 調査、業務棚卸し、業務プロセス可視化、ボトルネック分析、自動化候補抽出、報告書ドラフト作成を支援する、Mac向けローカルファーストOSSだよ。

## プロダクト概要

商用SaaS契約なしで、CSV/JSONイベントログや任意のActivityWatchエクスポートをローカルに取り込み、業務フローと改善候補を可視化する。

## OpsMineFlowの目的

本人同意に基づく業務改善、BPR、As-Is調査を効率化すること。社員監視や個人評価を目的にしない。

## ローカル完結方針

実行時の通信は localhost、ローカルファイル、Tauri内部通信だけを想定する。外部API、テレメトリ、クラッシュ送信、外部アップデート確認、CDN、外部フォント、外部画像は使わない。

## LLM/API非連携方針

OpenAI、Anthropic、Google、Azure、Ollama、ローカルLLMを含むLLM連携は実装しない。ラベル付け、分析、レポートはルールベースと統計処理で行う。

## 商用利用しやすいApache-2.0ライセンス

プロジェクト本体は Apache-2.0 固定。中核依存にはMIT、Apache-2.0、BSD系など商用利用しやすいものだけを採用する。AGPL/GPL/LGPL/SSPL/Commons Clause/Business Source License/Non-Commercial系は中核依存にしない。

## 機能

- CSVイベントログ取り込み
- JSONイベントログ取り込み
- ActivityWatch風エクスポート取り込み
- 明示ON時のみActivityWatch localhost API取り込み
- 明示的な開始・停止によるmacOS前面アプリ記録
- 標準イベントスキーマ
- URLパスとウィンドウタイトルのマスキング
- ルールベース業務ラベル付け
- アプリ別・業務別時間分析
- Directly-Follows Graph
- バリアント分析
- ボトルネック候補抽出
- 繰り返し作業・アプリ往復検出
- 自動化候補スコアリング
- Markdown/HTML/CSV/JSON/Mermaid/SVG/draw.io系エクスポート

## ローカル製品版スコープ

OpsMineFlow は、1コマンド導入、1コマンド起動、WebUI操作、ローカル永続保存、診断、記録、取り込み、分析、出力までをローカルアプリとして扱える水準を目指す。macOS記録は利用者がWebUIで明示的に開始した間だけ動作し、ブラウザ拡張は既定OFFのロードマップ項目として扱う。方針は [docs/product/COLLECTION_ROADMAP.md](docs/product/COLLECTION_ROADMAP.md) に記録する。

## 最短セットアップ

必要なもの:

- macOS Sonoma以降
- Python 3.11以降
- Node.js 20以降
- npm

新しいMacのターミナルから1回だけ実行:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuzuminami/OpsMineFlow/main/scripts/bootstrap_mac.sh)"
```

標準の場所へclone済みの場合は、次の1コマンドで再インストール:

```bash
cd ~/OpsMineFlow && ./scripts/install_mac.sh
```

毎回の起動:

```bash
cd ~/OpsMineFlow && ./scripts/run_local.sh
```

別のターミナルから終了:

```bash
cd ~/OpsMineFlow && ./scripts/stop_local.sh
```

起動中のターミナルで `Control-C` を押しても終了できる。すでに正常起動中に起動コマンドを再実行した場合は、そのプロセスを安全に再利用してブラウザを開く。

ブラウザは `http://127.0.0.1:5173` で自動的に開く。起動後の通常操作はWebUIだけで完結する。

`./scripts/...` はOpsMineFlowのディレクトリ内でだけ実行できる。bootstrapの標準インストール先は `~/OpsMineFlow`。別の場所にcloneした場合は、実際のディレクトリを使う。

## 初心者向け使い方

### 1. 日本語へ切り替える

画面右上の **日本語 / English** で表示言語を切り替える。初回はブラウザの言語に合わせ、日本語環境では日本語になる。選んだ言語はブラウザ内だけに保存され、次回起動時も維持される。

### 2. 最初から表示される7件について

初回に表示される7件は画面確認用のサンプルデータで、あなたのMacから取得した記録ではない。

- **最新状態を再読込**: ローカルSQLiteから最新状態を読み直す。初期化や削除は行わない。
- **サンプルデータを削除** または **設定 > データを削除**: イベント、ラベル、レビュー、取り込み履歴を削除する。
- 削除後は、再読込やアプリ再起動をしても空の状態が維持される。
- プライバシー設定はデータ削除後も残る。

サンプルをもう一度確認したい場合は、リポジトリ内の `data/sample/sample_events.csv` を取り込む。

### 3. Mac上の業務を記録する

普段の操作をそのまま記録する場合は、ホーム上部の **業務を記録** を使う。

1. **案件・作業単位**に、あとで見分けられる名前を入れる。例: `2026-06-21 月次請求確認`。
2. **作業名**に、これから行う業務を入れる。例: `請求処理`。
3. 取得範囲を読み、同意チェックを入れる。
4. **記録を開始**を押す。初回サンプルが残っている場合は、確認後にサンプルだけ削除して記録を始める。
5. Safari、Excel、メールなど、業務に必要なアプリを普段どおり使う。画面には現在のアプリ、経過時間、確定したアプリ区間が表示される。
6. その作業単位が終わったら、必ず **記録を停止** を押す。
7. **ダッシュボード**、**業務フロー**、**アプリ切替**で結果を確認する。

記録するのは、前面にあるアプリの表示名、bundle identifier、開始・終了時刻、滞在時間だけ。ウィンドウタイトル、URL、キー入力、入力本文、パスワード、クリップボード、スクリーンショット、画面録画、マイク、カメラは取得しない。WebUIを開いただけでは記録は始まらず、停止後も勝手に再開しない。

特定のアプリを記録しない場合は、開始前に **設定 > 除外するアプリ** へアプリ名を1行ずつ入力して保存する。記録エージェントが利用不可と表示された場合は、次を実行してからOpsMineFlowを再起動する。

```bash
cd ~/OpsMineFlow && ./scripts/install_mac.sh
```

CSV/JSONやActivityWatchの既存ログを使う場合は、**ホーム > データ収集を始める** から取り込み方法を選ぶ。

### 4. CSVまたはJSONを取り込む

1. CSVまたはJSONのイベントログを用意する。
2. Finderでファイルを選び、`Option-Command-C` を押してフルパスをコピーする。
3. **ホーム > データ取り込み** を開く。
4. CSVまたはJSONを選び、コピーしたパスを貼り付ける。
5. **内容を確認** を押し、イベント件数、機密フラグ、アプリ、時間を確認する。
6. 問題がなければ **確認したファイルを取り込む** を押す。

CSVでは主に `case_id`、`activity`、`timestamp_start`、`timestamp_end`、`user`、`app_name`、`url`、`memo` を使う。取り込みを実行すると現在の分析データが置き換わり、ローカルの取り込み履歴に記録される。

### 5. 分析画面を見る

- **ダッシュボード**: 件数、アプリ別時間、業務分類別時間、ボトルネック、自動化候補。
- **イベント一覧**: マスキング済みのイベント単位データ。
- **業務フロー**: 開始・終了、遷移、頻度、時間、選択した業務の詳細。
- **アプリ切替**: アプリ間の遷移と往復操作。
- **自動化候補**: 候補を並べ替え、採用・保留・却下・未確認を保存。
- **レポート**: ローカル生成したMarkdownレポートを確認。

### 6. 結果を出力する

**ホーム > 出力** でMarkdown、JSON、CSV、Mermaid、draw.ioを選び、内容を確認する。マスキングと機密フラグを見てから **指定先へ保存** または **ダウンロード** を押す。

### 7. 診断とデータ削除

**ホーム > 診断** ではAPI、WebUI、SQLite保存先、依存、ポート、ActivityWatch、外部通信禁止状態を確認できる。現在の分析を消す場合は **設定 > データを削除** を使う。元ファイルが残っていない場合、削除した分析データは元に戻せない。

### 8. OpsMineFlowを終了する

起動したターミナルで `Control-C` を押すか、別のターミナルから次を実行する。

```bash
cd ~/OpsMineFlow && ./scripts/stop_local.sh
```

詳しい運用手順は [docs/operations/RUNBOOK.md](docs/operations/RUNBOOK.md)、問題が起きた場合は [docs/operations/TROUBLESHOOTING.md](docs/operations/TROUBLESHOOTING.md) を参照。

macOSアプリ成果物の作成:

```bash
./scripts/package_macos.sh
```

配布手順: [docs/operations/PACKAGING_MACOS.md](docs/operations/PACKAGING_MACOS.md)

## 開発者向け手順

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
./scripts/smoke_local.sh
```

開発起動:

```bash
./scripts/dev.sh
```

## CSV/JSON取り込み

CSVは `case_id`、`activity`、`timestamp_start`、`timestamp_end`、`user`、`app_name`、`url`、`memo` などを受け付ける。JSONは汎用配列とActivityWatch風エクスポートを標準イベントへ変換する。

## Mermaid/SVG/draw.io出力

Directly-Follows GraphをMermaid形式とdraw.io互換mxfile XMLとして出力する。WebUIから出力内容をプレビューし、ダウンロードまたはローカルパス保存できる。SVGはローカルレンダリングで対応する予定。

## ローカル保存

実行データはデフォルトでユーザーのアプリデータ配下にあるSQLiteへ保存する。保存先を変える場合は `OPSMINEFLOW_DATA_DIR` を指定する。

## プライバシーとセキュリティ

キーログ、入力本文、パスワード、スクリーンショット、画面録画、マイク、カメラは扱わない。共有前にはexportプレビューと機密警告を確認する前提。

## ライセンス

Apache-2.0。

## 注意事項

OpsMineFlowはコンサルティングと業務改善の補助ツール。法務、人事、セキュリティ、コンプライアンス判断はクライアント側の正式レビューと併用してね。
