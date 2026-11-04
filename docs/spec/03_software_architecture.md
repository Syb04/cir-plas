# 03. ソフトウェア構成

## 1. 技術スタック

### 1.1 採用案と比較検討した代替案

**決定（06 章 Q1）：A. Python + PySide6 を採用します。** 以下は比較の記録です。

| 項目 | **A: Python + PySide6（採用）** | B: C++20 + Qt 6 | C: C# .NET 8 + WPF |
|---|---|---|---|
| GUI | PySide6（Qt 6 Widgets）＋ pyqtgraph | Qt Widgets ＋ QCustomPlot / ImPlot | WPF ＋ ScottPlot 5 |
| 数値 | numpy / scipy | Eigen | Math.NET |
| ngspice 連携 | ctypes で `ngspice.dll`（sharedspice API） | 直接リンク | P/Invoke |
| 結果ファイル | h5py（HDF5） | HighFive（HDF5） | HDF5.PInvoke |
| 起動時間 | 1〜3 s | < 0.5 s | < 1 s |
| 操作レスポンス | ネイティブ Qt。計算は別プロセスなので UI は常に軽い | 最速 | 速い |
| 物理モデルの改変容易性 | ◎（スクリプト感覚で差し替え、ノートブックで検証） | △ | ○ |
| スイープ・自動化 | ◎（同じ Python API を外部から呼べる） | ○（CLI 経由） | ○ |
| Linux CI でのビルド/テスト | ◎（GUI もオフスクリーンでテスト可） | ◎ | △（WPF は Windows 必須） |
| 配布 | 埋め込み Python 同梱 zip / インストーラ（~150 MB） | exe ＋ Qt DLL（~40 MB） | 自己完結 exe（~80 MB） |
| 開発工数（相対） | 1 | 2〜3 | 1.5 |

**採用理由**：本ツールの計算負荷は ngspice（C 実装）と行列演算（numpy/scipy）が担い、
Python 層はネットリスト生成・後処理・GUI に限られます。GUI は Qt のネイティブウィジェットで描画されるため
Web ベースの UI とは異なり操作は軽快で、pyqtgraph は $10^6$ 点規模の波形をインタラクティブに扱えます。
一方、本ツールの価値はシースモデル・加熱モデル・密度モデルの改良サイクルの速さにあり、
ここで Python の生産性が最も効きます。「速さ」の要件は **UI スレッドを計算で塞がない構成**（§3）と
**解析時間の目標**（02 章 §7）で担保します。

将来、GUI だけを C++/Qt に置き換える判断をしても、コア（ワーカ CLI ＋ HDF5）は変更不要な境界で設計します。

### 1.2 バージョン方針（案 A）

- Python 3.12（3.13 でも動作確認）、PySide6 ≥ 6.7、pyqtgraph ≥ 0.13、numpy ≥ 2.0、scipy ≥ 1.13、h5py ≥ 3.11、pydantic ≥ 2.7
- ngspice 共有ライブラリ：45 以降（最新安定版 47 で検証）。同梱して版を固定。
- Windows 10/11 64-bit。Linux は開発・CI 用（GUI 含め動作可）。

## 2. モジュール構成

