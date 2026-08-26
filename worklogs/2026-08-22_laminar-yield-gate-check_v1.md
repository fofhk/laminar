# Laminar — 年化收益 gate 核实（9/4 复核前置准备，非决策）

- 日期：2026-08-22
- 版本：v1
- 前置：`laminar_20260814_preregistration.md`（gate 原文）
- 阶段：Stage 1 只读，本次全部是对已存书面快照的离线复算，未写库、未改任何生产代码
- 定位：**这不是 9/4 的正式复核**。预注册写的复核时点是"采集满 21 天（2026-09-04），或连续 3 天丢样 >5%，以先到者为准"——
  今天是第 9 天，两个触发条件都没到。本文只是把 9/4 要用的第二个数字提前核实、备好。

## 发现：daily report 现有的 `est_usd` 和 gate 定义的口径不一致

预注册的 gate 原文：

> ABANDON Stage 2 if the modelled reward yield on **a reward-eligible two-sided quote
> (200 shares per side ≈ 200 USDC locked)** is below **10% annualised at the upper bound**

但 `report.observe()`（daily email 用的就是这个）里，参考报价的 size 取的是**每个市场自己的 `rewards.min_size`**（20–200 不等，中位数 100），不是 gate 写死的 200 股。这不是无关小事：
`score.payout_bounds` 里 share = mine/(mine+D) 对 size 是**凹函数**（边际递减）——size 越小，单位股的份额效率越高，所以用 min_size 代入算出来的年化数字，
和 gate 实际要求的"200 股"不能直接互换着看。

## 核实做法

复用 `score.py` 的纯函数（`order_score` / `qmin` / `payout_bounds`），重新按 gate 原文把参考报价的 size 写死成 **200**（offset 沿用现有 `min(1 cent, max_spread*0.5)` 惯例），
对存量书面快照逐 token 重算，取 **upper bound**（`share_hi`，用 `denom_lo` 那个更有利的分母假设）——即 gate 原文点名要测的那个数。

脚本：droplet `/tmp/yield_gate.py`；本地副本 job tmp `/Users/tingluo/.claude/jobs/173c471f/tmp/yield_gate.py`。只读。

## 结果：gate 通过，且余量很大

| 日期 | 有效 token 数 | 年化收益中位数（upper） | 最差 token（upper） | <10% 的 token 数 |
|---|---|---|---|---|
| 08-15 | 60 | 263.3% | 28.3% | 0 |
| 08-18 | 60 | 364.9% | 33.8% | 0 |
| 08-20 | 60 | 315.7% | 28.6% | 0 |
| 08-22 | 58 | 337.9% | 27.8% | 0 |

四个抽样日期都稳定：中位数常年在 260–365%，**全窗口没有一个 token 低于 10%，最差的也有 27.8%**（gate 门槛的 ~2.8 倍）。
08-22 少了 2 个 token（不是丢数据——是当天 midpoint 出了 `[0.10, 0.90]` 双边带或 pool 被拉掉，gate 本身对这类 token 单独按
`min(Q1,Q2)` 计算、不适用这条 200-股测试，予以排除，符合预注册原意）。

与 08-16 邮件里报过的"610–1020% 毛年化"（用的是旧口径/不同 size）量级一致，不是凭空冒出的数字。

## 这个数字仍然只是"毛收益"，不是净收益

- 这套算法里，逆向选择成本恒为 0（Stage 1 从未下单，没有真实成交，也就没有真实的一侧被吃亏）。
  今天另一份 worklog（`2026-08-22_laminar-direction-a-unlabeled_v1.md`）里"大单之后价格延续"的发现，
  正是开始把这个 0 变成一个真实数字的第一块拼图，但目前还没并进这个 yield 计算里。
- `b`（in-game multiplier）假设为 1.0，未有文档确认（预注册"known unknown #1"）。
- 用的是单日最新快照，不是全窗口的时间加权平均；但四个抽样日期彼此接近，不像是靠某一天运气撑起来的。
- 200 股在这些薄门槛市场（`min_size` 常常只有 20–100）里，可能已经是书面上很大的一部分挂单——
  reward 份额公式已经把这个"我方规模影响分母"的效应算进去了，但真实下单时的执行冲击（会不会一挂上去
  就把书面价格顶开）没有建模，这正是 Stage 2 要做、Stage 1 结构性做不到的事。

## 结论（给 9/4 用，不是现在的决策）

**两个 gate 目前都健康**：
- Denominator bound：中位数 ~1.75–1.80x，最大 ~2.0x，远低于 3x 硬门槛（但要注意：balanced book 的理论下限本身就是 2x，
  这个比值离下限很近，说明书面本身相当平衡，还没真正测出"极端失衡时会多差"）。
- Yield：中位数 260–365%，最差 token 也有 27.8%，远高于 10% 门槛。

按预注册原文，**两个 gate 都清了 = PROCEED to Stage 2 的必要条件已经在望**——但正式判定仍然要等到 9/4（或提前触发），
且预注册自己写死了一句话：**"a positive Stage-1 result is explicitly NOT sufficient to trade"**。
gate 过了只解除"值不值得建模"的疑虑，不解除"建完模型之前不能下单"的约束。

## 下一步

1. 9/4（或触发条件提前满足时）用完整窗口重跑一次这两个数字，作为正式记录，而不是引用本文件的抽样结果。
2. 逆向选择成本项：待下周中价版本的方向 A 结果出来后，尝试把它折算进这里的年化收益，得到一个更接近"净"的数字。
