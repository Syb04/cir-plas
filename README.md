# CirPlas — CCP 装置向けプラズマ回路シミュレータ

ウェハ電極とフォーカスリングを持つ CCP（容量結合プラズマ）装置を、軸対称の等価回路メッシュとして
**ngspice** で解き、定在波効果・表皮効果を含む電子加熱分布と、それと自己無撞着な電子密度分布を計算するツールです。
Windows ネイティブ GUI（PySide6）を備えています。

- ユーザマニュアル: [docs/manual.md](docs/manual.md)
- 仕様書: [docs/spec/README.md](docs/spec/README.md)
- 決定事項: [docs/spec/06_decisions.md](docs/spec/06_decisions.md)
- ロードマップ: [docs/spec/07_roadmap.md](docs/spec/07_roadmap.md)

## インストーラ（Windows）

Windows インストーラは GitHub Actions の `windows-package` ジョブが master への push ごとに生成します。

1. [master の成功した CI 実行の一覧](https://github.com/Syb04/cir-plas/actions/workflows/ci.yml?query=branch%3Amaster+is%3Asuccess) を開き、いちばん上（最新）の実行を選びます。
2. ページ下部の **Artifacts** から `CirPlas-windows` をダウンロードします（GitHub へのログインが必要です。保存期間は 90 日）。
3. zip を展開し、`CirPlas-<version>-setup.exe` を実行します。インストール不要の展開済みバンドル（`CirPlas/`）も同梱しています。

ngspice を含むすべての依存物が同梱されています。最新版のインストーラでの動作確認は未了です（後日実施予定）。

## 状態

- **コア**：AC / TRAN（自己バイアス反復、周期定常の早期停止）/ 密度自己無撞着（電力指定、TRAN 補正）/ 電力指定駆動 /
  50 Ω 発振器と L・π 型整合器（自動チューニング）/ シースモデル（matrix・Child–Langmuir・衝突性）/ IEDF /
  1D・2D パラメータスイープ / FR 調整の自動最適化 / 上部電極の形状最適化（電子加熱・密度の平坦化） / HTML レポート。ガスは Ar を同梱し、他ガスは LXCat の断面積ファイルなどから読み込めます。
- **CLI**：`cirplas-solve`（`sweep` / `optimize` / `shape` / `report` サブコマンドを含む）、`cirplas-sweep`、`cirplas-optimize`、`cirplas-shape`、`cirplas-report`。
- **GUI**：設定パネル（幾何・プラズマ・回路・数値設定。計算規模と時間の見積り、ngspice オプション）、断面図、Qucs-S 風の回路図エディタ、
  ネットリスト、結果（概要・径方向・2D マップ・波形・スペクトル・エッジ・IEDF・密度）、スイープ、最適化、電極形状、比較、
  実行ペイン（収束履歴・残り時間）、自動保存と復元、ライト / ダークテーマ。
- **検証**：V1〜V7（単セル、線形定在波、表皮効果、磁束チェーン・電荷保存、FR 結合、密度反復、回路図モード）と
  M6 の追加検証（シースモデル、IEDF、周期定常の早期停止、最適化）が CI で通ります。

## 使い方（開発者向け）

```bash
# 依存: Python 3.12+, ngspice（共有ライブラリ、またはコンソール版の実行ファイル）
#   Linux: apt install ngspice libngspice0
#   Windows: 共有ライブラリ（libngspice-0.dll / ngspice.dll）が無ければ ngspice_con.exe（PATH または C:\Spice64\bin）でバッチ実行
python -m venv .venv && .venv/bin/pip install -e ".[gui,dev,gui-dev]"
.venv/bin/pytest -q tests                # 単体・統合・検証・GUI テスト
.venv/bin/cirplas-solve cirplas/data/examples/demo_300mm_60MHz.cirplas.json -o demo.h5 --mode tran
.venv/bin/cirplas-report demo.h5 -o demo.html
.venv/bin/cirplas-gui                    # GUI（PySide6）
```

結果は HDF5（`/meta`, `/project`, `/mesh`, `/plasma`, `/ac`, `/tran`, `/iteration` など）に書き出されます。形式は仕様書 03 章 §4.2 を参照してください。
