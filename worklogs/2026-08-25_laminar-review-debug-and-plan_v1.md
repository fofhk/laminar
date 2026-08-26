# Laminar — 复核 / 自查 / debug + 详细时间线

- 日期：2026-08-25(droplet ~04:5x UTC)
- 版本：v1
- 范围：复核本会话全部改动(books_full 补采、影子目录清理、部署命令修正、bounds_sweep 固化),
  核对是否满足预注册目标,自查代码与运行机制,列出未来时间结点。

## A. 复核:本会话改动是否安全、是否达标

### A1. 门槛序列零回归(已实测)

我的 `collect.py` 改动把"仅在 band 内构造 row"改成"总是构造 row,band 内才进 `rows`",
逻辑上是原条件的补集,风险点是会不会扰动 9/4 要用的 band 序列。实测每快照 band 行数:

| 日期 | 每快照 band 行数 | 说明 |
|---|---|---|
| 08-23(改前) | 1,134.8 | |
| 08-24(改前) | 1,120.6 | |
| 08-25(改后) | 1,158.7 | 在历史区间内,无异常跳变 |

且 03:23 快照做过逐行对账:band 序列 == 全深度序列的 band 内子集(双向 anti-join 均 0)。
**结论:门槛序列未被触碰。**

### A2. books_full 机制健康(已实测)

- hour 04 起满勤(56 快照 = 当前分钟数),hour 03 部分(03:23 上线,无法回填,可接受)。
- 单快照捕获 in-band 1,170 / out-of-band 3,901 档 → **P2(存货平仓成本)所需的 band 外深度已到手**,
  这正是补采的唯一目的。
- best-effort 写入 + 异常仅 WARNING,不会拖垮门槛序列。
- 落盘约 8MB/天(不是先前口误的 107MB/天)。

### A3. 影子目录清理无副作用(已实测)

删除 `/opt/farseer/laminar`、`/opt/farseer/farseer` 后,`cwd=~` 与 `cwd=/opt/farseer`
两种情况下 `laminar.store` 均解析到 `src/`,`append_books_full` 存在,`CONFIG_DIR` 正确。
每日报告生成器 `build()` 在删除后仍能 import 并跑通(34,623 字符,无 traceback)。

### A4. 部署命令修正已验证

用修正后的命令(去源端尾斜杠)重新部署,模块落到 `/opt/farseer/src/`,
未重建影子目录;从仓库模块重跑 bounds_sweep 得到与 /tmp 逐位相同的结果。

## B. Debug:发现一个真实机制缺陷(需你裁决)

### ⚠️ 缺陷 #1 —— 每日报告"永远 ALERT",告警疲劳

`ops/laminar_daily_report.py:481` `ok = not warns` → `main()` `status = "OK" if ok else "ALERT"`。
但 `warns` 混入了**每天必然触发的非门诊断**:

| warn 站点 | 内容 | 是否门槛相关 | 触发频率 |
|---|---|---|---|
| :73 | 零观测 | ✅ 真告警 | 罕见 |
| :182 | 超 3x 分母门比例 | ✅ Gate① | 视数据 |
| :203 | watchlist token 不可观测 | ✅ ≈ 样本损失门 | 视数据 |
| :193 | 池子漂移 >30% | ❌ 背景噪音 | **每天 8–13 个** |
| :224 | shock 检出 | ❌ 信息性 | 频繁 |
| :439 | 收益较基线移动 >50% | ❌ 未校准首版 | 频繁 |
| :334 | lead tracker <24 pass | ⚠️ 覆盖相关 | 视数据 |

因为 :193/:224/:439 几乎天天触发,`warns` **永不为空** → **每天都发 "ALERT — flags raised"**。
实测 08-24 = ALERT;08-21/22/23 同理(池子漂移是常态,审计已记为"8–11/天背景噪音")。

**后果**:预注册的真实弃项触发器是"连续 3 天 >5% 样本损失"。若 ALERT 天天响,
这条真信号会被噪音淹没 —— 正是"fail loud"要避免的反模式,直接削弱预注册自带的安全机制。

**建议修法(两层,最小改动)**：把 `warns` 拆成
- `alerts`(门槛相关:零观测 / Gate① / 样本损失 / lead 覆盖不足)→ 决定 `status=ALERT` 与邮件主题严重度;
- `notes`(信息性:池子漂移 / shock / 未校准收益移动)→ 照常进 flags 栏展示,但**不**升级为 ALERT。

### ✅ 已修复(2026-08-25 05:0x UTC)

修复过程中发现**第二个、更严重的缺陷**:预注册唯一的硬性弃项条件
"连续 3 天 >5% 样本损失" —— `coverage` 从第一天起就在写进 `metrics_history`,
但**从来没有跟任何阈值比对过,完全没有告警**。即:天天为噪音报警,却对真正该停项目的条件保持沉默。

改动仅限 `ops/laminar_daily_report.py`:

