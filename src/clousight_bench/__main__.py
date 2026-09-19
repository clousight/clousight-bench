"""``python -m clousight_bench`` — the same entry point as the ``csbench`` script.

It exists so a subprocess can be started with ``sys.executable -m
clousight_bench``, which resolves through the running interpreter rather than
through PATH. The web console starts runs that way: a server has no business
assuming which ``csbench`` a shell would have found.
"""

import sys

from clousight_bench.cli.app import main

if __name__ == "__main__":
    sys.exit(main())
