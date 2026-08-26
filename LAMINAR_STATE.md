# LAMINAR — 状态与恢复入口(pick-up point)

> **下次开会话,先读这一份。** 最后更新：2026-08-25(Mac 时间约 16:00)
> 更新规则:每完成一个阶段就改这里的"当前状态"和"下一步",不要让它过期。

---

## 0. 一句话现状

Stage 1 只读采集第 11 天,满勤,**9/4 门槛裁决**;Stage 2 影子研究已开工,
匹配核心 + V1 已通过;**Stage 3 被辖区问题阻塞,不是被数据阻塞。**

---

## 1. 目录与真相来源(2026-08-25 独立成库)

| 位置 | 角色 |
|---|---|
| **`/Users/tingluo/Documents/CC/Laminar/`** | **真相来源 + git 仓库。所有编辑在这里做。** |
| `root@DROPLET_IP:/opt/farseer/` | 运行环境(SGP1 droplet),cron 在这里跑 |
| `root@DROPLET_IP:/opt/backups/git/laminar.git` | 代码异地镜像(bare) |
| `~/laminar-backup/` | 数据异地快照(带日期,硬链接去重) |
| `github.com/fofhk/laminar`(公开) | 公开镜像,供他人审查/改进 —— **已净化,见下** |

`CC/farseer/` 里的 laminar 文件是**迁出前的旧副本,已过期,不要读、不要改**
(见该目录下 `LAMINAR_MOVED.md`)。

**交叉备份**:代码主份在 Mac → 镜像到 droplet;数据主份在 droplet → 快照到 Mac。
任何一台机器整体丢失,两类资产都还各有一份。

```bash
git push droplet main     # 代码异地(ssh key 已写进 repo config,直接可用)
git push origin  main     # 公开镜像(GitHub)
ops/backup.sh             # 数据异地,带文件数核对 + parquet 回读校验
```

### 公开化(2026-08-26)——现在这份仓库是公开的,写东西前先想一下

历史已在 08-26 重建为单次 orphan 提交,因为旧的 5 个 commit 里带着采集机 IP、
邮件端点和邮箱。**旧的完整历史保留在 `archive/full-history-20260826`**
(本地 + droplet 各一份,**不要推到 GitHub**)。

三条规矩:

1. **主机、密钥、邮件端点不进仓库** —— 放 `ops/local.env`(已 gitignore),
   模板是 `ops/local.env.example`。`notify.py` 从环境或该文件读,读不到就抛
   `KeyError` —— 未配置的告警器必须响亮地失败,不能安静地不发信。
2. **辖区/主体相关的材料不进仓库。** 那两份 worklog 只在 `CC/worklogs/`
   和 droplet 的 archive 分支里。
3. **扫描已自动化,不要再靠人工记忆。** `ops/leak_scan.sh` 按**形状**扫 tracked 文件
   (任何 IPv4、任何邮箱),CI 与本地 pre-push 钩子共用同一份实现:
   ```bash
   ops/leak_scan.sh --install-hook    # 每台新机器装一次
   ```
   **它已经抓到过两条我人工漏掉的** —— 一条家庭出口 IP,一条是我在 worklog 里
   "举证"时把该 IP 又抄了一遍。引用泄露内容一律用占位符。

### 部署

```bash
cd /Users/tingluo/Documents/CC/Laminar
cp ops/local.env.example ops/local.env   # 首次:填 host / key / 邮件端点(untracked)
ops/deploy.sh             # 部署 + 在 droplet 上 import 验证 + 跑测试
ops/deploy.sh --check     # 只验证,不改任何东西
```

**不要再手写 rsync。** 旧的手写命令因源目录带尾斜杠,把 `src/` 内容摊平进
`/opt/farseer/`,而 venv 从 `/opt/farseer/src/` 导入 —— **静默失效了 11 天**。
`deploy.sh` 路径全部显式,且**部署完必须 import 成功 + 测试通过才算数**。

`deploy.sh` 还会比对冻结的 watchlist,**只报警不覆盖** —— 窗口内重新冻结
watchlist 是预注册变更,不是部署。

---

## 2. 文档索引(按阅读顺序)

