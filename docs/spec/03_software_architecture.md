# 03. ソフトウェア構成

## 1. 技術スタック

### 1.1 推奨案と代替案

| 項目 | **A: Python + PySide6（推奨）** | B: C++20 + Qt 6 | C: C# .NET 8 + WPF |
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

**推奨理由**：本ツールの計算負荷は ngspice（C 実装）と行列演算（numpy/scipy）が担い、
Python 層はネットリスト生成・後処理・GUI に限られます。GUI は Qt のネイティブウィジェットで描画されるため
Web ベースの UI とは異なり操作は軽快で、pyqtgraph は $10^6$ 点規模の波形をインタラクティブに扱えます。
一方、本ツールの価値はシースモデル・加熱モデル・密度モデルの改良サイクルの速さにあり、
ここで Python の生産性が最も効きます。「速さ」の要件は **UI スレッドを計算で塞がない構成**（§3）と
**解析時間の目標**（02 章 §7）で担保します。

将来、GUI だけを C++/Qt に置き換える判断をしても、コア（ワーカ CLI ＋ HDF5）は変更不要な境界で設計します。
最終決定は [06_decisions.md](06_decisions.md) Q1 で確認します。

### 1.2 バージョン方針（案 A）

- Python 3.12（3.13 でも動作確認）、PySide6 ≥ 6.7、pyqtgraph ≥ 0.13、numpy ≥ 2.0、scipy ≥ 1.13、h5py ≥ 3.11、pydantic ≥ 2.7
- ngspice 共有ライブラリ：45 以降（最新安定版 47 で検証）。同梱して版を固定。
- Windows 10/11 64-bit。Linux は開発・CI 用（GUI 含め動作可）。

## 2. モジュール構成

```
cirplas/
  core/
    constants.py        物理定数、単位変換
    project.py          プロジェクトスキーマ（pydantic）、検証、版移行
    geometry.py         領域・スタック・ギャップ形状の定義と整合性チェック
    materials.py        材料 DB（eps_r, sigma）、ガス DB（反応速度係数、質量、断面積）
    mesh.py             径方向/軸方向メッシュ、面積・長さ・高さ ζ、チェーンインダクタンス
    plasma.py           n(r), Te, nu_m, h_l, u_B, n_s, Drude 値、診断量
    sheath_models.py    Q(V) モデル（matrix, child）、I_e、R_stoc、線形化 C_lin
    netlist/
      builder.py        メッシュ＋要素値 → ネットリスト文字列（AC/TRAN 変種）
      external.py       外部回路テンプレート、ユーザ定義ネットリストの検証（禁止文・名前空間）
      naming.py         命名規約
    ngspice/
      shared.py         sharedspice ctypes バインディング、コールバック、ベクトル取得
      session.py        1 回の解析実行（投入→実行→蓄積→停止）、周期統計、測定窓
      rawfile.py        バッチモード rawfile リーダ（デバッグ用）
      capabilities.py   版・機能の自己テスト（Q= 容量、klu）
    analysis/
      ac.py             AC 解析（準線形反復、多周波重ね合わせ、周波数掃引）
      tran.py           TRAN 解析（ランプ、周期定常判定、測定窓）
      dc_bias.py        自己バイアス外側反復（Stage A/B、セカント/Broyden）
      power_drive.py    電力指定駆動（Phase 2）
      density.py        Level 1 密度反復（パワーバランス＋拡散、緩和制御）
      sweep.py          パラメータスイープのジョブ展開と集約
    post/
      resample.py, spectrum.py, heating.py, sheath.py, metrics.py, diagnostics.py
    results.py          HDF5 スキーマの読み書き、ラン履歴
  worker/
    cli.py              `cirplas-solve project.json -o run.h5 --mode tran --progress jsonl`
  gui/
    app.py              エントリポイント、テーマ、国際化
    models/             プロジェクト ↔ ウィジェットのバインディング、Undo/Redo
    panels/             geometry, plasma, circuit, numerics, run, results, compare, sweep
    plots/              pyqtgraph ベースの共通プロット部品（プロファイル、2D マップ、波形、FFT）
    runmanager.py       ワーカプロセスの起動・監視・キャンセル・並列キュー
  data/
    gases/ar.json       反応速度係数（L&L Table 3.3 形式）、質量、λ_i 係数
    materials.json      Al2O3, Si(ドープ別), SiC, 石英, Y2O3 等
    examples/           例題プロジェクト（代表構成、検証ケース）
  tests/
    unit/ integration/ validation/ gui/
```

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
      {"name": "fr",       "r_out": 0.180, "conductor": "F",
       "stack": [{"material": "quartz", "t": 2.0e-3}, {"material": "si_fr", "t": 4.0e-3}]},
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
            "blocking_c": 2.0e-9, "l_feed": 20e-9, "c_stray": 300e-12},
      "F": {"tuning": {"type": "cap_to_gnd", "c": 150e-12}}
    },
    "c_wf": {"mode": "auto"},
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
- 密度 `table`/`file` 指定では r–n 対を与えます。

