PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS accounts (
    acc_id          INTEGER PRIMARY KEY,
    trd_env         TEXT,
    acc_type        TEXT,
    security_firm   TEXT,
    card_num        TEXT,
    trdmarket_auth  TEXT,
    acc_status      TEXT,
    updated_at      TEXT
);

-- 每日账户资金快照（同日重复同步会覆盖）
CREATE TABLE IF NOT EXISTS account_snapshots (
    snap_date          TEXT NOT NULL,
    acc_id             INTEGER NOT NULL,
    currency           TEXT NOT NULL,
    total_assets       REAL,
    securities_assets  REAL,
    cash               REAL,
    frozen_cash        REAL,
    market_val         REAL,
    power              REAL,
    risk_status        TEXT,
    raw                TEXT,
    synced_at          TEXT,
    PRIMARY KEY (snap_date, acc_id, currency)
);

-- 每日持仓快照
CREATE TABLE IF NOT EXISTS position_snapshots (
    snap_date        TEXT NOT NULL,
    acc_id           INTEGER NOT NULL,
    code             TEXT NOT NULL,
    stock_name       TEXT,
    position_side    TEXT,
    position_market  TEXT,
    currency         TEXT,
    qty              REAL,
    can_sell_qty     REAL,
    nominal_price    REAL,
    cost_price       REAL,
    diluted_cost     REAL,
    market_val       REAL,
    pl_val           REAL,
    pl_ratio         REAL,
    today_pl_val     REAL,
    unrealized_pl    REAL,
    realized_pl      REAL,
    synced_at        TEXT,
    PRIMARY KEY (snap_date, acc_id, code)
);

-- 历史成交（唯一事实来源，用于 FIFO 归因）
CREATE TABLE IF NOT EXISTS deals (
    deal_id      TEXT PRIMARY KEY,
    acc_id       INTEGER,
    order_id     TEXT,
    code         TEXT,
    stock_name   TEXT,
    trd_side     TEXT,
    deal_market  TEXT,
    qty          REAL,
    price        REAL,
    create_time  TEXT,
    status       TEXT,
    synced_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_deals_code_time ON deals(code, create_time);
CREATE INDEX IF NOT EXISTS idx_deals_acc_time  ON deals(acc_id, create_time);

CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    acc_id          INTEGER,
    code            TEXT,
    stock_name      TEXT,
    trd_side        TEXT,
    order_type      TEXT,
    order_status    TEXT,
    qty             REAL,
    price           REAL,
    dealt_qty       REAL,
    dealt_avg_price REAL,
    currency        TEXT,
    create_time     TEXT,
    updated_time    TEXT,
    synced_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_acc_time ON orders(acc_id, create_time);

CREATE TABLE IF NOT EXISTS klines (
    code      TEXT NOT NULL,
    time_key  TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    PRIMARY KEY (code, time_key)
);

CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

-- Trading fees, keyed by order. Futu's deal feed carries no cost data at all,
-- so realised P&L is gross until these are joined back in.
CREATE TABLE IF NOT EXISTS order_fees (
    order_id   TEXT PRIMARY KEY,
    acc_id     INTEGER,
    fee_amount REAL,
    currency   TEXT,
    details    TEXT,
    synced_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_order_fees_acc ON order_fees(acc_id);

-- Cash movements that are not trades: dividends, withholding tax, interest,
-- deposits and withdrawals. None of these appear in the deal feed, so realised
-- P&L computed from deals alone silently omits every one of them.
CREATE TABLE IF NOT EXISTS cash_flows (
    cashflow_id     TEXT PRIMARY KEY,
    acc_id          INTEGER,
    clearing_date   TEXT,
    settlement_date TEXT,
    currency        TEXT,
    cashflow_type   TEXT,
    direction       TEXT,
    amount          REAL,
    remark          TEXT,
    synced_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_cash_flows_acc_date ON cash_flows(acc_id, clearing_date);