1. `warns` 拆成 `alerts` / `notes`,9 个站点重新归类:
   - **alerts**(升级为 ALERT):零观测、Gate① >5% 超 3x、watchlist token 不可观测、lead tracker 覆盖不足
   - **notes**(照常展示,不升级):每市场 BAND/DENOM/DRIFT 旗标、池子漂移 >30%、shock 检出、未校准的收益移动
2. `ok = not alerts`(原 `not warns`)
3. **新增样本损失告警**:`MIN_COVERAGE = 0.95`,`COVERAGE_STREAK_DAYS = 3`。
   当日 coverage 低于 95% → alert;从 `metrics_history` 回读连续 3 天均低于 95% → **ABANDON TRIGGER** 告警。
4. 正文顶部新增 Alerts banner(无告警时明示"No alerts",诊断项不升级)
5. 顺手修掉一处措辞 bug::215 的 "none fired today" 原本判的是全局 `warns`,
   但那句话讲的是每市场旗标列 —— 改为判 `notes`。

### 验证(全部实测)

| 场景 | 期望 | 实测 |
|---|---|---|
| 08-24(正常日,12 个池子漂移 + shock) | OK | **OK** ✅(修前是 ALERT) |
| 08-14(真实 coverage 74.4%) | ALERT | **ALERT**,"sample coverage 74.4% on 2026-08-14, below 95%" ✅ |
| 合成连续 3 天低覆盖(90/80/70%) | ABANDON TRIGGER | **ALERT**,"ABANDON TRIGGER: 3 consecutive days below 95% coverage (…)" ✅ |
| `metrics_history` 是否被测试污染 | 不变 | **12 行,08-13→08-24,coverage 尾部 0.9924/0.9986/0.9993/0.9993** ✅ |
| 采集器 | 不中断 | 05:01/05:02 连续 ok ✅ |

### 全窗口验证(2026-08-25 补完)

最初打算用 `build()` 抽查 4 天来证实"旧代码天天 ALERT",**那次跑批因 SSH 连接重置而没有返回任何结果** ——
当时报告里"08-21/22/23 同理"是推断,不是实测。改用 `metrics_history` 直接判定,证据更硬且覆盖全窗口:
旧代码只要 `pool_drift_count > 0` 就 append warn,而 `ok = not warns`。

| | 结果 |
|---|---|
| 旧代码(按 metrics_history 判定) | **12 天 / 12 天全部 ALERT**;池子漂移每天 7–12 个,从无一日为零 |
| 新代码实跑 08-19…08-24 | **6 天 / 6 天全部 OK** |
| 新代码实跑 08-14(coverage 74.4%) | **ALERT**,文案正确 |
| 新代码合成连续 3 天低覆盖 | **ABANDON TRIGGER 触发** |

结论:告警从"每天都响、真信号被淹没"变成"健康日安静、真条件才响"。

### ⚠️ 运维教训:droplet 上的长任务必须 detach

本次两次长跑批都被 `Connection reset by peer` 打断(第二次连 `exit code 0` 都给了,极具误导性)。
`setsid nohup ... > /tmp/x.log 2>&1 < /dev/null &` 之后再单独短连接读日志,才跑完。
**远程命令报成功 ≠ 它真的做完了事** —— 看产出,不看退出码。这和 `deploy.sh` 坚持"import 验证"是同一条道理。

**自查中发现并修掉的自造 bug**:告警文案原写 `f"... {coverage:.1%} < {MIN_COVERAGE:.0%} ..."`,
裸 `<` 会被 HTML 当成标签开头吞掉后半句(实测渲染成 `sample coverage 74.4% ` 就断了)。
改为 "below 95%" 措辞,不引入转义。已重新部署并验证 raw html 完整。

### 其余自查:未发现其他缺陷

- crontab:books 每分钟、markets/trades/competitiveness/leads/track 每小时错峰、daily report 00:15、
  activity 03:40 —— 与预注册 cadence 一致。
- `laminar_daily_report.py` 在 `/opt/farseer/` 与 `/opt/farseer/ops/` 各一份,**md5 相同**,
  cron 用 ops 版;非缺陷但属冗余,建议顺手删根目录那份(低优先)。
- bounds_sweep 沿用 gate_check 的毛数口径(用观测 midpoint,不含自我冲击)—— 这是**有意**的,
  它是毛数包络工具,不是组件 A;自我冲击在组件 A 处理。已在模块 docstring 标注。

## C. 数据收集是否满足预注册目标:是

沿用 `2026-08-25_laminar-collection-audit-vs-gates_v1.md` 的逐条对照,叠加本次两项进展:
- Gate③(band 可达)所需数据齐备,分析待写(8/28 前)。
- 新增 books_full 使 Stage 2 的 P2 存货平仓成本可算 —— 这是审计时还没有的能力。
唯一只读不可测项(epoch 1,440 vs 10,080)已正式降级为"已界定、对门槛无关、Stage 3 一笔小单实测"。

**结论:on the right track,只需继续积累时间;无需新增或修改任何采集口径。**