```
cirplas/
  core/
    constants.py        物理定数
    project.py          プロジェクトスキーマ（pydantic）、検証、JSON パス操作
    materials.py        材料 DB、ガス DB（Arrhenius・表・断面積のマクスウェル積分・LXCat 読み込み、複数励起準位）
    mesh.py             径方向/軸方向メッシュ、面積・長さ・高さ、チェーンインダクタンス
    plasma.py           n(r), Te（粒子バランス）, nu_m, h_l, h_R, u_B, n_s, Drude 値、診断量
    sheath_models.py    Q(V) モデル（matrix, child, collisional, auto）、I_e、R_stoc、線形化 C_lin
    elements.py         セル・境界ごとの回路要素値（CircuitModel）
    schematic.py        回路図（自由配置）モデル、ポート導出、表形式からの変換
    estimate.py         未知数の計数（MNA）と計算時間の見積り
    result_data.py      HDF5 結果の読み込み（GUI・レポート共通）、警告判定
    results.py          HDF5 結果の書き出し
    report.py           HTML レポート（インライン SVG）
    netlist/
      builder.py        メッシュ＋要素値 → ネットリスト（AC/TRAN、外部回路・整合器・回路図）、既定 .options
      naming.py         命名規約
      user_elements.py  ユーザ定義ネットリストの検証
    ngspice/
      shared.py         sharedspice ctypes バインディング（bg_run、SendData コールバックによる早期停止）
      backend.py        バックエンド選択（共有ライブラリ / コンソール版 ngspice_con.exe のバッチ実行）
      rawfile.py        バッチ実行の rawfile リーダ（電流ベクトル名を共有ライブラリ形式に正規化）
    analysis/
      common.py         実行コンテキスト、初期推定、モデル構築
      ac.py             AC 解析（準線形反復、周波数掃引、整合器の自動設計）
      tran.py           TRAN 解析（ランプ、自己バイアス反復、周期定常の判定と延長）
      pss.py            周期定常の監視（早期停止・必要周期数の外挿）
      matching.py       L 型・π 型整合器の設計
      power_drive.py    電力指定駆動
      density.py        Level 1 密度反復（電力指定、TRAN 補正、全体モデル）
      sweep.py          パラメータスイープの展開と集約メトリクス
      optimize.py       FR 調整・均一性の自動最適化
      shape.py          上部電極の形状最適化（S_e の平坦化）
    post/
      signals.py        再標本化、高調波、時間平均
      tran_post.py      TRAN 後処理（シース、加熱、ポート、電力収支、2D マップ）
      iedf.py           IEDF（減衰電位モデル）
  worker/
    cli.py              cirplas-solve（--mode ac|tran|sc、サブコマンド sweep / optimize / shape / report）
    sweep_cli.py        cirplas-sweep（並列ジョブ）
    optimize_cli.py     cirplas-optimize
    shape_cli.py        cirplas-shape
    report_cli.py       cirplas-report
  gui/
    app.py, mainwindow.py   エントリポイント、メインウィンドウ（タブ・ドック・メニュー）
    project_model.py    プロジェクト ↔ ウィジェットのバインディング、Undo/Redo
    runmanager.py       ワーカプロセスの起動・監視・キャンセル
    estimates.py        見積りのバックグラウンド計算と係数の較正
    autosave.py         自動保存と復元
    theme.py            テーマ（ライト / ダーク）
    results_io.py       結果読み込み（core.result_data の再公開）
    panels/             geometry, plasma, circuit, numerics, run, results, results_extra, sweep, optimize, shape, compare
    widgets/            paramform（設定フォーム）, crosssection（断面図）, schematic（回路図エディタ）
  data/
    gases/ar.json       Ar の反応速度係数（L&L Table 3.3）、質量、σ_i
    materials.json      Al2O3, Si(ドープ別), SiC, 石英, Y2O3 等
    examples/           例題プロジェクト
```

（2026-11-03 更新：実装に合わせた構成。v0.2 の設計案から、`geometry.py` は `project.py`・`mesh.py` に、`dc_bias.py` は `tran.py` に、
`post/` の各モジュールは `tran_post.py` に統合し、`session.py`・`capabilities.py` は作っていません。）

- `core` は GUI に依存しません（Qt を import しない）。CLI とノートブックから直接利用できます。
- `gui` は `core` と `worker` のみに依存します。

## 3. プロセスモデル

```
 ┌────────────── GUI プロセス (CirPlas.exe) ──────────────┐
 │ UI スレッド: 画面・プロット                               │
 │ RunManager: QProcess で worker を起動、stdout(JSONL) 監視 │
 └───────────────┬───────────────────────────────────────┘
                 │ 起動引数: project.json, out.h5, mode     │ 進捗: JSON Lines
                 ▼                                         │ キャンセル: terminate シグナル
 ┌────────────── ワーカプロセス (cirplas-solve) ───────────┐
 │ core: mesh → netlist → ngspice.dll (bg_run) → post → h5 │
 │ 外側反復（自己バイアス・電力・密度）はここで完結            │
 └─────────────────────────────────────────────────────────┘
```

- **1 ジョブ = 1 ワーカプロセス**。ngspice 共有ライブラリの単一インスタンス制約と、クラッシュ隔離のためです。
- スイープは `min(論理コア数, ジョブ数)` 個のワーカを並列起動します（GUI 設定で上限変更可）。
- 進捗は 1 行 JSON（`{"t": 12.3, "stage": "tran", "cycle": 42, "metrics": {...}, "eta_s": 18}`）。
- 結果は HDF5 ファイルで受け渡し、GUI は必要なデータセットだけを遅延読み込みします。
- 小さな AC プレビュー（< 1 s）もワーカで実行します（一貫性と安全性のため）。プロセス起動のオーバヘッド（~0.3 s）は許容します。
  必要なら常駐ワーカ（stdin コマンド待ち受け）に拡張できる設計にします。

