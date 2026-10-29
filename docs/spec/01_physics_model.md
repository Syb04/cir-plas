# 01. 物理モデル

本章では、放電空間と周辺構造を **どのような回路網に写像するか**、および各回路要素の
**物理的な値の決め方**を定義します。ngspice 上での具体的な記述方法は 02 章で扱います。

## 1. 座標系と幾何

- 円筒座標 $(r, z)$、軸対称（$\partial/\partial\phi = 0$）。扱う電磁モードは TM（$E_r, E_z, B_\phi$）のみ。
- $z = 0$ を **ウェハ電極（ESC 内部電極）の上面**とし、上向きを正とします。
- 径方向は複数の **領域 (region)** に分かれ、各領域は「下部導体の種類」と「下部スタック（誘電体/半導体層の積層）」を持ちます。

| 領域名 | 範囲（デフォルト） | 下部導体 | 下部スタック（下→上） |
|---|---|---|---|
| `wafer` | $0 \le r < R_w$ | ウェハ電極 `W` | ESC セラミック → Si ウェハ |
| `edge_gap` | $R_w \le r < R_{fr,in}$ | ウェハ電極 `W` または FR 電極 `F`（指定） | ESC セラミック（露出）など |
| `fr` | $R_{fr,in} \le r < R_{fr,out}$ | FR 電極 `F` | 結合リング（誘電体）→ FR |
| `outer` | $R_{fr,out} \le r < R_{wall}$ | 接地シールド `G` | カバーリング（石英等） |

- 上部構造：上部導体 `T`（接地または駆動ポート）と、その下面の上部スタック（例：Si プレート）。
- ギャップ $l(r)$（下部スタック上面から上部スタック下面まで）は定数またはテーブル指定（シェイプト電極対応）。
- 各層は材料 $(\varepsilon_r, \sigma)$ と厚さ $t$ を持ちます。$\sigma$ を与えることで、ドープ Si のように RF では導体として振る舞う層（$\sigma \gg \omega\varepsilon_r\varepsilon_0$）も同一の枠組みで扱います。

## 2. 回路メッシュ化

### 2.1 径方向分割

- セル境界 $r_0 = 0 < r_1 < \dots < r_{N_r} = R_{wall}$。領域境界（$R_w, R_{fr,in}, R_{fr,out}$）は必ずセル境界に一致させます。
- 環状セル $i$ の面積 $A_i = \pi (r_i^2 - r_{i-1}^2)$、代表半径 $\bar r_i = (r_{i-1} + r_i)/2$。
- 領域ごとに分割数と **幾何級数グレーディング**（ウェハエッジ側を細かく）を指定できます。デフォルトは `wafer` 24 分割（エッジ側を細密）、`edge_gap` 1、`fr` 8、`outer` 6。

### 2.2 軸方向構造（セル $i$ の縦方向回路）

各環状セルは下から順に次のノード列を持ちます。

```
   下部導体ノード  (W / F / G のいずれか。領域で決まる。セル間で共有)
        │  下部スタック層 1 (R‖C)        ... 層ごとにノード
        │  下部スタック層 m (R‖C)
   S_b(i) 下部表面ノード
        │  下部シース（非線形サブサーキット）
   B(i,1) バルクノード 1
        │  バルク軸方向セグメント 1 (L_e–R_e ‖ C)
   B(i,2)
        │  ...
   B(i,Nz+1)
        │  上部シース（非線形サブサーキット）
   S_t(i) 上部表面ノード
        │  上部スタック層 (R‖C)
   T(i)  上部導体ノード（セルごとに別ノード。径方向に磁束チェーン起電力で連結）
```

- バルク層数 $N_z$ はデフォルト 1（表皮効果を解像しない）。表皮効果を評価する場合は 4〜8 層とし、シース側を細かくグレーディングします（§4.4）。
- 同じ $z$ レベルで隣接するセルは **径方向ブランチ**（§2.3）で結ばれます。

### 2.3 径方向ブランチ

セル境界 $r_{i+1/2} = r_i$（セル $i$ と $i+1$ の境界）、レベル $j$ の径方向ブランチは、
ノード $X(i,j)$ と $X(i+1,j)$ を結び、次の 2 要素を直列に含みます。

