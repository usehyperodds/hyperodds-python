"""Run a notebook top to bottom, the way Colab's "Run all" does, and save it with its outputs.

    uv run --group notebook python scripts/run_notebook.py notebooks/hip4-in-colab.ipynb [out.ipynb]

Without ``out.ipynb`` the run is saved in the temp directory; the committed notebook changes only
when you name it as the output. The notebook's first code cell installs `hyperodds` from GitHub
with %pip. To run it against code that is not there yet, name another source in
``HYPERODDS_INSTALL_FROM``, such as ``git+file://$PWD``: the run installs from it, and the saved
notebook keeps the line as written.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

import nbclient
import nbformat

GITHUB = "git+https://github.com/usehyperodds/hyperodds-python"

source = Path(sys.argv[1])
target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.gettempdir()) / source.name

notebook = nbformat.read(source, as_version=4)
install = next(c for c in notebook.cells if c.cell_type == "code" and "%pip install" in c.source)
written = install.source
install.source = written.replace(GITHUB, os.environ.get("HYPERODDS_INSTALL_FROM", GITHUB))

started = time.monotonic()
nbclient.NotebookClient(
    notebook,
    kernel_name="python3",
    timeout=300,
    record_timing=False,
    resources={"metadata": {"path": str(source.parent)}},
).execute()
install.source = written
nbformat.write(notebook, target)
print(f"ran {source} in {time.monotonic() - started:.1f}s, saved to {target}")
