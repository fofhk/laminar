# 外部输入评估：三篇 Odaily 文章对 Laminar 的可用性

日期 2026-08-18 · 状态 评估，未实施 · 关联 `laminar_20260814_preregistration.md`

来源：
- [5208413 六款聚合器](https://www.odaily.news/zh-CN/post/5208413)（2025-12-27，工具盘点）
- [5209735 OpenClaw 实战访谈](https://www.odaily.news/zh-CN/post/5209735)（2026-03-16，个人访谈）
- [5210581 预测市场 AI 跟单 Bot](https://www.odaily.news/zh-CN/post/5210581)（2026-05-01，赛道盘点）

三篇本身对 Laminar 增量有限——都是面向散户的工具/跟单视角，Laminar 是做市方。真正有价值的是它们**引用的三篇**：

- [5210508](https://www.odaily.news/zh-CN/post/5210508) → 论文 [Prediction Market Accuracy: Crowd Wisdom or Informed Minority?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6617059)（LBS/Yale，SSRN 6617059）
- [5207173](https://www.odaily.news/zh-CN/post/5207173) → Robin (robinmarketsxyz)、HashDive
- [5208220](https://www.odaily.news/zh-CN/post/5208220) → [Messari: Enabling Leverage on Prediction Markets](https://messari.io/report/enabling-leverage-on-prediction-markets)

---

## 一、论文 SSRN 6617059 —— 唯一一条必须改变 Laminar 现有设计的输入

论文用 sign-randomization 把 172 万个 Polymarket 账户分成技能型赢家 3.14% / 运气型赢家 29.0% / 运气型输家 61.4% / 技能型输家 6.4%。三处直接命中：

### 1.1 做市商的逆向选择成本被量化了 —— Laminar 的收益模型里没有这一项

论文「真相八」：做市商占账户总数 0.1%（约 1,660 个），人均参与 942 个市场，人均盈利 $11,832。关键在后半句——做市商的订单流**短期正向预测价格，但对最终结果是负向预测（系数 −5.69，t = −10.30）**。也就是说做市商在结算时系统性地站在错的一边，靠价差赚的钱要减去这块。

对照 2026-08-16 邮件里推出的 610–1020% 年化：那是**毛奖励收益**，逆向选择项 = 0。论文说这一项不仅不为零，而且统计上极显著。

另一个校准点：人均 $11,832 / 942 个市场 ≈ 每市场 $12.6 净利。Laminar 当前 ~$3,020 本金规模下，这个数量级和 $1/天奖励下限撞在一起看，比单看年化百分比现实得多。

→ **动作**：奖励模型加一个逆向选择项。我们已经在收 `/trades` 成交带，可以直接测：挂单被吃后、到市场结算之间的价格漂移方向。这是 Day-7 复盘时用现有数据就能算的，不用等新采集。

### 1.2 方法论可以直接搬到 PolyBeats lead 序列上

论文的 OIB（订单不平衡度）回归：技能型赢家净买入每 +1%，下期价格 +2bp、最终事件发生概率 +8bp（t = 12.71 / 9.51）；运气型赢家两项都不显著（t ≈ 1.47）。

这正是 08-25 那次「第一次真正找 pattern」该跑的检验形式，而且它**对现在的采集有要求**：要算 OIB 就需要按方向切分的成交量，不只是价格。现有 `lead_tracks` 存的是盘口快照（bid/ask/depth），存的是**挂单**不是**成交**。补 `/trades` 到 lead 序列上，才能在 08-25 跑这个回归。

### 1.3 lead 序列的先验基准 —— 必须写进 pre-registration，且必须在看数据之前

- 「真相三」：按实际利润排名的前 5.4 万交易者中，只有 12% 被识别为技能型；**88% 是运气**。
- 60% 的运气型赢家在样本外验证中变成输家。
- 但「真相四」：技能型赢家的样本外持续性 44%（对比美国主动型基金 10%），技能型输家持续性 51%。

PolyBeats 的筛选口径是**已实现盈利和胜率**，也就是论文说的排行榜口径，也就是那个 88% 是运气的人群。这不是说 lead 无用——44% 的持续性远高于传统基金——而是说：**如果 08-25 第一眼就看到漂亮的 pattern，先验上它更可能来自 88% 那一侧。** 这条先验现在写进 pre-registration 才有约束力，事后写等于没写。

「真相四」还给了一条免费的反向假设：技能型输家的持续性（51%）**高于**技能型赢家（44%）。如果我们无论如何都要拉钱包历史，顺手打一个负技能标签的成本是零。5209735 里 Kevin 说的「把傻瓜地址当反指」是同一件事的口语版。

---

## 二、今天验证过的三个免费接口（无鉴权，已实测 200）

Laminar 目前只用 `clob` / `data-api/trades` / `gamma`。以下三个都能用，都没在用：

### 2.1 `data-api.polymarket.com/activity?user=<0x>&type=TRADE&limit=&offset=`
返回单钱包完整成交流水：`timestamp / conditionId / price / size / usdcSize / side / outcome / title / slug / eventSlug / type`（TRADE 和 REDEEM 分开）。支持 `offset` 翻页。

我们已经在 `leads.wallets` 里存了 PolyBeats 帖子里的 `0x` 地址，**但一次都没用过**。这个接口让我们能：
- 独立复核帖子声称的胜率，不必相信频道的口径；
- 拿到钱包**自己的**入场价和时间，而不是帖子写的「平均买入概率」；
- 看帖子发出**之后**该钱包是加仓、减仓还是没动 —— 这是目前 lead 序列里信息量最大的未观测变量。

配套：`/value?user=`（当前持仓市值）、`/traded?user=`（参与市场数）、`/holders?market=<condition_id>`（单个 token 的持仓榜，含 proxyWallet 和数量）。

### 2.2 `clob.polymarket.com/prices-history?market=<token_id>&startTs=&endTs=&fidelity=60`
实测返回 `{"history":[{"t":unix,"p":price}...]}`，`interval` 也接受 `1m/1w/max`。

这就是 08-21 待办里「历史 332 条 lead 的价格回填」需要的那个接口——之前是假设存在，现在确认可用。已结算的 2,158 行和窗口已过的 1,348 行，都能靠它补出价格轨迹。

### 2.3 `api.elections.kalshi.com/trade-api/v2/markets`（无鉴权，AU IP 实测 200）
纯行情读取，不涉及账户、登录或 KYC，与 Kalshi 的司法管辖限制是两件事（那条限制约束的是交易，不是读公开行情）。

5208413 整篇的立论就是**同一事件跨平台存在价差和流动性断层**。Laminar 现在是单场所的，问不了这个问题。加一条只读的 Kalshi 价格序列，成本很低，但打开一个新的可观测量：**被奖励补贴的 Polymarket 盘口，相对同一事件的 Kalshi 盘口，是更紧还是更松？** 这是做市方真正关心的问题——补贴到底买到了多少真实深度。

---

## 三、跳跃风险 —— 5208220 对 shock 阈值的修正

杠杆本身与 Laminar 无关（结论是行业收敛到 1–1.5 倍，几乎无解）。有用的是它的**因**：预测市场价格是跳跃的，不是扩散的。

dYdX TRUMPWIN 案例：Polymarket YES 从 ~0.60 **直接跳到** 1.00，中间没有成交价。做市商来不及在 Polymarket 对冲，保险基金被击穿，仓位正确且抵押充足的交易者照样被强制减仓。

对做市方这就是尾部风险的物理形态：**挂单在跳跃发生的那一瞬间被成交，方向错，规模满。** Laminar 现在的 shock ledger 用 50%/25%/60% 三个「未校准的初始猜测」阈值。这篇给了一个更好的定义方式：**shock 应该定义为 gap（两个成交价之间没有中间成交），而不是百分比变动**。我们已经在收成交带，`/trades` 的连续 print 之间有没有跳空是可以直接量出来的。

→ **动作**：Day-7 复盘时本来就要重新校准这三个阈值，把「按 gap 定义」作为候选口径一起测。

---

## 四、值得记一笔、但现在不动的

**Robin** ([robinmarketsxyz](https://x.com/robinmarketsxyz))：把 YES/NO 代币存入金库，协议自动合成对手盘凑成 USDC、做 delta 中性，投进 DeFi 生息，结算时赎回并按获胜结果分配。

做市商的库存恰好就是一堆躺到结算为止的 YES/NO 代币。这是**同一笔资本上的第二层收益**，叠在流动性奖励之上。但它给目前没有任何智能合约对手方风险的本金引入了一个合约对手方，且仍在测试/积分阶段。**观察，不入。**

**Synthesis** ([synthesis.trade](https://synthesis.trade/))：统一自托管账户 + dflow 跨链桥，是名单里唯一提供**原生限价单**的。属于 Stage 3 执行层的书签，Stage 1 不需要。

**Converge** ([converge.market](https://converge.market/))：为每个平台单独配钱包、直接桥接到原平台成交，资金不经过 Converge 本身。非托管结构，在司法管辖未决的情况下这个属性值得记住。

**Verso Trading** ([verso.trading](https://www.verso.trading/))：它的筛选维度（赔率区间、15min/30min/1h/3h 价格浮动、市值变化幅度、市场创建时间、截止时间、活跃状态）本身就是一份现成的 **screener 功能清单**——正好对应待办里那条「解冻每日 pool discovery 扫描」还没定义的筛选维度。

**HashDive** ([hash_dive](https://x.com/hash_dive))：钱包评分 −100~100、巨鲸持仓、市场树状图。完整数据要付费，但它的指标设计可以参照；而 §2.1 的 `/activity` 让我们能自己算，不必买。

---

## 五、明确不采纳

5210581 三个跟单产品（Polybot / Kreo / Chance）——Laminar 不是跟单项目，做市和跟单是相反方向的生意。且该文末尾直接写了「如果有需要媒体宣传或商务合作的项目方，欢迎与 Odaily 联系」，通篇按软文口径读。

5209735 的 P&L（3 万 → 10 万，10 天）是**自述、未经验证**，且文中已回撤到 8.2 万。当作观点看，不作为证据。唯一可用的是他的仓位拆分口径：60% 自动化吃点差 / 40% 主观，且他说点差收入主要在**体育赛事**。Laminar Stage 1 采样的是「thin long-dated political markets」（见 `clob.py` docstring）。体育是短周期、高换手、可重复的，奖励与逆向选择的权衡在那里很可能不一样——**加一条只读的体育切片进采样池**，同一个 collector，只是换筛选条件，成本很低。

---

## 六、按优先级排的建议（不含实施）

| # | 动作 | 何时 | 依据 | 成本 |
|---|---|---|---|---|
| 1 | 把论文的 3.14% / 88% 先验写进 pre-registration | **看 08-25 数据之前** | §1.3 | 一次编辑 |
| 2 | 用 `prices-history` 回填 332 条历史 lead 的价格 | 08-21，不用等窗口 | §2.2 | 已在待办，接口现已确认 |
| 3 | 拉 `leads.wallets` 的 `/activity` 流水 | 08-21 | §1.3 / §2.1 | 一个新 collector |
| 4 | shock 阈值改按 gap 口径测一版 | Day-7 复盘（本就要重校准） | §3 | 用现有 trades 数据 |
| 5 | 奖励模型加逆向选择项 | Day-7 | §1.1 | 用现有 trades 数据 |
| 6 | lead 序列补成交带，为 08-25 的 OIB 回归备料 | 08-21 前 | §1.2 | 复用 `clob.trades` |
| 7 | 只读 Kalshi 行情序列 | Day-14 | §2.3 | 新 collector，接口已验证 |
| 8 | 采样池加体育切片 | Day-14 | §5 | 改筛选条件 |

1–6 全部只用已验证的免费接口，不引入依赖、不涉及下单、不触碰司法管辖问题。7–8 是扩面，可以等。
