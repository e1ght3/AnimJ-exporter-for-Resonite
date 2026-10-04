# AnimJ exporter for Resonite

作者：**e1ght3**。開発中のプレビューです。

Blenderのタイムライン上の動きをAnimJとして出力します。初回モデル用のFBXもチェックボックスで同時出力できます。Resonite内の「AnimJ Library」パネルと組み合わせ、未着用モデルの動きを切り替えられます。

| 配布物 | 版 | 用途 |
| --- | --- | --- |
| Blenderアドオン | 0.3.1 | オブジェクト・ボーンの移動、回転、拡縮を出力 |
| Resonite内ツール | 0.1.0-preview | モデル・クリップの保管、一覧編集、再生・一時停止 |

**まず[操作マニュアル](docs/USER-GUIDE.ja.md)を読んでください。** [既知の制限](docs/LIMITATIONS.ja.md)も併せて確認してください。

## 入手

Blenderには[アドオンZIP](downloads/animj_exporter_for_resonite-0.3.1-preview.zip)をダウンロードし、ZIPのままインストールします。GitHubのファイル画面でダウンロードボタンを押してください。「Code → Download ZIP」はリポジトリ全体なので、アドオンインストーラーへ直接渡さないでください。

Resonite内ツールは作者e1ght3が空の状態でインベントリへ保存済みです。現時点では作者から直接受け取ってください。共有リンクは準備中です。作者から渡された空の「AnimJ Library 0.1.0 preview」を使用してください。BlenderアドオンのZIPにはResoniteアイテムは含まれません。

## 開発と表記

設計・実装・資料作成にAI **Astra** を使用し、Resonite内での構築・調査・検証に **ResoniteLink** と **Resoloop** を使用しました。ResoniteLinkとResoloopはAIモデルではなく、開発時の接続・操作ツールです。通常の利用者はこれらをインストールする必要はありません。

コピー時の作者表記例：`AnimJ exporter for Resonite — e1ght3`。
本アドオンは **GPL-3.0-or-later** で配布します。作者e1ght3の著作権表示とライセンスを残し、対応するソースを添えて自由に利用・コピー・改変・再配布できます。商用利用も可能です。再配布はGPLの条件に従ってください。無保証です。詳細は[LICENSE](LICENSE)と[簡単な利用条件](TERMS.ja.md)を参照してください。

[Blender](https://www.blender.org/) ／ [AnimJ公式仕様](https://wiki.resonite.com/AnimJ) ／ [ResoniteLink](https://github.com/Yellow-Dog-Man/ResoniteLink) ／ [Resoloop](https://github.com/orange3134/resoloop)

本プロジェクトはBlenderやResoniteの公式製品ではありません。テストに用いた第三者のアバターモデル、ユーザー作例、Resonite本体、Resoloop本体は同梱していません。

ソースの構成とZIPの再生成は[開発者向け案内](docs/DEVELOPMENT.ja.md)を参照してください。