## 4. データ形式

### 4.1 プロジェクトファイル（`*.cirplas.json`）

スキーマは pydantic で定義し、`schema_version` で移行します。例（抜粋）：

```json
{
  "schema_version": 1,
  "name": "demo_300mm_60MHz",
  "geometry": {
    "regions": [
      {"name": "wafer",    "r_out": 0.150, "conductor": "W",
       "stack": [{"material": "al2o3", "t": 1.0e-3}, {"material": "si_wafer_p", "t": 0.775e-3}]},
      {"name": "edge_gap", "r_out": 0.151, "conductor": "W",
       "stack": [{"material": "al2o3", "t": 1.0e-3}]},
      {"name": "fr",       "r_out": 0.180, "conductor": "W",
       "stack": [{"material": "al2o3", "t": 1.0e-3}, {"material": "quartz", "t": 2.0e-3}, {"material": "si_fr", "t": 4.0e-3}]},
      {"name": "outer",    "r_out": 0.250, "conductor": "G",
       "stack": [{"material": "quartz", "t": 6.0e-3}]}
    ],
    "gap": {"type": "constant", "value": 0.030},
    "top": {"conductor": "ground", "stack": [{"material": "si_fr", "t": 6.0e-3}]},
    "outer_boundary": {"type": "short"}
  },
  "plasma": {
    "gas": "ar", "pressure_pa": 6.67, "gas_temperature_k": 400,
    "density": {"model": "parabolic", "n0": 1.0e16, "alpha": 0.3},
    "te": {"mode": "auto"},
    "sheath": {"model": "matrix", "alpha_min": 2.0, "stochastic": {"model": "hardwall", "kappa": 1.0}},
    "h_l": {"mode": "auto"}, "nu_m": {"mode": "auto"}
  },
  "circuit": {
    "ports": {
      "W": {"sources": [{"f": 60e6, "v": 400, "phase_deg": 0}],
            "blocking_c": 2.0e-9, "l_feed": 20e-9, "c_stray": 300e-12}
    },
    "fr": {"type": "esc_coupled"},
    "extra_netlist": ""
  },
  "numerics": {
    "mesh": {"n_r": {"wafer": 24, "edge_gap": 1, "fr": 8, "outer": 6}, "grading": {"wafer": 1.15}, "n_z": 1},
    "analysis": "tran",
    "tran": {"pts_per_cycle": 200, "ramp_cycles": 3, "cycles_min": 10, "cycles_max": 200, "meas_cycles": 4, "pss_tol": 1e-3},
    "dc_bias": {"method": "two_stage", "kappa_b": 0.02, "cycles_a": 30, "tol": 1e-3, "max_iter": 4},
    "density_iteration": {"enabled": false, "relax": 0.4, "tol": 1e-3, "max_iter": 30},
    "ngspice": {"options": {}}
  },
  "outputs": {"save_waveforms": true, "save_raw_timestep": false}
}
```

- 材料名・ガス名は `data/` の DB キー。インライン定義（`{"eps_r": 9.5, "sigma": 0}`）も可。
- `circuit.fr.type` が `separate` の場合は `fr` 領域の `conductor` を `"F"` にし、`ports.F` と `c_wf` を追加します。
- `circuit.mode` は `ports`（既定、ポートごとの直列チェーン）または `schematic`。`schematic` では `circuit.schematic` に
  `components`（`id`, `type` = R/L/C/Vdc/Vrf/GND/Label/Plasma, `x`, `y`, `rot`, `value`, `tones`, `port`, `label`）と
  `wires`（`points` の折れ線）を保存し、`circuit.ports` は検証時に回路図から導出されます（02 章 §2.4.1）。
- 密度 `table`/`file` 指定では r–n 対を与えます。

### 4.2 結果ファイル（HDF5）

