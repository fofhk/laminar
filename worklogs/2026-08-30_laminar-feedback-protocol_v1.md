# Laminar — 免责声明与实盘结果回流协议 v1

- 日期:2026-08-30
- 版本:v1
- 提交:`b5e3159`(main),CI 四个 job 全绿(test 3.12/3.13、reports、leak-scan)
- 仓库:github.com/fofhk/laminar

## 1. 任务

公开仓库缺两样东西:(a) 项目性质与责任边界的声明;(b) 别人拿代码去实盘之后,
数据怎么回流。第二件是设计工作,不是文书工作。

## 2. 核心设计判断:回流的不应该是"数据",而是"结论"

直觉方案是"把日志发给我"。这个方案错三次:

1. **不被允许。** 簿子和成交带是场地的数据,条款限制转交。接收一堆这种东西,
   等于把仓库变成一个我们无权做的重分发点。
2. **不是项目需要的。** OPEN_QUESTIONS 里没有一条是因为缺快照而卡住的 —— 采集
   本来就在产生快照。真正缺的是**竞争方到底得了多少分**,那是三个数,不是数据集。
3. **撑不过 review。** 二十个数的 JSON 能在 CI 里查、在 PR 里吵、在工作日志里引用;
   几个 G 的数据包三样都做不到,实际结果是没人看。

所以协议的形状是:**贡献者在本地算完,只交结论。** schema 全层
`additionalProperties: false` —— 簿子快照**没有地方可放**。这是机制,不是风格。

## 3. 关键洞察:一次奖励结算就能精确测出分母 D

Stage 2 只能给区间(实测中位宽度 ~1.75x),因为 `/book` 按价位聚合。但对一个真在
拿钱的人来说 D 不是未知量:

```
share = mine/(mine+D)  且  share = reward_paid/pool
=>  D = mine_qmin × (pool − reward_paid) / reward_paid
```

三个数全部来自**他自己的账户**:自己的挂单日志过 `score.qmin()`、自己的奖励结算、
市场公布的日费率。**全程零市场数据。** 这就是为什么协议能同时满足"合规"和"有用"——
不是折中,是同一个约束的两面。

### 3.1 偏差是单向的,两个方向的证据强度不等

若场地未足额发放奖池(没人报够价的市场),`reward_paid/pool` 低估真实 share,
算出的 D **偏高**。因此:

- **落在区间下方** —— 强证据。未足额发放解释不了这个方向,直接证伪 `D_min`。
- **落在区间上方** —— 弱证据。可能是界错,也可能只是奖池没发满。
- **落在区间内** —— 佐证,而且是这个 bracket 有史以来第一份实盘证据。

把这条写进了 `feedback.py` 的 docstring 和协议文档。**这是本轮最值得留下的推理**:
不对称性不写出来,收到一份 "above" 会被误读成证伪。

## 4. 交付物

| 文件 | 作用 |
|---|---|
| `DISCLAIMER.md` | 观察性研究、从未下过单;指向 LICENSE §7/§8 而非自造法律语言;补 Apache 不覆盖的部分 —— 数字全是毛额/区间/成交下界/从未实盘验证,场地条款要自己读,本研究的 gate 是作者的风险偏好不是推荐 |
| `docs/FEEDBACK_PROTOCOL.md` | 协议主文:为什么拒收数据、D 的测量、每个 open question 对应哪种观测、什么绝对不能发、怎么提交 |
| `schema/live_report.v1.json` | 格式定义(draft 2020-12),四种观测:`denominator_measurement` / `queue_bracket` / `yield_realised` / `other` |
| `schema/example_live_report.json` | 算术精确的样例,CI 会验它,防漂移 |
| `src/laminar/feedback.py` | `implied_denominator()` + `bracket_verdict()`,含偏差说明 |
| `ops/validate_reports.py` | CI guard(见 §5) |
| `contrib/reports/` | 提交落点,目前只有 README |
| `tests/test_laminar_feedback.py` | 8 条,总数 75 → 83 |

## 5. 校验不是"读一遍 schema"

报告**同时携带输入和结论**,所以 CI 可以**重算**而不是采信。三层:

1. schema —— 未列出的字段直接拒。
2. `validate_reports.py` 重算 `d_implied` 和 `verdict`,与提交者声称的不符就拒
   (容差 0.5%)。**提交者不能声称一个自己的数字不支持的结论。** 这不是不信任谁,
   是这个项目一贯的方法:拒绝接受不可验证的量,贡献者的声称和作者的声称一视同仁。
3. 40 位十六进制钱包地址扫描 —— schema 用 pattern 挡住了结构化字段
   (condition_id 是 64 位,不会误伤),但 `notes`/`summary` 是自由文本。

### 5.1 注入验证(必须做,否则 guard 只是装饰)

造了四个坏样本喂进去,四个全部拒收:
篡改 `d_implied` → 报"输入算出 3583.16";篡改 verdict → 报"输入算出 inside";
塞 `order_book` 字段 → schema 拒;`notes` 里塞钱包地址 → 扫描拒。

**这一步顺带发现校验器自己的 bug**:`path.relative_to(ROOT)` 对仓库外的路径抛
ValueError。如果只跑正常样例永远发现不了。已修(`_shown()` 降级为原路径)。

## 6. 与 OPEN_QUESTIONS 的接线

Q1 / Q3 / Q4 / Q5 各加一行"实盘能定什么",Q4 那条是决定性的。

**Q2(自影响)和 Q6(非均匀分配)故意不可上报。** 两层都还没写,没有预测可供反驳,
一个无从证伪的"测量"不值得一个 schema kind。写进文档里当作明确的范围边界 ——
说清楚接口**不**覆盖什么,和说清楚覆盖什么一样是设计。

## 7. 未完成

- **Topics 仍是空的。** 本机没有 `gh`、没有 token,我不碰用户的 GitHub 凭据。
  需要用户在网页 About 齿轮里填,或装 `gh` 后由我执行。
- 仓库仍是 0 个 issue。想开六个(对应 OPEN_QUESTIONS)但同样卡在凭据。
- 直接推 main,未走 PR,所以 CI 的 `pull_request` 触发路径没有被实跑过
  (配置与 push 触发相同,风险低,但没验证过就是没验证过)。

## 8. 用户偏好记录

- 明确要求:声明里要写"拿去实盘的风险不由我们承担",同时**欢迎实盘数据回流**。
  两句必须并存,不能只写免责。
- 要求把 open questions 和实盘接口**一起**定义好,不接受只写文档不给格式。
