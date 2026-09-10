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

## 命令

| 命令 | 说明 |
| --- | --- |
| `ftrade init` | 生成配置、初始化数据库 |
| `ftrade sync [--full] [--no-klines] [--source mock]` | 增量同步账户/持仓/成交/订单/日线 |
| `ftrade positions` | 当前持仓表 |
| `ftrade deals --start 2025-01-01 --code HK.00700` | 成交流水 |
| `ftrade trips` | FIFO 配对后的已平仓交易 |
| `ftrade report --json reports/r.json` | 组合报告 + 风险观察 |
| `ftrade serve` | 本地 Web 面板（只监听 127.0.0.1） |

## 结构

```
src/ftrade/
├── gateway/     数据源：futu_gw.py（只读）、mock.py（离线）
├── storage/     SQLite schema + 仓储查询
├── sync/        增量同步（成交按 90 天切片，断点存在 sync_state）
├── analysis/    FIFO 归因、组合结构、风险指标、行为统计
├── advice/      规则观察（Phase 1）+ 建议引擎接口（Phase 2）
└── web/         FastAPI + 单页面板
```

数据表：`accounts` / `account_snapshots` / `position_snapshots` / `deals` / `orders` / `klines` / `sync_state`。
持仓与资金是**按日快照**，同一天重复同步会覆盖，所以可以放心多跑。

## 几个已知口径问题

- **收益率不是 TWR**。`account_snapshots.total_assets` 含出入金，出入金频繁时收益率会失真。真正的 TWR 需要现金流记录，排在 Phase 2。
- **汇率是静态的**，写在 `config.yaml` 里。低频场景够用，需要精确时再换实时源。
- **拆股/红股/派息**不在成交流水里，会导致本地 FIFO 推算的持仓与券商返回的对不上。报告里的 `reconciliation` 字段会把差异列出来。
- 富途历史接口有频率限制，`--full` 首次拉多年数据会比较慢。

## 参考项目

- [FutunnOpen/py-futu-api](https://github.com/FutunnOpen/py-futu-api) — 官方 Python SDK
- [billpwchan/futu_algo](https://github.com/billpwchan/futu_algo) — 基于富途 OpenAPI 的量化交易程序
- [vnpy/vnpy](https://github.com/vnpy/vnpy) + [veighna-global/vnpy_futu](https://github.com/veighna-global/vnpy_futu) — 网关抽象的分层思路

## 免责声明

本项目输出的是基于历史数据的统计事实，不构成投资建议。