1. **媒質インピーダンス**：そのレベルの媒質（バルク：Drude L–R‖C、層：R‖C、シース：C のみ、上部導体：0）。
   長さ $\Delta r_{i+1/2} = \bar r_{i+1} - \bar r_i$、断面積 $S = 2\pi r_i \Delta z_j$。
2. **磁束チェーン起電力**（§4）：ファラデーの法則による誘導起電力。

隣接する 2 セル（$N_z = 1$）の回路トポロジを模式的に示します（`~~` は磁束チェーン起電力、`=` は媒質インピーダンス）。

```
   T(i) ──~~E~~──=====──── T(i+1) ──~~E~~──  ... ──► 外周境界（short/open）
    │                        │
 [上部シース]             [上部シース]
    │                        │
   B(i,1) ──~~E~~──=L–R‖C=── B(i+1,1) ──~~E~~──  ...
    │                        │
 [下部シース]             [下部シース]
    │                        │
   Sb(i) ──~~E~~──=R‖C=──── Sb(i+1) ──~~E~~──  ...      ← ウェハ層（導電性なら R 小）
    │                        │
 [ESC 容量]               [ESC 容量]
    │                        │
   W ────────────────────────┴──────────────  ...      ← 参照導体（1 ノード）

   セル境界 i の磁束チェーン:  0 ─ΔL1─ A(i,1) ─ΔL2─ A(i,2) ─ΔL3─ A(i,3)
                               ▲ 各レベルの径方向電流を F 源で注入、E 源が V(A(i,k)) を起電力として返す
```

下部導体は **参照導体**として扱い、径方向ブランチを持ちません（1 つのノード）。
したがって径方向電流が流れる経路は、下部スタック層、バルク各層、（無視できる変位電流として）シース層、上部導体です。

### 2.4 ノード・ブランチ数の目安

$N_r = 39$、$N_z = 1$、下部スタック 2 層、上部スタック 1 層のとき、ノード数は約 $39 \times 8 \approx 310$、
磁束チェーンの補助ノードを含めて約 500、未知数（電流を含む）は 1000 弱です。
$N_z = 6$ では約 2 倍になります。

## 3. 要素モデル

### 3.1 誘電体・半導体層

厚さ $t$、面積 $A$、材料 $(\varepsilon_r, \sigma)$ の層の **軸方向**要素：

$$C = \frac{\varepsilon_r \varepsilon_0 A}{t}, \qquad R = \frac{t}{\sigma A} \quad (\sigma = 0 \text{ なら省略})$$

**径方向**要素（セル境界半径 $r_i$、層厚 $t$、長さ $\Delta r$）：

$$C_r = \frac{\varepsilon_r \varepsilon_0\, 2\pi r_i t}{\Delta r}, \qquad R_r = \frac{\Delta r}{\sigma\, 2\pi r_i t}$$

ドープ Si ウェハ（$\rho \sim 1$–$10\ \Omega\,\mathrm{cm}$）や導電性 FR では $R_r$ が小さく、層上面はほぼ等電位になります。
これは「ウェハ面が電極として振る舞う」ことを自動的に再現します（§4.5）。

### 3.2 シース（非線形サブサーキット）

セル $i$ の下部（上部）シースは、面積 $A_i$、局所シース端密度 $n_s$、電子温度 $T_e$ をパラメータとする
サブサーキットで、プラズマ側ノード $P$ と壁側ノード $W$ を結びます。
シース電圧 $V = V_P - V_W$（通常 $\ge 0$）。

```
        P ──┬──────────┬──────────────┬──── 
            │          │              │
         [I_i]↓     [I_e(V)]↑     [C_sh: Q(V)]
            │          │              │
            │          │           [R_stoc]
            │          │              │
        W ──┴──────────┴──────────────┴────
```

| 要素 | 定義 | 備考 |
|---|---|---|
| イオン電流 $I_i$ | $I_i = e\, n_s u_B A_i$、$u_B = \sqrt{e T_e / M}$ | 定電流源（$P \to W$）。周期内で一定 |
| 電子電流 $I_e(V)$ | $I_e = I_{e0} \exp(-V/T_e)$、$I_{e0} = \tfrac14 e n_s \bar v_e A_i$、$\bar v_e = \sqrt{8 e T_e/(\pi m_e)}$ | $W \to P$ 向き。$V<0$ では $I_{e0}(1 - V/T_e)$ に線形外挿して数値発散を防ぐ |
| シース電荷 $Q(V)$ | 下式 | 電荷ベース非線形容量（02 章 §2.3） |
| 確率的抵抗 $R_{stoc}$ | $R_{stoc} = \dfrac{m_e \bar v_e}{e^2 n_s A_i}$ | hard-wall モデルの線形等価抵抗。容量経路に直列 |

