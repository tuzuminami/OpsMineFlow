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

## MVPスコープ

MVPではCSV/JSON取り込み、標準化、分析、draw.io出力、Markdown報告書、ローカルAPI、最小デスクトップUIを優先する。macOS常駐ログ収集とブラウザ拡張は第2段階。

## macOSインストール

必要なもの:

- macOS Sonoma以降
- Python 3.11以降
- Node.js 20以降
- npm

```bash
./scripts/setup_mac.sh
```

## 開発環境セットアップ

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
```

開発起動:

```bash
./scripts/dev.sh
```

## 使い方

1. 対象者へ収集範囲を説明
2. 同意取得
3. CSV/JSONを取り込み
4. マスキング済みイベントを確認
5. 業務ラベルを付与
6. プロセスマップと自動化候補を生成
7. Mermaid、SVG、draw.io、Markdown、CSV、JSONで出力

## CSV/JSON取り込み

CSVは `case_id`、`activity`、`timestamp_start`、`timestamp_end`、`user`、`app_name`、`url`、`memo` などを受け付ける。JSONは汎用配列とActivityWatch風エクスポートを標準イベントへ変換する。

## Mermaid/SVG/draw.io出力

Directly-Follows GraphをMermaid形式とdraw.io互換mxfile XMLとして出力する。SVGはローカルレンダリングで対応する予定。

## プライバシーとセキュリティ

キーログ、入力本文、パスワード、スクリーンショット、画面録画、マイク、カメラは扱わない。共有前にはexportプレビューと機密警告を確認する前提。

## ライセンス

Apache-2.0。

## 注意事項

OpsMineFlowはコンサルティングと業務改善の補助ツール。法務、人事、セキュリティ、コンプライアンス判断はクライアント側の正式レビューと併用してね。

