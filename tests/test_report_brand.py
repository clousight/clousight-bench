from clousight_bench.core.reporting.renderers import brand


def test_logo_data_uri_is_base64_png():
    uri = brand.logo_data_uri()
    assert uri.startswith("data:image/png;base64,") and len(uri) > 100


def test_provider_logo_maps_adapter_to_svg():
    assert "<svg" in (brand.provider_logo("aliyun-agentrun") or "")
    assert "<svg" in (brand.provider_logo("aws-agentcore") or "")
    assert brand.provider_logo("local-sim") is None


def test_primary_brand_token():
    assert brand.BRAND_HSL["600"] == "217 71% 51%"