| 文档 | 内容 |
|---|---|
| `docs/laminar_20260814_preregistration.md` | **治理文件。冻结范围、评分模型、门槛。不得随意改。** |
| `docs/laminar_shadow_study_design_v1.md` | Stage 2 影子研究执行规格(含阈值 §6、双界实测 §6.4) |
| `worklogs/2026-08-25_laminar-collection-audit-vs-gates_v1.md` | 采集口径 vs 9/4 目标逐条对照 |
| `worklogs/2026-08-25_laminar-booksfull-deploy-and-bounds_v1.md` | books_full 上线 + 双界扫描 + 部署命令 bug |
| `worklogs/2026-08-25_laminar-review-debug-and-plan_v1.md` | 复核 + 告警缺陷修复 + 详细时间线 |
| `worklogs/2026-08-25_laminar-shadow-matching-core-v1_v1.md` | 匹配核心 + V1(含 60 秒盲区发现) |
| `worklogs/2026-08-26_laminar-github-publication_v1.md` | 公开前的净化:扫描结果、参数化、历史重建 |

> 两份辖区/主体相关的 worklog **有意不进本仓库**。原件在 `CC/worklogs/` 下,
> 那才是这个项目 worklog 的规范位置;仓库里的 `worklogs/` 只是提交快照。

---

## 3. 当前状态

**采集(全自动,cron,无需人工)**
- `books` 每分钟(band 内)· `books_full` 每分钟(全深度,**08-25 03:23 UTC 起**,不可回填)
- `markets` / `trades` / `competitiveness` / `leads` / `track` 每小时错峰 · `activity` 每日 03:40
- 日报 00:15 UTC → MAIL_TO
- coverage 0.99+,08-25 每快照 1,159 行,满勤

**门槛(不变)**:Gate① 分母界比 ≤3x(中位 ~1.75x)· Gate② 年化 ≥10%(200 股上界)· Gate③ band 可达

**投产阈值(2026-08-25 定稿,不得再改)**:底线 **X = 110%**(最悲观角,100k 净年化)/ 目标 **200%**(中位角)

**ToS(硬阻塞,两道独立的门)**:Stage 3 被 Polymarket ToS 阻塞,不是被数据阻塞。
① 辖区条款;② **数据条款(2026-08-26 新发现)—— 这道门搬家解决不了**。
两者的结论与原文依据都记在**私有笔记**(不在本仓库)。

数据条款还有一个直接后果,已落实到本仓库:**样本数据不发,永远不发** ——
许可是 personal / non-sublicensable / non-transferable,我们没有转授权可授。
公开的是代码、方法与结论表;**仓库可运行,但不可复现我的数字**,README 里已明说。

---

## 4. 已完成 / 下一步

**已完成**
- [x] books_full 旁路补采(逐行对账:band 序列严格 == 全深度的 band 内子集)
- [x] 双界扫描 `src/laminar/bounds_sweep.py`(收敛猜想被证伪;100k 处中位 lo 136% / hi 225%,毛数)
- [x] 部署命令修正 + 影子目录清除(备份 `backups/shadow_pkgs_20260825.tgz`)
- [x] 日报告警缺陷修复:`alerts`/`notes` 两层 + **新增样本损失 ABANDON TRIGGER**(此前完全没有告警)
- [x] **组件 A 匹配核心** `src/laminar/shadow.py` + `tests/test_laminar_shadow.py`(16 项)
- [x] **V1 退化测试**:5,148 笔真实成交,100% 精确,最大误差 0.0;
      磁带 `side` 实测为 **taker 方向**(97.7% / 98.8%)

**下一步(按序)**
1. **V2 簿子增量对账** —— 用成交后快照的深度变化验证队列假设
2. 自身冲击层(挂单并入后重算 midpoint —— 会**降低**自己的得分,反直觉)
3. 存货三政策(P0 持有至结算 / P1 报价偏移 / P2 穿价差平仓,P2 依赖 books_full)
4. 五个 horizon 逆向选择折美元(15m/60m/4h/24h/至结算)
5. 凹性曲线 + 非均匀最优配置求解器
6. 重推 watchlist(当前全池仅 $3,171/天,100k 必须扩容;全宇宙参数可回溯)
7. 组件 B 在线影子挂单器(运营指标)

**8/28 前另需补齐**:band 可达性全窗口统计(Gate③ 后半句 + 两个非门诊断)

---

## 5. 待你裁决的开放项

