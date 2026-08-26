"""Read-only viewer layer over a results directory.

Nothing in this package ever writes to the results tree.
"""

from clousight_bench.viewer.data import list_records, load_record, load_trajectory

__all__ = ["list_records", "load_record", "load_trajectory"]
