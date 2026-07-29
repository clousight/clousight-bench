# API reference

The stable, plugin-facing surface of the core. Anything not documented here is
internal and may change without a version bump.

## Result schema

The data contract every run emits and every plugin reads.

::: clousight_bench.core.schema

## Plugin base classes

Subclass these to add a platform, a dimension, or a product category. Register a
`DomainPack` via the `clousight_bench.domains` entry point.

::: clousight_bench.core.plugin

## Lifecycle orchestrator

::: clousight_bench.core.orchestrator

## Cross-language workload protocol

::: clousight_bench.core.workload

## Asset resolution

Three-tier asset resolution: bundled / remote (checksummed) / private (via a
licensed resolver).

::: clousight_bench.core.assets