- [ ] `/opt/farseer/laminar_daily_report.py`(根目录冗余份,md5 与 `ops/` 相同,cron 用 ops/)要不要删
- [ ] `CC/farseer/` 里的 laminar 旧副本何时删(现留 `LAMINAR_MOVED.md` 指路,未删)
- [ ] 是否再加一个真正第三地的远端(GitHub 需先装 `gh` 并登录 —— 需要你操作)

**已决策,不要再提**
- **备份保持手动,不上 LaunchAgent**(2026-08-25 用户决定)。理由:项目仍在分析研究阶段,
  尚未承载资本,自动化的收益不抵"静默失败带来虚假安全感"的代价。
  → **代价是备份只和最后一次手动运行一样新。** 缓解办法写在 §6:把它绑到本来就有的检查点上。
- **已完成**:git 建库 + droplet bare 镜像 + 数据快照备份(见 §1)。

---

## 6. 时间结点

| 日期 | 事件 |
|---|---|
| 8/28 | Day-14 复核;band 可达性统计交付;**跑一次 `ops/backup.sh` + `git push droplet main`** |
| **9/4** | **Stage 1 门槛裁决;组件 A 代码冻结;全窗口双界重算;跑备份(窗口收官,这次最重要)** |
| 9/5–9/8 | 组件 A 跑留出窗口(8/25–9/4)样本外确认;组件 B 上线 |
| 约 9/25 | **若 9/4 结论模糊,预注册规定延长至 42 天 —— 采集不能停** |

⚠️ **9/4 是裁决点,不是关机点。**

**备份是手动的** —— 上面每个检查点都附了一次备份,因为那是你本来就会开会话的时候。
两条命令,几秒钟(增量快照约 120K):

```bash
ops/backup.sh && git push droplet main
```

若某次会话改了代码或跑出了新结果,顺手再跑一次。

---

## 7. 两条不要重犯的教训

1. **跨时区审计先对表。** Mac 在悉尼时间,droplet 在 UTC。曾误判"缺一小时数据 + 日报没跑",
   实际是那个小时还没发生。
2. **rsync 报 OK ≠ 部署生效。** 见 §1 的验证命令。
3. **droplet 上的长任务必须 detach。** 长跑批会被 `Connection reset by peer` 打断,
   而且可能仍返回 `exit code 0` —— 极具误导性。

   **根因已于 2026-08-25 修掉**:`~/.ssh/config` 之前只给 `foftrader`(FOFTRADER_IP)
   配了 keepalive + 连接复用,而 Laminar 这台 **DROPLET_IP 完全没有条目** —— 一直裸 IP 连,
   sshd 限流照单重置。已新增 `Host laminar DROPLET_IP` 块(别名与裸 IP 同时匹配,
   所以 `deploy.sh`/`backup.sh` 也自动受益)。现在 `ssh laminar` 即可,复用后第二条命令 ~0.5s。
   备份在 `~/.ssh/config.bak-20260825`。

   即便如此,**长任务仍建议 detach** —— 复用降低了重置概率,不等于消除:
   ```bash
   ssh ... "setsid nohup <cmd> > /tmp/x.log 2>&1 < /dev/null &"
   ssh ... "until grep -q DONE /tmp/x.log; do sleep 10; done; cat /tmp/x.log"
   ```
   通则:**远程命令的退出码不算数,看它实际产出了什么。**

### worklog 的两个位置

规范位置是 `CC/worklogs/`(全局约定)。本仓库的 `worklogs/` 是**提交快照**,
写完新的 laminar worklog 后同步一次再提交:
```bash
cp ../worklogs/*laminar*.md worklogs/
```

---

## 8. 恢复方式

```
cd /Users/tingluo/Documents/CC/Laminar
```
然后让我读这份 `LAMINAR_STATE.md`。它指向其余全部内容。

开工前的例行三件事:
```bash
git log --oneline -5                                        # 上次做到哪
ops/deploy.sh --check                                       # droplet 上跑的是不是这份代码
ssh laminar 'date -u; tail -2 /opt/farseer/logs/laminar_books.log'   # 别名见 ~/.ssh/config
```

采集是自动的,不会因为没开会话而中断;**但任何分析/写代码的工作都需要你开会话** ——
我的定时工具是 session-only 的,不会自己醒。
