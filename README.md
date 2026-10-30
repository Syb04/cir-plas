# CirPlas — CCP 装置向けプラズマ回路シミュレータ

ウェハ電極とフォーカスリングを持つ CCP（容量結合プラズマ）装置を、軸対称の等価回路メッシュとして
**ngspice** で解き、定在波効果・表皮効果を含む電子加熱分布（将来は電子密度分布）を計算するツールです。
Windows ネイティブ GUI（PySide6）を予定しています。

- 仕様書: [docs/spec/README.md](docs/spec/README.md)（v0.2）
- 決定事項: [docs/spec/06_decisions.md](docs/spec/06_decisions.md)
- ロードマップ: [docs/spec/07_roadmap.md](docs/spec/07_roadmap.md)

## 状態

M1（コアとワーカ CLI）実装済み。検証 V1（単セル）、V2（線形定在波）、V4（磁束チェーン・電荷保存）が通ります。
GUI（M2 以降）は未着手です。

## 使い方（開発者向け）

```bash
# 依存: Python 3.12+, ngspice 共有ライブラリ (Linux: apt install ngspice libngspice0 / Windows: ngspice.dll)
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q tests                # 単体・統合・検証テスト
.venv/bin/cirplas-solve cirplas/data/examples/demo_300mm_60MHz.cirplas.json -o demo.h5 --mode tran
```

結果は HDF5（`/ac`, `/tran`, `/mesh`, `/plasma` グループ）に書き出されます。形式は仕様書 03 章 §4.2 を参照してください。
