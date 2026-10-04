# 開発者向け

ソースは `addon/resoanimation_exporter/` の5つのPythonファイルです。Blender同梱のPython API、NumPy、標準FBX入出力を使用します。外部サービスやAPIキーは利用者に不要です。

```sh
python tools/build.py
```

生成された `downloads/animj_exporter_for_resonite-0.3.1-preview.zip` をBlenderへインストールします。ソースZIP全体とは異なります。

- `__init__.py`：エクスポート画面とOperator
- `core.py`：評価・採取・出力処理
- `fbx.py`：FBXの階層・座標・名前の照合
- `mesh.py`：静的シェイプ・Modifierの準備と復元
- `format.py`：AnimJ・対応表・既存モデル検査

0.3.1公開プレビューでは従来の検証版から書き出しアルゴリズムを変更していません。公開に合わせて作者表示とライセンスコメントを追加しました。開発環境ではオブジェクト・ボーン・複数リグ・例外時復元を検証しましたが、このリポジトリには第三者モデルを含む検証素材やローカルセッション記録は収録していません。

このリポジトリの公開対象はBlenderアドオンと操作資料です。Resonite内ツールのProtoFluxソース・構築環境はこの初回公開には含みません。