### 4.2 結果ファイル（HDF5）

```
/meta              attrs: schema_version, cirplas_version, ngspice_version, created, project_hash, mode
/project           プロジェクト JSON（文字列）
/netlist           生成ネットリスト（文字列）、AC 版・TRAN 版
/mesh              r_edges, r_centers, areas, z_levels[i,j], zeta, region_index, chain_dL
/plasma            n0(r), ns(r), Te, nu_m, h_l, 診断量（omega_pe, lambda_D, delta, omega_psr, lambda_eff, kR）
/ac/f{k}           freq, V_nodes(complex)[node], I_branches(complex)[branch], node_names, branch_names
/tran
  /cycle_stats     cycle index, metrics[name][cycle]
  /window          time[N_t], V[N_t, N_node], I[N_t, N_branch]（再サンプル版）, (任意) raw_time, raw_V, raw_I
  /spectrum        harmonics[node/branch][k]（複素）
/post
  /radial          Vsh_dc_b(r), Vsh_1_b(r), Vsh_dc_t, Vsh_1_t, s_mean, s_max, z_sheath, theta, Jz_1(r),
                   S_ohm_bulk(r), S_stoc_b(r), S_stoc_t(r), S_ohm_sh(r), S_e(r), Gamma_i(r), E_ion(r)
  /map             p_ohm[r,z], Jr_1[r,z], Jz_1[r,z], Bphi_1[r,z]
  /ports           V_dc, V_1, I_1, P, Z（電極ごと）
  /metrics         uniformity_U, center_edge_ratio, std, power_balance_error, runtime_s
/iteration         k, n(r)[k], Te[k], residual[k]（Level 1）
/sweep             param_path, values, child run への参照（または埋め込み）
/log               ngspice 出力、警告
```

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

- GitHub Actions：Linux ジョブ（lint, 単体, 結合, GUI スモーク）、Windows ジョブ（公式配布の `ngspice.dll` を取得して結合・検証、パッケージ作成）。
- 開発環境（Linux）で GUI まで動作確認できることを重視します（案 A/B の利点）。

## 9. 配布とライセンス

- 配布形態：埋め込み Python 同梱の zip（展開して `CirPlas.exe` 実行）と Inno Setup インストーラの両方。`ngspice.dll` と `spinit`・コードモデル（`*.cm`）を同梱。
- 第三者ライセンス：ngspice（修正 BSD）、Qt/PySide6（LGPL v3、動的リンク）、pyqtgraph（MIT）、numpy/scipy/h5py（BSD）。
  いずれも社内利用・商用利用に支障はありません。LGPL の要件（ライセンス表示、ライブラリ差し替え可能性）はドキュメントとパッケージ構成で満たします。
- 本ツール自身のライセンスは未定（06 章 Q8）。

## 10. セキュリティ・安全性

- ユーザ定義ネットリストから OS コマンドが実行される経路（`.control` ブロック、`shell`）を遮断します。
- ワーカはプロジェクトディレクトリと一時ディレクトリ以外に書き込みません。
- 外部ネットワークアクセスは行いません（オフライン環境で動作）。
