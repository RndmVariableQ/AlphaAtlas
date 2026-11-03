"""Open a version's explicitly pinned local data through existing MarketData adapters."""

from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.operators import OperatorRegistry


def open_market(root, version):
    data = version["data"]
    sources = data["sources"]
    if data["adapter"] == "parquet":
        source = sources["base"]
        market = ParqMarketData(
            root / source["path"],
            version["universe"],
            allow_incomplete=source["allow_incomplete"],
        )
        checks = [(market, source)]
    elif data["adapter"] == "zookeeper_monthly_dsl_v2":
        from alpha_atlas.assets.zookeeper import ZookeeperMarketData

        if (
            version["universe"] != "all"
            or sources["base"]["allow_incomplete"]
            or not sources["curve"]["allow_incomplete"]
        ):
            raise ValueError("monthly adapter requires all universe and explicit incomplete curve")
        market = ZookeeperMarketData(
            root,
            plan=version["data_recipe"],
            registry=OperatorRegistry(versions=version["operator_versions"]),
            base_dir=sources["base"]["path"],
            curve_dir=sources["curve"]["path"],
        )
        checks = [(market.base, sources["base"]), (market.curve, sources["curve"])]
    else:
        raise ValueError(f"unsupported data adapter: {data['adapter']}")
    try:
        for source_market, source in checks:
            if source_market.snapshot_id() != source["snapshot_id"]:
                raise ValueError(f"version dataset snapshot mismatch: {source['path']}")
    except Exception:
        market.close()
        raise
    return market