```
/meta              schema_version, cirplas_version, ngspice_version, created, mode, project_hash, project_name（attrs）
/project           プロジェクト JSON（実行時の内容。SC では収束密度のテーブル・調整後の振幅を含む）
/netlist/ac, /netlist/tran   生成したネットリスト
/mesh              r_centers, r_edges, areas, region_index, l_gap, l_p, z_bulk, z_offset, z_surface_*, s_b, s_t（attrs: n_z, region_names）
/plasma            n0(r), ns(r)（attrs: te, n_g, lambda_i, h_l, h_r, nu_m, u_b, v_e, gas）
/ac                attrs: frequency, iterations
  /cells           セルごとの量（vsh_b, vsh_t, j_z, s_e, p_ohm_bulk, p_stoc_* …、複素数は振幅・位相）
  /ports/<P>       attrs: v, i, z, p
  /diagnostics     attrs: f_pe, f_psr, lambda_eff, kR_wafer, delta, delta_over_half_bulk, …
  /estimates       v1_b, v1_t, s_b, s_t（線形化の推定値）
/tran              attrs: period, cycles, meas_cycles, bias_iterations, v_dc_clamp, pss_converged, pss_change, runtime_s
  history          反復・延長の履歴（JSON）
  /cells           vsh_b_dc, vsh_b_1, vsh_b_min/max, vsh_t_dc, vsh_t_1, s_b_mean, s_b_max, s_t_mean, p_ohm_bulk, p_stoc_b/t,
                   p_ion_b/t, p_el_b/t, j_z_1, e_ion_mean, e_ion_width, s_e
  /ports/<P>       attrs: v_dc, v_1, i_1, phase_deg, p, p_source, z_re, z_im, i_dc
  /metrics         attrs: p_source_total, p_total_dissipated, power_balance_error, p_ohm_radial_total
  /map             p_ohm[r,z], jz_1[r,z], jr_1[r,z], p_ohm_radial, z_mid, r_centers, r_boundaries
  /iedf            energy[E], f[cell, E], collisionless_fraction, peak_low, peak_high
  /window          測定窓の時刻と全ベクトル（outputs.save_waveforms）
/iteration         r_centers, n[k, r], te[k], residual[k], relax[k], stage[k]（ac / tran）
                   attrs: converged, iterations, d_a, amplitudes, power_history（SC）
/log               ワーカのログ
```

（2026-11-03 更新：実装の形式。スイープは各点の `run.h5` と、全点のメトリクスをまとめた `results.json` を出力ディレクトリに書きます。）

- 単位・説明は各データセットの attrs（`unit`, `description`）に記録します。
- 大きい配列は gzip 圧縮（レベル 4）とチャンク化。

### 4.3 その他の入出力

- ネットリスト書き出し `*.cir`（ngspice でそのまま実行可能な形式。デバッグ・論文用）。
- CSV（径方向プロファイル、波形、メトリクス）、PNG/SVG（プロット）、Markdown/HTML レポート（主要図表と設定の要約）。
- ラン履歴：`<project_dir>/runs/<YYYYMMDD_HHMMSS>_<mode>/` に `project.json`、`netlist.cir`、`run.h5`、`log.txt`。

## 5. 設定・ログ

- ユーザ設定（最近使ったプロジェクト、テーマ、言語、並列数、ngspice パス）は `%APPDATA%/CirPlas/settings.json`。
- ログは Python `logging`（GUI・ワーカそれぞれにファイル）。ngspice の標準出力/エラーは `SendChar` コールバックで取り込み、
  レベル分け（`Warning:`、`Error:` 判定）してログと GUI のログペインに出します。

## 6. 拡張ポイント

| 拡張点 | 方式 |
|---|---|
| シースモデル（$Q(V)$、$I_e$、$R_{stoc}$） | `SheathModel` 抽象クラスを登録（名前で選択） |
| 加熱モデルの補正 | `HeatingCorrection` フック（後処理） |
| ガスデータ | `data/gases/*.json`（反応速度係数の Arrhenius 形または表） |
| 材料 | `data/materials.json`、プロジェクト内インライン定義 |
| 外部回路テンプレート | `external.py` にテンプレート関数を追加（GUI フォームは宣言的に生成） |
| 後処理メトリクス | `MetricPlugin` 登録 |
| 密度モデル | `DensityModel` 抽象クラス（Level 0/1、将来の軸方向分布） |

## 7. エラー処理と検証

