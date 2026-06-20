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

OpsMineFlow は、1コマンド導入、1コマンド起動、WebUI操作、ローカル永続保存、診断、取り込み、分析、出力までをローカルアプリとして扱える水準を目指す。macOS常駐ログ収集とブラウザ拡張はロードマップ項目。

## 最短セットアップ

必要なもの:

- macOS Sonoma以降
- Python 3.11以降
- Node.js 20以降
- npm

```bash
./scripts/install_mac.sh
```

新しいMacでの1行bootstrap:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuzuminami/OpsMineFlow/main/scripts/bootstrap_mac.sh)"
```

毎回の起動:

```bash
./scripts/run_local.sh
```

ブラウザは自動で開く。起動後の通常操作はWebUIだけで完結する。

## WebUIでの操作順

### 1. ログ取り込み

**Home > Import** でCSVまたはJSONを選び、ローカルファイルのパスを入力して **Preview** を押す。イベント件数とマスキング済みサンプルを確認してから **Import Previewed File** を押す。ActivityWatch localhost取り込みは、利用者が明示的に有効化したときだけ使う。

### 2. 分析

**Dashboard** で全体件数、**Event Explorer** でマスキング済みイベント、**Process Map** で遷移とボトルネック、**App Switching** でアプリ往復、**Automation** で候補の並び替えと採用・保留・却下レビューを行う。

### 3. 出力

**Home > Exports** でMarkdown、JSON、CSV、Mermaid、draw.ioを選び、内容をプレビューする。プライバシー警告を確認してからローカルパス保存またはダウンロードする。

### 4. 診断

**Home > Diagnostics** でAPI、WebUI、保存先、依存、ポート、ActivityWatch、local-only状態を確認する。**Run Checks** でライセンスとローカルネットワークのguardrailを実行できる。

### 5. ローカル分析データ削除

**Settings** で **Delete Data** を押し、確認ダイアログを承認する。取り込みイベント、ラベル、レビュー状態、取り込み履歴がローカルDBから削除される。

### 6. 終了

OpsMineFlowを実行しているターミナルへ戻り、`Control-C` を押す。

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
