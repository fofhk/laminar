# Laminar — 成交带钱包归属 (v1)

**日期** 2026-08-19 · **阶段** Stage 1（只读，零下单，本次未改变）
**关联** `farseer/laminar_20260814_preregistration.md`（第二条 addendum）、`farseer/laminar_20260818_external_inputs.md`

---

## 为什么做

来自 SSRN 6617059（LBS/Yale）的两个结论，在 08-18 的外部输入评估里被判定为唯一需要改动现有设计的输入：

1. 做市商订单流**对最终结算结果是负向预测**（系数 −5.69，t = −10.30）。做市商赚价差、被知情流收割。Laminar 现有奖励模型里这一项是零，610–1020% 是**毛**收益。
2. 技能型输家的样本外持续性 **51%**，高于技能型赢家的 **44%**。稳定地错比稳定地对更持久，且不需要假设任何主观动机。

用户在此基础上提出两个方向，本次只做**数据记录**，不做分类、不做策略：

- **方向 A（避险）** 高胜率地址下单 → 单向赔率变化 → 对 MM 是风险 → 记录用于评估「撤单/偏斜」方案。
- **方向 B（反指）** 持续亏损地址建仓的池子 → 考虑做对手盘或加大投放。

## 关键发现（本次实测）

- `data-api.polymarket.com/trades` 每一笔都带 **`proxyWallet`**，无鉴权。成交可归属到地址。
- 采集器 `collect.sample_trades()` **已经在收这条流，但把 `proxyWallet` 丢掉了**。这是唯一的缺口，且**不可回溯**——没记的每一小时永久缺失。
- `data-api.polymarket.com/activity?user=&type=TRADE` 是**回溯性**的：下个月才发现的钱包仍可回填到它第一笔交易。所以它**不需要**上 cron。
- 实测 `usdc_size` 与 `price × size` **不相等**（0.962 vs 1.00）。PnL 必须用 `usdc_size`。

## 改动（三个文件，外科手术式）

| 文件 | 改动 |
|---|---|
| `src/laminar/store.py` | `TRADE_SCHEMA` 加 `wallet`；trade 去重键加 `wallet`；`_append` 改 `how="diagonal"`；新增 `ACTIVITY_SCHEMA` / `append_activity` / `load_activity` |
| `src/laminar/clob.py` | 新增 `activity(wallet, limit, offset)`；`trades()` docstring 记录 `proxyWallet` 及其**未验证项** |
| `src/laminar/collect.py` | `sample_trades()` 带上 `wallet`；新增 `sample_activity()` + `activity` 子命令 |
| `tests/test_laminar_store.py` | 新增，4 个 intent 测试 |

### 两个非显然的设计点

**`_append` 用 `how="diagonal"`** — 加列后，当天已存在的 parquet 是旧 schema，`pl.concat` 默认会因形状不匹配直接抛错。那是个 cron 任务，没人每小时看输出，采集会**静默停摆**直到某次 review 发现空洞。diagonal 让旧行取 null。
只改了 `_append` 这一处；`store.py` 里另外四个 append 函数各有自己的 concat，schema 未变动，按 Rule 3 不碰。

**去重键加 `wallet`** — 一笔 tx 可能产生多条除钱包外完全相同的成交。旧键 `[tx_hash, token_id, price, size]` 会把成交方和对手方**折叠成一行**，恰好抹掉这次要测的信号。

## 验证

- 全量测试 **324 passed**（新增 4 个）。
- Schema 演进冒烟测试：旧文件（无 wallet 列）+ 新行 → 3 行，旧行 `wallet` 为 null，同 tx 两个钱包都保留。
- Live 端到端：真实钱包 → `/activity` → parquet，7 个日分区，`usdc_size` 非零。

## 待办（按依赖排序）

1. **部署到 droplet** — 未部署则本次改动不产生任何数据。cron **不需要改**：`ops/run_laminar.sh` 第 25 行直接透传 `$CMD`，`trades` 上线即自动带 wallet。
   文档记录的命令（`docs/droplet-provisioning.md:14`，本次未执行未验证）：
   ```
   rsync -az -e "ssh -i ~/.ssh/farseer_ed25519" --exclude /.venv --exclude /data \
     src/ ops/ config/ pyproject.toml root@DROPLET_IP:/opt/farseer/
   ```