**シース厚と電荷**：準静的シース幅 $s(V) = s_1 (V/T_e)^{\beta}$ を仮定し、
$C(V) = \varepsilon_0 A_i / s(V)$ を積分して

$$Q(V) = \frac{\varepsilon_0 A_i\, T_e^{\beta}}{s_1 (1-\beta)}\,(V + V_{min})^{1-\beta}$$

| モデル | $\beta$ | $s_1$ | 備考 |
|---|---|---|---|
| matrix（一様イオン密度） | $1/2$ | $\sqrt{2}\,\lambda_D$ | $Q = A_i\sqrt{2\varepsilon_0 e n_s (V+V_{min})}$。Lieberman 均質モデルと整合 |
| Child–Langmuir | $3/4$ | $(2^{5/4}/3)\,\lambda_D$ | $s = \tfrac{\sqrt2}{3}\lambda_D (2V/T_e)^{3/4}$（L&L 6.3） |

ここで $\lambda_D = \sqrt{\varepsilon_0 T_e/(e n_s)}$。$V_{min}$ は最小シース幅 $s_{min} = \alpha_{min}\lambda_D$（$\alpha_{min}$ デフォルト 2）に対応する電圧で、
$s(V_{min}) = s_{min}$ から決めます（matrix では $V_{min} = \alpha_{min}^2 T_e/2$）。

**確率的抵抗の根拠**：hard-wall モデル（L&L §11.2）で、シース端速度 $u_s(t) = J_C(t)/(e n_s)$（$J_C$ はシース容量経路の電流密度）のとき、
時間平均の確率的加熱は $\bar S_{stoc} = m_e n_s \bar v_e \langle u_s^2\rangle$ です。
これを $\langle I_C^2\rangle R_{stoc} / A_i$ と書き直すと上の $R_{stoc}$ が得られ、任意波形（多周波）にそのまま適用できます。
Kaganovich らの補正等を取り込むため係数 $\kappa_{stoc}$（デフォルト 1）を掛けられるようにします。

**$n_s$ の決め方**：$n_s(r) = h_l\, n_0(r)$。$h_l = 0.86\,(3 + l_p/(2\lambda_i))^{-1/2}$（L&L 5.3）、
イオン平均自由行程 $\lambda_i = 1/(n_g \sigma_i)$（Ar では $\lambda_i \approx 1/(330\,p[\mathrm{Torr}])$ cm）。手動指定も可。

### 3.3 バルクプラズマ（Drude 媒質）

電子密度 $n$、運動量緩和周波数 $\nu_m = n_g K_m(T_e)$ のバルクの複素導電率は

$$\sigma_p(\omega) = \frac{\varepsilon_0 \omega_{pe}^2}{\nu_m + j\omega}, \qquad \omega_{pe} = \sqrt{\frac{n e^2}{\varepsilon_0 m_e}}$$

であり、長さ $\ell$・断面積 $S$ のセグメントは **直列 L–R と並列 C** で正確に表せます。

$$L_e = \frac{m_e\, \ell}{e^2 n S}, \qquad R_e = \nu_m L_e, \qquad C_{disp} = \frac{\varepsilon_0 S}{\ell}$$

- 軸方向セグメント（セル $i$、層 $j$）：$\ell = \Delta z_j$、$S = A_i$。
- 径方向セグメント（境界 $r_i$、層 $j$）：$\ell = \Delta r_{i+1/2}$、$S = 2\pi r_i \Delta z_j$。
- $n$ はセル中心での時間平均密度（RF 周期内の密度変動は無視。イオンは RF に追従しないという標準的仮定）。
- 層ごとに密度を変える（軸方向プロファイル）ことも可能ですが、v1 では径方向分布のみ（軸方向一様）とします。

### 3.4 導体電極

