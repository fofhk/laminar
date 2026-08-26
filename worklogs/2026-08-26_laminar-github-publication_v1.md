# Laminar — 公开到 GitHub 前的净化

- 日期：2026-08-26
- 版本：v1
- 触发：用户希望在 GitHub 建库,便于他人共同审查和改进
- 决策：**公开 + 净化**(用户在三个选项中选定)

## 一、扫描结果:没有凭证,但有三类暴露

53 个跟踪文件全量扫过。**无 API key、无私钥、无助记词、无钱包私钥。**
`config/laminar_watchlist.json` 里的 31 个 `0x...` 是 Polymarket 的 `condition_id`,
链上公开数据,**不是钱包地址** —— 这一条差点被 grep 的 `0x[a-f0-9]{40}` 模式误报。

| 级别 | 内容 | 处置 |
|---|---|---|
| A(法律/个人) | ToS 辖区 worklog:点名居住地、点名 BVI 实体、并论证第三国 VPS 是否落入 circumvention 条款 | **移出仓库** |
| B(运维) | 采集机 IP + `root@` + 加固清单;邮件服务器 IP + 两个邮箱 | **参数化到 `ops/local.env`(untracked)** |
| C(alpha) | 策略本身 | 用户自决;已提示"换到审查,付出分母" |

A 级的要害不是内容质量(那份分析是对的),而是**它是自己写的、带时间戳、可公开检索的证物**。
它当初的价值——劝阻 5k 试单方案——已经兑现,不需要挂到公网上继续产生风险。

**移出不等于丢失**:`LAMINAR_STATE.md §7` 早已规定 `CC/worklogs/` 才是 worklog 的规范位置,
仓库里的 `worklogs/` 只是提交快照。原件一个字没动,且 droplet 的 archive 分支里也还有一份。

## 二、参数化的一个设计决定:加载点只能有一个

最初把 `local.env` 的加载写进 `ops/run_laminar.sh`。**这是错的** —— `notify.py` 有两个调用者,
另一个是 cron 直接跑的 `laminar_daily_report.py`(`:537` 用 subprocess 调),**它继承的是空环境**。
只改 wrapper 会让日报的告警静默失效 —— 正是这个项目一直在防的失败模式。

改为在 `notify.py` 内部加载:环境优先,回退到脚本旁的 `ops/local.env`。
**两个都读不到就抛 `KeyError`** —— 未配置的告警器必须响亮地失败,不能安静地不发信。

验证方式是 `env -i`(等同 cron 的空环境)在 droplet 上直接 import + SMTP `noop()`:

```
loaded: <mailhost> <from> <to>
smtp: (250, b'2.0.0 Ok')
```

## 三、历史重建

IP/邮箱在**全部 5 个 commit** 里,公开必须改历史。没有用 `filter-branch`——
改用 orphan 分支做单次根提交,旧历史原样保留在 `archive/full-history-20260826`
(本地 + droplet bare 各一份,**不推 GitHub**)。

净化后仍在的东西,是有意保留的:预注册里"AU 和 SG 是 close-only 辖区"、
DO 各区的 geoblock 分档 —— 这些是 Polymarket 自己文档里的公开市场结构事实,
**不含自我指认**,而且对读者有用。**保留的是事实,拿掉的是"我是谁、我住哪、我要不要搬"。**
`搬家阈值` 一并改写为 `投产阈值`:方法学(阈值须在看到净额结果前钉死)一字未损,
个人推断没了。

## 四、其他

- **LICENSE**:此前没有 → 默认 all rights reserved,别人法律上不能改。补 Apache-2.0(带专利授权)。
- **README**:重写为面向陌生读者 —— 讲清三个可独立复用的部分(分母包络、拒绝猜测的撮合器、
  一分钟采样的代价),并在 Contributing 里点名我自己最没把握的两处。
- **`gh` 没装、`brew` 也没有,但不需要**:`~/.ssh/config` 已有 `Host github.com` + `fofhk_ed25519`,
  网页建空库 + `git remote add` 即可,零新工具。

## 五、验证

| 项 | 结果 |
|---|---|
| 75 项测试 | 全过 |
| `ops/deploy.sh --check` | 通过(含远端 75 项) |
| 空环境告警链路 | 通过(SMTP 250) |
| 最终泄露扫描(13 个模式,对 staged 树) | 全 0 |
| `ops/local.env` 是否被跟踪 | 否(gitignore 已确认) |
| 两份私有 worklog 是否仍有两份副本 | 是(`CC/worklogs/` + droplet archive 分支) |

## 六、遗留

