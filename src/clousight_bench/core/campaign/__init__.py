"""The ECS prod-run subsystem: campaign spec/manifest/channel, the driver-host
controller + its watchdog, and the submit/status/logs/fetch/teardown client.

Grouped together (and separable from the local-run kernel in ``core``) — a run
plan submitted to a cloud-resident controller flows: prod_submit → channel →
controller (on the ECS driver host) → watchdog. Kept as its own package so the
core kernel stays focused on the single-run lifecycle.
"""
