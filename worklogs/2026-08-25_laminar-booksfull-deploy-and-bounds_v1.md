# Laminar — books_full 补采上线 + 双界扫描 + 阈值定稿

- 日期：2026-08-25(droplet 03:23 UTC)
- 版本：v1
- 规格：`farseer/laminar_shadow_study_design_v1.md`

## 1. 双界扫描(设计文档 §6.2 → §6.4)

`/tmp/bounds_sweep.py`(droplet),2026-08-23,16,679 个 token-快照,stride 5min。
结论:**"分母界在目标规模上自动收窄"的猜想被证伪。** hi/lo 在 3,000 股处 = 1.65,
与 200 股处的 1.59 基本相同;要到 10,000 股才降到 1.34。

目标规模(3,000 股/边 ≈ $90k)：**中位 lo 136% / hi 225%**(毛数,未扣任何成本)。
均值 196%/277% —— **均值远高于中位说明收益在市场间高度分散**,这是非均匀配置能赚到的空间。

**该脚本尚在 /tmp,需固化进仓库。**

## 2. 阈值定稿(用户裁定)

两段式,均为 100k 净年化：**底线 X = 110%(最悲观角)/ 目标 200%(中位角)**。
纪律:X 在毛数双界之后、净额结果之前定;**净额出来后不得再动**。

## 3. books_full 补采上线

改动两个文件,均为增量,**未触碰任何既有写入路径**：

- `store.py`：新增 `append_books_full()` / `load_books_full()` → `data/laminar/books_full/<day>/<hh>.parquet`,复用 `BOOK_SCHEMA`。
- `collect.py`：`sample_books()` 在同一次 `clob.book()` 调用内同时装填 `rows` 与 `rows_full`(**零额外网络开销**);
  先 `append_books` 落盘,再 best-effort 写全深度,异常只打 WARNING。

### 验证(2026-08-25 03:23 快照)

| 检查 | 结果 |
|---|---|
| band 行数 / 全深度行数 | 1,160 / 5,172 → **4.46x**(估计是 3x,实际更高) |
| band 行缺失于全深度 | **0** |
| 以精确比较重算全深度的 band 内子集 | **1,160,与 band 序列逐行一致(双向 anti-join 均为 0)** |

→ band 序列严格等于全深度序列的 band 内子集。磁盘约 **107MB/天**;`/opt` 剩 18G,三周约 2.2G。
cron 每分钟自动写入,无需人工干预。

## 4. ⚠️ 部署命令是错的(运维发现,需修)

`docs/droplet-provisioning.md` 里记录的部署命令：

```
rsync -az -e "ssh -i ~/.ssh/farseer_ed25519" --exclude /.venv --exclude /data \
      src/ ops/ config/ pyproject.toml root@DROPLET_IP:/opt/farseer/
```

`src/` 带尾斜杠 → **把 src 的内容摊平进 /opt/farseer/**,于是产生
`/opt/farseer/laminar/` 与 `/opt/farseer/farseer/` 两个**影子包**,
而 venv 的 editable install 实际指向 `/opt/farseer/src/laminar/`。
→ **按文档部署不会更新生产代码。** 今天第一次部署就踩中(`append_books_full` 部署后仍为 False)。

影子目录**自 2026-08-14 起就存在**(`/opt/farseer` 的 mtime 仍是 08-14,证明目录非今日创建),
一直未暴露,因为 cron 的 `run_laminar.sh` 不 cd,导入走 editable install。
但任何 `cd /opt/farseer` 后的 python 调用会让影子包**优先命中**(空目录 `''` 在 sys.path 首位)。

**本次处置(保守)**：正确部署到 `/opt/farseer/src/`,**并把影子目录同步成完全相同的内容** ——
两份一致则无论命中哪一份行为都一样,零风险。**未删除影子目录**(它早于今日,删除应是单独决定)。

**处置结果(2026-08-25 04:3x UTC,全部完成)**：

- [x] **部署命令已修** —— `docs/droplet-provisioning.md` 改为去掉源端尾斜杠：
      `rsync -az -e "ssh -i ~/.ssh/farseer_ed25519" src ops config pyproject.toml root@DROPLET_IP:/opt/farseer/`
      并附上失败原因说明 + 一条"验证部署是否落地"的命令(不要假设 rsync 报 OK 就等于生效)。
- [x] **影子目录已删** —— 删前先 `diff -rq` 确认与 `src/` **逐字节相同**且无任何引用,
      打包备份至 `backups/shadow_pkgs_20260825.tgz`(573KB)后 `rm -rf`。
      删后验证:`cwd=~` 与 `cwd=/opt/farseer` 两种情况下 `laminar.store` 均解析到
      `/opt/farseer/src/laminar/store.py`,`farseer.config.CONFIG_DIR` = `/opt/farseer/config`。
      采集器持续满勤(04:00/04:01/04:02 连续 ok)。
- [x] **`bounds_sweep.py` 已固化** → `src/laminar/bounds_sweep.py`,
      按 `gate_check.py` 的既有惯例放在同一包内,可 `python -m laminar.bounds_sweep [YYYY-MM-DD]` 运行;
      改用 `store.load_books()` 而非 glob。用修正后的部署命令推上去,
      **从仓库模块重跑 08-23 得到与 /tmp 版本逐位相同的结果**(16,679 个 token-快照,同一张表)。

## 4.1 体积估计的修正

先前写的"107MB/天"是**按原始行数外推的,高估约 13 倍**。
实测 `books_full` 一个整点约 325KB → **约 8MB/天**(band 序列 2.2MB/天,比值 ~3.5x)。
行数比确实是 4.46x,但 parquet 压缩吃掉了大部分增量。三周约 165MB。

## 5. 未改动的东西(确认)

冻结范围、评分模型、watchlist 过滤器、9/4 门槛口径、既有 `books` 写入路径 —— **全部未动**。
采集满勤未中断(03:23 的手工触发被 flock 正确拦截,cron 自身完成了写入)。
