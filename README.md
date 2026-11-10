# ftrade

个人低频交易分析平台，数据源为富途 OpenAPI。

**Phase 1 只读**：只查询持仓、成交、账户资金并做分析，代码里**不存在**任何下单/改单/撤单接口，
`tests/test_readonly.py` 会静态扫描源码来保证这一点。

## 它解决什么问题

富途 App 能看到当前浮盈，但看不到：

- 每一笔买卖按 FIFO 配对后的**完整交易**（胜率、盈亏比、真实持有期）
- **处置效应**：是不是赚了就跑、亏了死扛
- 集中度（HHI / 有效持仓数）、市场与币种暴露
- 跨越券商历史窗口（历史接口单次上限 90 天）的**长期净值曲线**

所以这个项目做的事很简单：把富途的数据增量同步进本地 SQLite，然后在本地做分析。

## 快速开始

```bash
git clone git@github.com:clevertang/futu_trading.git
cd futu_trading
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ftrade init                    # 生成 config.yaml
```

### 先用 Mock 数据跑一遍（不需要 OpenD）

```bash
ftrade sync --source mock
ftrade positions
ftrade report
ftrade serve                   # http://127.0.0.1:8765
```

### 接真实账户

1. 下载并启动 [富途 OpenD](https://openapi.futunn.com/futu-api-doc/qa/opend.html) 网关，登录账号（默认 `127.0.0.1:11111`）
2. 按需修改 `config.yaml` 里的 `security_firm` / `markets` / `fx_rates`
3. 首次全量拉取历史：

```bash
ftrade sync --full
```

之后每天跑一次 `ftrade sync` 即可（会从断点续拉，并回溯 3 天容错）。
加 `--cash-flow` 会同时同步分红、利息、出入金（按清算日逐日拉，首次回补要几个小时，可中断续传）。

`ftrade sync` 有任何账户或步骤失败时退出码为 **3**（成功的部分照常入库）；
同一个数据库同时只允许一个 sync 在跑。Mock 同步会拒绝写入已有真实账户的数据库——
离线试用请用 `-c` 指向另一份配置和数据库。

## 命令

| 命令 | 说明 |
| --- | --- |
| `ftrade init` | 生成配置、初始化数据库 |
| `ftrade sync [--full] [--no-klines] [--cash-flow] [--source mock]` | 增量同步账户/持仓/成交/订单/费用/日线（及资金流水） |
| `ftrade positions` | 当前持仓表 |
| `ftrade deals --start 2025-01-01 --code HK.00700` | 成交流水 |
| `ftrade trips` | FIFO 配对后的已平仓交易 |
| `ftrade report --json reports/r.json` | 组合报告 + 风险观察 |
| `ftrade serve [--host] [--port]` | 本地 Web 面板（默认只监听 127.0.0.1；非本机地址需显式加 `--allow-remote`，面板没有登录） |

`--acc-id N` 可以放在子命令前或后，只看某个账户。

### 每天自动跑一次

`scripts/daily_report.sh` 是定时任务用的包装脚本：同步 + 导出当天报告，
日志写到 `reports/daily.log`。它不会启动 OpenD——OpenD 没在跑就跳过并记一行说明。

用 launchd 定在工作日早上 8:30：

```bash
scripts/install_launchd.sh            # 安装/重装
scripts/install_launchd.sh run        # 立即按 launchd 的方式跑一次
scripts/install_launchd.sh status     # 看状态和上次退出码
scripts/install_launchd.sh uninstall  # 卸载
```

不用 cron 的原因：机器在 8:30 睡着时 cron 直接跳过、永不补跑，而每漏一次就
永久少一天的持仓和净值快照（成交下次同步能补回，快照补不回）。launchd 会在
机器醒来后补跑一次；关机状态下仍然跑不了。

脚本自带互斥锁：一轮没跑完时下一轮直接跳过，而不是两个进程同时写同一个
SQLite 文件（这个库曾经就是这么被写坏过一次的）。`ftrade sync` 本身也加了文件锁，
手动跑的 sync 和定时任务不会撞在一起。同步部分失败时脚本照样出报告，但退出码为 3。

## 结构

```
src/ftrade/
├── gateway/     数据源：futu_gw.py（只读）、mock.py（离线）
├── storage/     SQLite schema + 仓储查询
├── sync/        增量同步（成交按 90 天切片，断点存在 sync_state）
├── analysis/    FIFO 归因、组合结构、风险指标、行为统计
├── advice/      规则观察（Phase 1）+ 建议引擎接口（Phase 2）
└── web/         FastAPI + 单页面板（只读连接数据库）
scripts/         每日任务（launchd）、分享模式截图、雪球事实清单与阅读统计
```

数据表：`accounts` / `account_snapshots` / `position_snapshots` / `deals` / `orders` /
`order_fees` / `cash_flows` / `klines` / `year_end_prices` / `quote_status` / `sync_state`。
持仓与资金是**按日快照**，同一天重复同步会整体替换当天的持仓，所以可以放心多跑。

## 几个已知口径问题

- **收益率不是 TWR**。净值曲线用的 `account_snapshots.total_assets` 含出入金，出入金频繁时会失真。
  「今年 / 本月收益」已经用资金流水扣掉了出入金；完整的 TWR 序列排在 Phase 2。
- **汇率是静态的**，写在 `config.yaml` 里。低频场景够用，需要精确时再换实时源。
- **拆股/改名/退市**不在成交流水里，需要在 `config.yaml` 的 `analysis.corporate_actions` 里声明。
  没声明的会导致本地 FIFO 推算的持仓与券商返回的对不上，报告里的 `reconciliation` 字段会把差异列出来。
  分红和预扣税来自资金流水（需 `--cash-flow`）。
- 富途历史接口有频率限制，`--full` 首次拉多年数据会比较慢。

## 参考项目

- [FutunnOpen/py-futu-api](https://github.com/FutunnOpen/py-futu-api) — 官方 Python SDK
- [billpwchan/futu_algo](https://github.com/billpwchan/futu_algo) — 基于富途 OpenAPI 的量化交易程序
- [vnpy/vnpy](https://github.com/vnpy/vnpy) + [veighna-global/vnpy_futu](https://github.com/veighna-global/vnpy_futu) — 网关抽象的分层思路

## 免责声明

本项目输出的是基于历史数据的统计事实，不构成投资建议。
