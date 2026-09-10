"""日志配置。"""
from __future__ import annotations

import logging

_CONFIGURED = False


def setup_logging(verbose: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # futu SDK 默认很吵
    logging.getLogger("futu").setLevel(logging.WARNING)
    _CONFIGURED = True