2. **核对 `side` / `proxyWallet` 语义** — 用一笔已知成交反查：`proxyWallet` 是 taker 还是双边各一行？`side` 是谁的方向？**这条不解决，任何方向性结论可能整个反过来。** 已写进 `clob.trades()` docstring。
3. **先验写进 pre-registration，在看数据之前** — 3.14% 技能型 / 88% 运气；分类在 T 时刻做，只用 T 之后的成交评估。事后写的先验没有约束力。
4. Day-7 / 08-25 review 时计算核心指标：**每避免一单位逆向选择，放弃多少 reward-seconds**。撤单期间奖励为零，而奖励是主营收入——这是两个收入项之间的兑换率，不是单纯风控。

## 观察到的用户偏好

- 明确要求「客观评估」，接受负面结论（跨平台套利被判定为同一司法管辖阻塞的下游，且费用与价差同量级）。
- 倾向先建**数据记录**、后续统一 review 改善，而非一次性做完策略。
- 对「反指」思路兴趣高于「跟单」，与论文证据方向一致。
- 反复强调 Stage 1 只读边界；本次全程零下单、零密钥。

---

## 部署记录（2026-08-19 13:19 UTC）

**已部署并验证。**

文档里的部署命令 `docs/droplet-provisioning.md:14` **是错的**：`rsync -az src/ ops/ config/ ...` 带尾斜杠会把三个目录的内容摊平进 `/opt/farseer/` 根目录，而 droplet 实际保留 `src/ ops/ config/` 结构。照抄会破坏部署。且它会覆盖 `config/laminar_watchlist.json`（预注册冻结项）。

实际执行的是收窄版本：

```
rsync -azc -e "ssh -i ~/.ssh/farseer_ed25519" \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  src/laminar/ root@DROPLET_IP:/opt/farseer/src/laminar/
rsync -azc -e "ssh -i ~/.ssh/farseer_ed25519" \
  tests/test_laminar_store.py root@DROPLET_IP:/opt/farseer/tests/
```

- 部署前用 `-c` 校验和 dry-run 确认：只有 `clob.py` / `collect.py` / `store.py` 内容不同，droplet 侧无任何改动被覆盖。
- 本地与 droplet 的 watchlist md5 一致（`36e50026ea56161f32a2f9f887f86443`），冻结集未受影响。
- 备份：`/opt/farseer/backups/laminar_20260819T131856Z/`（部署前的全部 `*.py`）。
- droplet 测试 26 passed；`ops/run_laminar.sh trades` 手工跑通；真实 store 已落 wallet，325 个不同地址。
- **cron 未改动**，`trades` 每小时 :25 自动带 wallet。

## 未决：过渡日重复行（需要你决定）

`2026-08-19.parquet` 共 1116 行，其中 **530 行 wallet 为 null**，且**每一条都有一条内容完全相同、带 wallet 的孪生行**（孤儿 0 条）。原因：wallet 进了去重键，部署前记的旧行与部署后重记的新行不再互相去重。

- 影响面：**仅 2026-08-19 一天**。08-17 / 08-18 全为 null-wallet 且无重复，不受影响；08-20 起采集器全程带 wallet，不会再发生。
- 后果：08-19 当天任何**成交量 / 笔数**统计会翻倍。
- 修复是**可证明无损**的（每条待删行都有更完整的孪生行）：删掉「wallet 为 null 且存在同 `tx_hash/token_id/price/size` 的非 null 行」。

**未执行** —— 写生产 parquet 被 auto mode 分类器拦截，判断权交回用户。
替代方案（无需改数据）：所有 wallet 条件分析的窗口从 **2026-08-20** 起算，把 08-19 当作过渡日排除。这也是更符合预注册纪律的做法。

---

# 2026-08-20 Review + 语义定论 + 数据修复