- GitHub 空库需用户在网页上建(我没有、也不应有其账号凭证),之后 `git remote add origin` + 推送。
- **今后新增 worklog 要先想一次:这份能不能公开?** 规矩写进了 `LAMINAR_STATE.md §1`。

---

# 补记(同日,发布后)

## 七、CI 的第一件事就是抓到我漏的东西

加 CI 时顺手写了一个 **leak-scan** job:不匹配具体值(把值写进去等于又泄露一次),
而是匹配**形状** —— 任何 IPv4 字面量、任何邮箱地址,白名单只放 RFC 5737 的
`203.0.113.0/24` 和 RFC 2606 的 `.invalid`。

本地首跑就报了一条 **我上一轮完全漏掉的**:

```
worklogs/2026-08-14_...md:35: Mac egress: `<home IP>`, `<ISP>`, **<city>**
```

**这比 droplet IP 严重得多** —— 家庭出口 IP + ISP + 城市,正是我声称已经清干净的那类
自我指认信息。上一轮我按"关键词清单"扫(IP 常量、邮箱、实体名),而这一条是
**用另一个词写的同一件事**;按形状扫才抓得到。

教训:**净化不能靠"我记得要找什么",要靠机器按形状扫,并且把扫描固化进 CI。**
现在任何一次提交只要带回地址形状的字面量,CI 直接红。

## 八、GitHub 的 force-push 不等于抹除

已 force-push 覆盖。但 **GitHub 会保留被覆盖的 commit 对象,凭 SHA 仍可访问**
(除非联系 support 清理)。当前仓库刚建、零 star / 零 fork,
**最干净的做法是删库重建**,成本几乎为零;拖久了就贵了。已提请用户裁决。

## 九、ToS 数据条款(详见 `2026-08-26_laminar-tos-data-clauses_v1.md`)

原计划发一天样本数据供他人复现 —— **查完 ToS 后取消**。§96 的许可是
personal / non-sublicensable / non-transferable,我们没有可转授的权利;§83 另行禁止
再分发。改为:**代码 + 方法 + 结论表公开,数据不公开**,并在 README 与 CONTRIBUTING
里明写"本仓库可运行,但不可复现我的数字"。

**顺带查出 §82,那是个比样本数据重要得多的发现,写在私有笔记里。**

## 十、本轮交付

| 项 | 状态 |
|---|---|
| CI(pytest × 3.12/3.13 + leak-scan) | ✅ 三个 job 全绿 |
| `CONTRIBUTING.md`(含五条 house rules) | ✅ |
| `docs/OPEN_QUESTIONS.md`(6 个开放问题,按影响排序) | ✅ |
| README(徽章 / 数据现实 / 指向上面两份) | ✅ |
| 干净 clone + `pip install -e '.[dev]'` + 75 测试 | ✅ 实测(Python 3.14 也过) |
| 仓库 description / topics | ⏳ 需用户在网页设置(无 gh、不应持有其凭证) |

## 十一、同一个错误,我犯了第二次

写完第七节后,**我在描述这条泄露时把 IP 原样抄进了 worklog**,又把该 worklog 同步进仓库
并推了上去 —— 于是"记录我如何移除家庭 IP"的那份文档,自己带着家庭 IP。

CI 抓住了:`5415ebd` 的 `leak-scan` **failure**,两个 test job 通过。
**机制是对的,漏的是流程 —— 我推完没看 CI 结果。**

两条改正:
1. 引用泄露内容时一律写占位符(`<home IP>` / `<ISP>` / `<city>`),**永远不要为了"举证"把值抄一遍**。
2. **推送后必须读 CI 结论**,不能推完就当完事。这与本项目一直在讲的
   "远程命令的退出码不算数,看它实际产出了什么"是同一条,只是这次的"远程"是 CI。

## 十二、删库重建(收尾)

force-push 只是改了分支指向,**被覆盖的 commit 在 GitHub 上凭 SHA 仍可访问**。
仓库当时零 star / 零 fork,所以删库重建是成本最低的彻底清除。已执行并**验证**:

| 检查 | 结果 |
|---|---|
| 对照:当前 SHA `44eb7e9` | HTTP **200**,返回 sha 字段 |
| 旧 SHA `4465953` / `b5347c2` / `5415ebd` / `07684a7` | **"No commit found for SHA"**;网页 `/commit/<sha>` **404** |
| GitHub 上的 refs | 只有 `main`,**archive 分支未泄露** |
| CI(`44eb7e9`) | 三个 job 全绿 |

**注意判定方法**:API 对不存在的 commit 返回 **422**(不是 404),我第一次把它误读成
"仍可访问"。**用一个确定存在的 SHA 做对照**才分辨得出来 —— 又一次"看产出,不看状态码"。
