# Laminar — 采集计划 vs 9/4 评审目标 全面对照审计

- 日期：2026-08-25（Mac 本地；droplet 仍在 08-24 UTC）
- 版本：v1
- 目的：确认到 9/4 评审时不会发现"少采了一个口径还要再等 21 天"。
- 结论先行：**不需要改任何采集**。缺的是两个"分析待写"，数据都已在盘上，均已验证可回算。

## 逐条对照（预注册 laminar_20260814_preregistration.md）

| 9/4 要回答的问题 | 数据状态 | 备注 |
|---|---|---|
| Gate①分母界比 ≤3x（中位市场） | ✅ 每日入 metrics_history，中位 ~1.75x，max 2.00x | 时间序列完整 |
| Gate②年化 ≥10%（200股、upper bound） | ✅ section 11 每日算；全窗口序列可从 books 回算 | 冒烟 3 天 288–378% |
| Gate③PROCEED 后半句：band 可达不穿价差 | ⚠️ 无 section 在算，但**已验证可回算**：collector 只存 band 内档位，空组=band 无人。08-23 实测 99.84% side-samples 有 ≥1 档在 band 内，中位 4 档 | 分析待写，非采集缺口 |
| 已知未知②：epoch 1,440 vs 10,080 | ⚠️ 只读**无法**实测（无账户无 payout 可观察）。预注册"measures which is true"是过度承诺 | 对 gate 无影响：日池按日付，epoch 长度只改跨天加权；正式降级为"已界定无关，Stage 3 用一笔小单实测" |
| 非门诊断：逆向选择（band 内档被吃后 60min 中价漂移） | ✅ trades+books 全存，可回算；gate_risk 15min 版每日在跑 | 60min 版 9/4 前补算一次 |
| 非门诊断：spread vs max_spread 分布 | ✅ 同 Gate③，占用率即答案 | 同上待写 |
| 非门诊断：band 内档位数（maker 拥挤度） | ✅ 直接存了，中位 4 档/side | |
| 非门诊断：池子日漂移 | ✅ 每日 pool_drift，8–11 个/天为背景噪音 | |
| Uptime 自测 | ✅ coverage 0.99–1.0 每日入库 | books 每日 1438–1441 分钟 |
| Shock ledger 按类别聚集 | ✅ 累积中，8/28 Day-14 复核阈值 | |
| Lead series | ✅ 08-25 UTC "first complete windows" 复查到期（明天 droplet 时间） | 单独 cadence |

## 审计中的一次虚惊（记录以免再犯）

Mac 在悉尼时间 08-25 检查时，droplet 还在 08-24 22:56 UTC。一度误判"08-24 缺
23 点整小时 + 今晨日报没跑"——实际是那个小时还没发生。**跨时区审计先对表。**
实际采集满勤：08-24 当时 1375/1376 分钟。

## 深度存储口径确认

collect.py:156 起：**只存 reward band 内档位**（设计如此，band 外得分为零）。
每 side 平均 ~9.7 行是 in-band 档位数，不是截断。分母重构不受影响。

## 9/4 前的分析待办（数据已齐，只是要写）

1. Band 可达性/拥挤度全窗口统计（Gate③ 后半句 + 两个非门诊断）——建议 8/28 前写好。
2. 200 股年化收益全窗口逐日序列（正式 gate 用全窗口，不只抽查日）。
3. 60min 版逆向选择诊断跑一次全窗口。
4. epoch 问题在评审文档里正式记录为"bounded, 对 gate 无关，Stage 3 实测"。

## Stage 3 路径的诚实评估（用户当前计划：独立钱包 + 许可国 IP 服务器）

预注册自己已写明："The ToS binds on the user's location, not the server's; a Dutch
server does not relocate an operator." 该计划解决 reachability 层，不解决 operator
jurisdiction 层。结构性矛盾：策略 100% 收入是 Polymarket 酌情支付的奖励——被识别的
现实后果是奖励不付/追回 + 封禁（链上仓位可退出但收入线归零），MM 账户（持续报价、
按 epoch 领奖）是审视密度最高的账户类型。AU 侧另有 ACMA 屏蔽 + IGA 本地风险。
路径选项按 H1 排序：(a) 许可辖区真实实体运营（唯一真正 settle 的选项）；
(b) 等监管面变化 + 先做 Stage 2（零资本只读，不被辖区阻塞）；(c) 止于研究结果。
9/4 前无需购置任何东西；关键路径是实体决策的 lead time，不是数据。