## 一、采集 review（08-14 至 08-20）

| 数据集 | 规模 | 状态 |
|---|---|---|
| `books` | 60 tokens × 每分钟 | **08-16/17/18 = 1440/1440，08-19 = 1438/1440** |
| `markets` | 640,743 行/天，18,722 个 condition | 正常：这是**全量** sampling universe，不是 watchlist |
| `competitiveness` | 4,380 行 / 7 天 | 正常 |
| `trades` | 修复后 28,443 行 / 112 天 | 见第三节 |
| `leads` | 527 行 | 单文件 `leads.parquet`，非目录 |
| `lead_markets` | 4,036 行 | 事件扇出后的市场集 |
| `lead_tracks` | 37,264 行 / 3 天 | 08-18 起 |
| `shocks` | 24 行 | 阈值仍未校准，Day-7 待办 |
| `activity` | 空 | 按设计：回溯性接口，不上 cron |

**结论：08-21 计划的数据质量检查（24/24 小时通过率）实际已提前通过。** 每分钟盘口采样连续四天满格，无需再等。

## 二、`proxyWallet` / `side` 语义 —— 已定论，不再是假设

三项检验，全部在已采集数据上完成：

1. **一笔撮合只发一行**：08-20 的 939 条记录，每个 `tx_hash` 恰好 1 行，无一例外 → `proxyWallet` 是双方之一，不是双方都记。
2. **`side` 属于本行的钱包**：抽 55 笔与该钱包自己的 `/activity` 记录比对，**55 一致，0 反向**。
3. **这个钱包是 taker**：用我们自己的每分钟盘口给成交定价 → **TAKER 675 / MAKER 30 / INSIDE 234**。INSIDE 那 25% 是快照最多滞后 120 秒的正常表现，非反例。

**因此：`BUY` = 该钱包吃掉了卖一（有挂单的 ASK 被touch）；`SELL` = 吃掉买一。** 已写入 `clob.trades()` docstring 并部署。

保留的告诫：3% 读作 MAKER 虽在误差内但不为零，**任何单笔的角色判定应从盘口重新推导，不要直接假设**。

## 三、数据修复（已执行）

范围远大于 08-19 初判。我先前说「仅影响 08-19、孤儿 0 条」**是错的**——那只是因为我只查了 08-19，而它恰好是被完整重抓的一天。

全量实际：**112 个文件受影响**，48,603 行中 **20,160 条真重复**、**6,515 条孤儿**（部署前采集、之后再未被重抓；成交带无 `since` 游标，旧成交滚出窗口后不可再得，**不可替代**）。

修复脚本 `scripts/dedupe_wallet.py`（droplet 上，默认 dry run，`--apply` 才写）。

**Dry run 抓到一个会造成数据丢失的 bug**：左连接后 `_twin` 在无孪生行处为 null，而 polars 三值逻辑下 `~(True & null)` = `null`，`filter` 会**丢弃**结果为 null 的行 → 6,515 条孤儿会被静默删除。第一版的断言没拦住，因为它只校验带 wallet 的行。修法是 `fill_null(False)`，并补了一条独立计算 `want_orphans` 的断言与之交叉验证。

结果：**48,603 → 28,443 行**，与独立扫描数字完全一致；重跑幂等（-0）。
备份：`/opt/farseer/backups/trades_predede_20260820T131944Z`（已移出 data 目录，避免被任何 glob 误读为数据）。

修复后 08-18/19/20 的 `null-wallet` 均为 0，08-20 全天 100% 带钱包、490 个地址。

## 四、下一步

1. **08-25 之前**：先验（3.14% / 88%）写进预注册；分类在 T 时刻定、只用 T 之后成交评估。
2. **Day-7**：`shocks` 阈值按 gap 口径重新校准（现仍是未校准初始猜测，仅 24 行样本）。
3. 奖励模型加逆向选择项；计算「每避免一单位逆向选择放弃多少 reward-seconds」。
4. 方向 B 需要时用 `python -m laminar.collect activity 0x...` 按需回填，不必提前跑。
