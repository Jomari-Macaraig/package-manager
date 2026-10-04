"""FastAPI backend for the packing playground.

The server renders headless, so it forces matplotlib's Agg backend before
anything imports pyplot. This lives here, not in `packing`, because the labs
notebook imports `packing` too and needs its inline backend.
"""

import matplotlib

matplotlib.use("Agg")
