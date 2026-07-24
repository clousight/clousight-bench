"""Guardrail: every example asset manifest stays valid as the schema evolves.

Parses each template's `assets:` block via load_asset_specs -- no downloads, no
network. Keeps the copy-paste templates honest (required fields, valid source,
remote-needs-license, etc.)."""
from pathlib import Path

import yaml

from clousight_bench.core.assets import REMOTE, load_asset_specs

_DIR = Path(__file__).resolve().parents[1] / "examples" / "asset-manifests"


def _manifests():
    return sorted(_DIR.glob("*.yaml"))


def test_examples_exist():
    assert _manifests(), f"no example manifests under {_DIR}"


def test_every_example_manifest_parses():
    for path in _manifests():
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        specs = load_asset_specs(manifest)  # raises AssetError on any invalid asset
        assert specs, f"{path.name} declares no assets"
        for spec in specs:
            assert spec.name and spec.source
            if spec.source == REMOTE:
                # remote templates must be auditable: uri + license present
                assert spec.uri and spec.license, f"{path.name}: remote asset needs uri+license"


def test_the_three_tiers_are_represented():
    sources = {
        s.source
        for path in _manifests()
        for s in load_asset_specs(yaml.safe_load(path.read_text(encoding="utf-8")))
    }
    assert {"bundled", "remote", "private"} <= sources
