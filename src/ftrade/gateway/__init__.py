from .base import Gateway, GatewayError  # noqa: F401


def build_gateway(cfg, kind: str = "futu"):
    """按名字构造网关。kind: futu | mock"""
    if kind == "mock":
        from .mock import MockGateway

        return MockGateway(cfg)
    from .futu_gw import FutuGateway

    return FutuGateway(cfg)
