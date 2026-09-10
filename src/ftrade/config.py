"""配置加载。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAMES = ("config.yaml", "config.yml", "config.example.yaml")


def project_root() -> Path:
    """src/ftrade/config.py -> 仓库根目录。"""
    return Path(__file__).resolve().parents[2]


@dataclass
class FutuConfig:
    host: str = "127.0.0.1"
    port: int = 11111
    security_firm: str = "FUTUSECURITIES"
    markets: list[str] = field(default_factory=lambda: ["HK", "US"])
    trd_env: str = "REAL"


@dataclass
class StorageConfig:
    db_path: str = "data/ftrade.db"


@dataclass
class SyncConfig:
    deals_start: str = "2020-01-01"
    window_days: int = 90
    kline_lookback_days: int = 400


@dataclass
class AnalysisConfig:
    base_currency: str = "HKD"
    fx_rates: dict[str, float] = field(default_factory=dict)
    concentration_warn: float = 0.25
    risk_free_rate: float = 0.03


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class Config:
    futu: FutuConfig = field(default_factory=FutuConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    web: WebConfig = field(default_factory=WebConfig)
    root: Path = field(default_factory=project_root)

    @property
    def db_path(self) -> Path:
        p = Path(self.storage.db_path)
        return p if p.is_absolute() else self.root / p


def _section(raw: dict[str, Any], key: str, cls):
    data = raw.get(key) or {}
    known = {f for f in cls.__dataclass_fields__}
    return cls(**{k: v for k, v in data.items() if k in known})


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    root = project_root()
    if path is not None:
        cfg_path = Path(path)
    else:
        cfg_path = next(
            (root / name for name in DEFAULT_CONFIG_NAMES if (root / name).exists()),
            root / "config.example.yaml",
        )
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    return Config(
        futu=_section(raw, "futu", FutuConfig),
        storage=_section(raw, "storage", StorageConfig),
        sync=_section(raw, "sync", SyncConfig),
        analysis=_section(raw, "analysis", AnalysisConfig),
        web=_section(raw, "web", WebConfig),
        root=root,
    )