- ウェハ電極 `W`、FR 電極 `F`、接地シールド `G`、上部導体 `T` は完全導体。
- 下部導体はすべて **参照導体**（径方向ブランチなし、1 ノード）。
- 上部導体は各セルにノード $T(i)$ を持ち、隣接セル間を径方向ブランチ（インピーダンス 0、磁束チェーン起電力のみ）で連結します。
  外周端 $T(N_r)$ は境界条件（§6）に従って接地等に接続します。
- 下部導体間の直接結合（例：ESC 電極と FR 電極間の静電容量 $C_{WF}$）は外部回路要素として与えます（自動推定値を提示）。

## 4. 電磁結合：磁束チェーン（定在波・表皮効果）

### 4.1 なぜ必要か

Kirchhoff 則だけの RLC 網では、ギャップ内の磁場 $B_\phi$ による誘導起電力（ファラデーの法則）が入りません。
定在波効果（径方向に伝搬する波）と表皮効果（バルク表層への電流集中）は、いずれもこの誘導起電力が本質です。
Lieberman ら（2002）はこれを **径方向伝送線路**として解析しました。本ツールはその考え方を一般化し、
軸方向多層・複数電極・任意外部回路に対応する **回路要素**として実装します。

### 4.2 定式化

アンペールの法則（変位電流を含む）より、半径 $r$、高さ $z$ における方位角磁場は