- プロジェクト読込時に pydantic で型・範囲を検証し、幾何の整合性（領域の連続性、厚さ > 0、$R_w < R_{fr,in} \le R_{fr,out} < R_{wall}$）を確認します。
- ユーザ定義ネットリストは字句解析して禁止文（`.tran`, `.ac`, `.control`, `.option`, `.include`, `.lib`, `shell` 等）を拒否します。
- ngspice のエラー文字列は辞書で利用者向けメッセージに変換します（例：`timestep too small` → 「収束に失敗しました。ランプ周期を延ばして再試行します…」）。
- 数値失敗時の自動再試行チェーンは 02 章 §4.3 の通りで、再試行内容は結果の `/log` に記録します。

## 8. テストと CI

| 層 | 内容 | 実行環境 |
|---|---|---|
| 単体 | 要素値（解析式との一致）、メッシュ幾何、チェーンインダクタンス、ネットリスト生成のゴールデン比較、JSON スキーマ | Linux / Windows |
| 結合 | libngspice を用いた小規模回路の AC/TRAN、`Q=` 容量の電荷保存、磁束チェーン（05 章 V4） | Linux（apt/conda の libngspice） / Windows |
| 検証 | 05 章 V1〜V7（物理的妥当性） | 両方（夜間ジョブ） |
| GUI | pytest-qt によるオフスクリーン・スモークテスト、パネルの入出力バインディング | Linux（`QT_QPA_PLATFORM=offscreen`） |
| 性能 | 02 章 §7 の時間予算をベンチマークで監視 | Windows ランナー |

- GitHub Actions（実装）：Linux ジョブ（apt の ngspice 共有ライブラリで単体・結合・検証・GUI スモーク）、Windows ジョブ（MSYS2 の ngspice を取得してパッケージ作成）。
  開発機の Windows では共有ライブラリが無ければコンソール版 `ngspice_con.exe` のバッチ実行でテストが通ります。
- 開発環境（Linux）で GUI まで動作確認できることを重視します（案 A/B の利点）。

## 9. 配布とライセンス

- 配布形態（決定）：**Windows インストーラ**（Inno Setup、スタートメニュー登録、`.cirplas.json` の関連付け）を主とし、
  展開して使える zip も併せて提供します。利用者側のインストールは不要です。
- ビルド手順（実装済み、`packaging/`）：PyInstaller（`cirplas.spec`）で GUI `cirplas-gui.exe` とワーカ `cirplas-solve.exe` を 1 つの onedir バンドル
  `dist/CirPlas/` に生成し、Inno Setup（`installer.iss`、日本語既定）でインストーラを作ります。ngspice は **MSYS2 の `mingw-w64-x86_64-ngspice`**
  パッケージから `libngspice-0.dll` と依存ランタイム DLL を取得して同梱します（`fetch_ngspice_msys2.sh`）。GitHub Actions の `windows-package`
  ジョブがこれを自動実行し、インストーラとバンドルを成果物として保存します。凍結バンドルでは GUI が隣接する `cirplas-solve.exe` を起動し、
  スイープ・最適化・レポートも同じ実行ファイルのサブコマンド（`cirplas-solve sweep|optimize|shape|report`）で動かします。
  2026-10-30 に GitHub Actions 上でインストーラ生成まで成功しました（成果物 `CirPlas-windows`、約 220 MB、MSYS2 UCRT64 の ngspice 42 同梱）。
  最新版インストーラの Windows 実機での動作確認は後日実施します（06 章の追加決定）。
- UI 既定言語（決定）：日本語（英語に切替可）。
- 本ツールのライセンス（決定：OSS）：**Apache-2.0 を提案**します（特許条項を含み企業由来の OSS で一般的。MIT/BSD-3 でも可）。
  リポジトリに `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.md` を置きます。
- 第三者ライセンス：ngspice（修正 BSD）、Qt/PySide6（LGPL v3、動的リンク）、pyqtgraph（MIT）、numpy/scipy/h5py（BSD）。
  いずれも OSS 公開・商用利用に支障はありません。LGPL の要件（ライセンス表示、ライブラリ差し替え可能性）はインストーラの構成
  （PySide6 を個別ファイルとして同梱）とドキュメントで満たします。
- OSS 公開に伴い、CI（GitHub Actions）は公開リポジトリの無料枠で運用し、リリースは GitHub Releases にインストーラと zip を添付します。

## 10. セキュリティ・安全性

- ユーザ定義ネットリストから OS コマンドが実行される経路（`.control` ブロック、`shell`）を遮断します。
- ワーカはプロジェクトディレクトリと一時ディレクトリ以外に書き込みません。
- 外部ネットワークアクセスは行いません（オフライン環境で動作）。
