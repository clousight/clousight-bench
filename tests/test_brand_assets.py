from importlib import resources


def test_brand_assets_are_packaged():
    root = resources.files("clousight_bench.resources").joinpath("brand")
    assert root.joinpath("logo.png").is_file()
    providers = root.joinpath("providers")
    for name in ("alibaba", "aws", "huawei", "gcp"):
        assert providers.joinpath(f"{name}.svg").is_file()