$$B_\phi(r,z) = \frac{\mu_0\, I_{enc}(r,z)}{2\pi r}, \qquad I_{enc}(r,z) = \int_0^{r} J_z(r',z)\, 2\pi r' \mathrm{d}r'$$

（$I_{enc}$：半径 $r$ の円板を貫く全軸方向電流）。電荷保存から、$I_{enc}(r_i, z)$ は
**半径 $r_i$ の円筒面を高さ $z$ より上で横切る径方向電流の総和**に等しくなります
（上部導体の径方向電流を含む。上部導体より上には電流が無いため）：

$$I_{enc}(r_i, z) = \sum_{j:\ \zeta_j > z} I_r(i{+}\tfrac12, j)$$

ここで $\zeta_j$ はレベル $j$ の径方向ブランチの高さ（下部導体面を基準）、$I_r$ は $+r$ 向きを正とする径方向ブランチ電流です。

セル境界 $r_i$ で、レベル $j$ と $j+1$ の径方向ブランチに挟まれたループ（幅 $\Delta r_{i+1/2}$、高さ $\Delta\zeta_{j+1/2} = \zeta_{j+1} - \zeta_j$）を貫く磁束は
$\Phi = \mu_0 \Delta r \Delta\zeta_{j+1/2} I_{enc} / (2\pi r_i)$ であり、ファラデーの法則はこのループの起電力 $\mathrm{d}\Phi/\mathrm{d}t$ を要求します。
これを満たすには、レベル $j$ の径方向ブランチに次の誘導電圧（$+r$ 方向の電圧降下）を直列に挿入すればよいことが示せます：

$$V_j^{ind} = \sum_{j'} L_{jj'}\, \frac{\mathrm{d} I_{j'}}{\mathrm{d}t}, \qquad L_{jj'} = \frac{\mu_0\, \Delta r_{i+1/2}}{2\pi r_i}\, \min(\zeta_j, \zeta_{j'})$$

すなわち、同じセル境界上の径方向ブランチ群は **相互インダクタンス行列 $L_{jj'} \propto \min(\zeta_j,\zeta_{j'})$** で結合しています。
この行列（ブラウン運動の共分散行列と同形）は正定値で、物理的に実現可能です。

### 4.3 回路実現：磁束チェーン

相互インダクタンスを K 結合で書く代わりに、次の **補助回路（磁束チェーン）**で等価かつ簡潔に実現します。

```
  レベル j の径方向ブランチ:
     X(i,j) ──[E_j = V(A_j) − V(A_0)]──[Z_media(j)]──[V_sense_j]── X(i+1,j)

  磁束チェーン（セル境界 i+1/2 ごとに 1 本）:
     A_0 ──[ΔL_1]── A_1 ──[ΔL_2]── A_2 ── ... ──[ΔL_N]── A_N
     各 j について F_j : I(V_sense_j) を A_0 → A_j に注入（電流制御電流源、利得 1）
     ΔL_k = μ0 Δr_{i+1/2} (ζ_k − ζ_{k−1}) / (2π r_i)
```

- チェーンのインダクタ $\Delta L_k$ に流れる電流は $\sum_{j \ge k} I_j = I_{enc}$（レベル $k$ の高さでの包含電流）になり、
  $V(A_j) - V(A_0) = \sum_{k \le j} \Delta L_k\, \mathrm{d}I_{enc,k}/\mathrm{d}t$ が §4.2 の $V_j^{ind}$ と厳密に一致します。
- 必要な要素数はセル境界あたり $N$ 個のインダクタと $N$ 組の制御源（K 結合なら $N(N+1)/2$ 個）で、ネットリストの可読性も高くなります。
- $\Delta L_k$ は幾何（高さと半径）だけで決まり、プラズマパラメータに依存しません。$\zeta$ はシース厚を含む **公称高さ**（時間平均シース幅）を用います。
  シース厚の時間変動による磁束の再配分は無視します（$\delta \gg$ シース厚なので影響は小さい）。
- 下部導体が領域境界で切り替わるセル境界（例：$R_{fr,in}$ 付近）では、基準高さは両側の下部導体面の平均を用います。

### 4.4 表皮効果の表現

- 無衝突表皮深さ $\delta = c/\omega_{pe}$（$n = 10^{16}\ \mathrm{m^{-3}}$ で 5.3 cm、$10^{17}$ で 1.7 cm）、
  衝突性 $\delta_c = \sqrt{2/(\omega\mu_0\sigma_{dc})}$。実効値は $\sigma_p(\omega)$ から直接評価します。
- $\delta$ がバルク半厚 $l_p/2$ に比べて小さいとき、バルク内部の径方向電流は表層に集中し、磁束チェーンの結合を通じて軸方向電流分布・加熱分布が変わります（エッジ高）。
- これを解像するには、バルクを $N_z \ge 4$ 層に分け、シース側の層厚を $\lesssim \delta/3$ にします。GUI は $\delta/(l_p/2)$ を表示し、推奨 $N_z$ を提案します。
- $N_z = 1$ の場合、本モデルは Lieberman らの径方向伝送線路モデル（$L' = \mu_0 l/(2\pi r)$、シャント＝シース–バルク–シース）に帰着し、
  定在波効果のみを再現します。

### 4.5 2 電極構成での振る舞い（定性的な期待）

- 導電性ウェハの上面はほぼ等電位になるため、ウェハ領域内の定在波による電圧変動は **シース＋バルク**に現れます。これは一体電極の場合と同じ挙動です。
- ウェハ電極と FR 電極の電位差（振幅・位相）は、FR 上のシース電圧と、ウェハエッジ付近のバルク径方向電流（バルク層の径方向ブランチ）を通じて
  ウェハエッジのシース電圧・シース厚に影響します。これが「FR 調整によるエッジ均一性制御」の回路的表現です。
- 従って $N_z = 1$ でも **バルク層の径方向ブランチ**は必須です（これが無いと領域間の結合が外部回路の $C_{WF}$ だけになってしまいます）。

## 5. 外部回路とポート

メッシュは次の **ポート**を外部回路に公開します。

| ポート名 | 内容 |
|---|---|
| `W` | ウェハ電極 |
| `F` | FR 電極 |
| `T` | 上部導体（接地の場合はポート無し） |
| `0` | 接地（チャンバ壁・接地シールド） |

### 5.1 テンプレート要素（GUI フォームで設定）

- **電源**：ポートごとに複数トーン $V(t) = \sum_k V_k \sin(2\pi f_k t + \phi_k)$、立ち上がりランプ（数周期）付き。
  指定方法は「電圧振幅」「電流振幅」「供給電力」（電力指定は外側反復、Phase 2）。
- **ブロッキング容量** $C_b$（直列）、**給電インダクタンス** $L_{feed}$（直列）、**ストレイ容量** $C_{stray}$（ポート–接地）。
- **FR 調整回路**：`none`（開放）、`tie`（ウェハ電極と短絡）、`cap_to_gnd`（可変 C）、`series_LC_to_gnd`、`coupling_to_W`（W–F 間インピーダンス）、`dc_bias`（RF フィルタ経由 DC）、`source`（独立電源）。
- **電極間容量** $C_{WF}$（自動推定：同軸ギャップ近似、または手入力）。
- **整合器**（Phase 2）：L 型 / π 型、50 Ω 発振器、AC 解析で得たインピーダンスから自動チューニング。

### 5.2 ユーザ定義ネットリスト

上級者向けに、予約ノード名（`W`, `F`, `T`, `0`）に接続する任意の SPICE 文を挿入できます（02 章 §2.6）。

## 6. 境界条件

| 位置 | 条件 |
|---|---|
| 中心軸 $r=0$ | 径方向ブランチ無し（開放）。$I_{enc} = 0$ |
| 外周 $r = R_{wall}$ | `short`：$T(N_r)$ を接地（チャンバ壁） / `open`：無接続（誘電体コンファインメント） / `impedance`：任意 R‖C |
| 上部導体 | `ground`：全 $T(i)$ の鎖の端を接地 / `driven`：ポート `T` に接続（鎖の端） |
| 下部外周領域 | 接地シールド `G` = 接地ノード `0` |

## 7. 電子加熱の算出

周期定常後の測定窓（$N_{meas}$ 周期）で時間平均 $\langle\cdot\rangle$ を取ります。

| 量 | 定義 | 単位 |
|---|---|---|
| バルクオーミック（セル $i$、層 $j$） | $P_{ohm}(i,j) = \langle I_{ax}^2\rangle R_{e,ax} + \tfrac12\left[\langle I_{r,i-\frac12}^2\rangle R_{e,r} + \langle I_{r,i+\frac12}^2\rangle R_{e,r}\right]$ | W |
| 同・体積密度 | $p_{ohm}(i,j) = P_{ohm}(i,j)/(A_i \Delta z_j)$ | W/m³ |
| 確率的加熱（下部/上部シース） | $S_{stoc}(i) = \kappa_{stoc}\,\langle I_C^2\rangle R_{stoc}/A_i$ | W/m² |
| シース内オーミック（オプション） | $S_{ohm,sh}(i) = \kappa_{sh}\, \dfrac{m_e \nu_m}{e^2 n_s}\,\langle J_C^2\rangle\, \bar s$（係数は L&L §11.2 の均質モデルに合わせて確定） | W/m² |
| 電子加熱面密度 | $S_e(r_i) = \sum_j P_{ohm}(i,j)/A_i + S_{stoc,b}(i) + S_{stoc,t}(i) + S_{ohm,sh}(i)$ | W/m² |
| イオン加速電力 | $P_{ion}(i) = I_i \langle V_{sh}\rangle$（電子加熱には含めない） | W |
| 誘電体損 | $\langle I^2\rangle R$（損失層） | W |

**電力収支チェック**：全電源の供給電力 $\sum \langle V I\rangle$ が、上記すべての散逸（＋外部回路の抵抗損）の和と
1 % 以内で一致することを毎回確認し、結果に記録します。

## 8. 電子密度と電子温度

### 8.1 Level 0：与える（Phase 1）

- $n_0(r)$：`uniform` / `parabolic`（$n_0[1 - a (r/R)^2]$）/ `table`（r–n 対）/ `file`（CSV）。
- $T_e$：`auto`（全体粒子バランス）/ 手動。粒子バランスは

$$K_{iz}(T_e)\, n_g\, d_{eff} = u_B, \qquad d_{eff} = \frac{1}{2}\frac{R\, l_p}{R h_l + l_p h_R}$$

  （L&L 10.2、$h_R = 0.80 (4 + R/\lambda_i)^{-1/2}$）。
- Ar の反応速度係数は L&L Table 3.3 の Arrhenius 形（$K_{iz}, K_{ex}, K_{el}$）をデフォルトデータとし、他ガスはデータファイルで追加します。

### 8.2 Level 1：自己無撞着（Phase 2）

回路解から得た電子加熱面密度 $S_e(r)$ を用い、径方向の **電子パワーバランス＋両極性拡散**を解きます。

$$-\frac{1}{r}\frac{\mathrm{d}}{\mathrm{d}r}\left(r D_a \frac{\mathrm{d}n}{\mathrm{d}r}\right) + \frac{2 h_l u_B}{l_p}\, n = \frac{S_e(r)/l_p}{e\, \varepsilon_{T,e}}$$

- 右辺：電子加熱体積密度を、電子–イオン対 1 組を生成・壁へ運ぶのに要する電子側エネルギー $\varepsilon_{T,e} = \varepsilon_c(T_e) + 2T_e$ で割った **生成率**。
  $\varepsilon_c = (K_{iz}E_{iz} + K_{ex}E_{ex} + K_{el}\, 3 (m_e/M) T_e)/K_{iz}$（L&L 3.5）。
- 左辺第 2 項：上下シースへの軸方向損失（Bohm 流束）。
- $D_a \approx \mu_i T_e = e T_e/(M \nu_{in})$（$T_e \gg T_i$）、$\nu_{in} = u_B/\lambda_i$ 近似（ユーザ上書き可）。
- 境界条件：$\mathrm{d}n/\mathrm{d}r|_{r=0} = 0$、外壁 $r = R_{wall}$ で $-D_a\, \mathrm{d}n/\mathrm{d}r = h_R u_B n$。
- $T_e$ は §8.1 の粒子バランスで決まる一様値（$n$ に弱く依存するため反復内で更新）。
- 離散化：径方向セル中心での有限体積法、三重対角解。

**反復**（02 章 §6 に擬似コード）：

1. $n^{(0)}(r)$ を Level 0 と同様に設定。
2. 回路要素（$n_s, L_e, R_e, R_{stoc}, I_i, I_{e0}, Q(V)$）を更新し、回路を解く（内側は AC 線形モードで高速に、最終は TRAN）。
3. $S_e(r)$ を評価し、上式から $\hat n(r)$ を得る。
4. 緩和 $n^{(k+1)} = (1-\alpha) n^{(k)} + \alpha \hat n$（$\alpha$ デフォルト 0.4、振動検出時に半減）。
5. $\max_r |n^{(k+1)} - n^{(k)}| / n^{(k)} < \epsilon$（デフォルト $10^{-3}$）で収束。最大反復回数既定 30。

### 8.3 既知の限界

- $T_e$ の径方向変化、非局所電子運動論、二温度分布は扱いません。
- 高圧（$\gtrsim$ 数百 mTorr）で局所的な電子エネルギー緩和が支配的になる場合は、$S_e(r)$ と $n(r)$ の対応が局所化し、本モデルの拡散項が過小になる可能性があります。
- 密度の軸方向分布は一様とし、$h_l$ でシース端密度を関係づけます。

## 9. イオン・シース境界に関する後処理

| 量 | 定義 |
|---|---|
| DC シース電圧 $\langle V_{sh}\rangle(r)$ | 測定窓での時間平均。イオン衝突エネルギーの推定値 $E_i \approx e\langle V_{sh}\rangle$（無衝突） |
| RF 変調幅（オプション） | $\Delta E \approx \dfrac{2 e V_{sh,1}}{\omega \tau_i}$ 型の近似（$\tau_i$：イオン通過時間）から bimodal 幅を推定 |
| シース厚 $\bar s(r), s_{max}(r)$ | $s(V(t))$ の時間平均と最大 |
| シース境界プロファイル | $z_s(r) = z_{surf}(r) + \bar s(r)$、傾き角 $\theta(r) = \arctan(\mathrm{d}z_s/\mathrm{d}r)$（ウェハエッジ近傍の評価指標） |
| イオン流束 $\Gamma_i(r)$ | $n_s(r) u_B$（Level 1 では分布が得られる） |
| 不均一性指標 | $U = (\max - \min)/(2\,\mathrm{mean})$、中心/エッジ比、標準偏差。評価範囲 $r \le R_w - \Delta_{edge}$（既定 3 mm） |

## 10. 解析式（検証・診断用）

以下は回路解の妥当性確認と GUI 上の診断表示に用います。

- プラズマ周波数 $\omega_{pe}$、デバイ長 $\lambda_D$、表皮深さ $\delta = c/\omega_{pe}$、衝突周波数 $\nu_m$。
- **PSR**（1 セル・対称）：$\omega_{PSR} = \omega_{pe}\sqrt{2\bar s/l_p}$。一般には $1/\sqrt{L_{tot} C_{tot}}$ を AC 周波数掃引で検出。
- **径方向伝送線路の実効波長**：全シース幅 $s_{tot}$（上下合計）、全ギャップ $l$ に対し

$$\lambda_{eff} \approx \lambda_0 \sqrt{\frac{s_{tot}}{l}}\;\sqrt{1 - \frac{\omega^2}{\omega_{PSR}^2}}$$

  これは Lieberman ら（2002）の結果（半ギャップ $l_{half}$、片側シース幅 $s$ の記法で $\lambda \approx \lambda_0\sqrt{s/l_{half}}$、
  均一条件 $\lambda_0 \gg 2.6\,(l_{half}/s)^{1/2} R$、すなわち $J_0(kR)$ の第 1 零点 $kR = 2.405$ より十分小さいこと）と同じものです。
  線形・一様・$N_z = 1$ の回路解は $|V_{sh}(r)| \propto J_0(k r)$、$k = 2\pi/\lambda_{eff}$ を再現しなければなりません（05 章 V2）。
- **表皮効果の目安**：$\delta$ とバルク半厚 $l_p/2$ の比。$\delta \lesssim l_p/2$ で表皮効果が顕著。

## 11. 仮定と制約（まとめ）

1. 軸対称、TM モードのみ。
2. イオン密度は RF 周期内で一定。密度は軸方向一様（$h_l$ で端を補正）。
3. シースは準静的（$s(V)$ の瞬時関係）。イオンのシース通過時間効果は IEDF 推定でのみ考慮。
4. 電子はマクスウェル分布、$T_e$ は空間一様。
5. 磁束チェーンの高さは公称値（シース厚の時間変動は磁束計算では無視）。
6. 誘電体・半導体層は線形（$\varepsilon_r, \sigma$ 一定）。
7. 外部回路は線形集中定数（ユーザ定義ネットリストで非線形要素を入れることは可能）。
8. 二次電子放出、電子ビーム、磁場は無し。

## 12. 参考文献

1. M. A. Lieberman and A. J. Lichtenberg, *Principles of Plasma Discharges and Materials Processing*, 2nd ed., Wiley (2005). — 本書で「L&L」。均質モデル、シース、加熱、全体モデル。
2. M. A. Lieberman, J. P. Booth, P. Chabert, J. M. Rax, M. M. Turner, "Standing wave and skin effects in large-area, high-frequency capacitive discharges," *Plasma Sources Sci. Technol.* **11**, 283 (2002).
3. P. Chabert, "Electromagnetic effects in high-frequency capacitive discharges used for plasma processing," *J. Phys. D: Appl. Phys.* **40**, R63 (2007).
4. P. Chabert and N. Braithwaite, *Physics of Radio-Frequency Plasmas*, Cambridge Univ. Press (2011), Ch. 10.
5. G. A. Metze, D. W. Ernie, H. J. Oskam, "Application of the physics of plasma sheaths to the modeling of rf plasma reactors," *J. Appl. Phys.* **60**, 3081 (1986).
6. M. A. Sobolewski, "Electrical characterization of radio-frequency discharges in the Gaseous Electronics Conference Reference Cell," *J. Vac. Sci. Technol. A* **10**, 3550 (1992).
7. T. Mussenbrock and R. P. Brinkmann, "Nonlinear electron resonance heating in capacitive radio frequency discharges," *Appl. Phys. Lett.* **88**, 151503 (2006).
8. U. Czarnetzki, T. Mussenbrock, R. P. Brinkmann, "Nonlinear plasma dynamics in capacitive radio frequency discharges," *Phys. Plasmas* **13**, 123503 (2006).
9. M. A. Lieberman, "Analytical solution for capacitive RF sheath," *IEEE Trans. Plasma Sci.* **16**, 638 (1988).
10. L. Sansonnens and J. Schmitt, "Shaped electrode and lens for a uniform radio-frequency capacitive plasma," *Appl. Phys. Lett.* **82**, 182 (2003).
11. A. Perret, P. Chabert, J.-P. Booth, J. Jolly, J. Guillon, Ph. Auvray, "Ion flux nonuniformities in large-area high-frequency capacitive discharges," *Appl. Phys. Lett.* **83**, 243 (2003).
12. ngspice User's Manual（対象版は 02 章参照）。
