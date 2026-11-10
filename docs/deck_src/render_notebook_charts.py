"""Re-render the walkthrough notebook's charts at slide resolution into docs/deck_src/img/.

Run from the repo root:
    uv run --group demo python docs/deck_src/render_notebook_charts.py

It converts notebooks/walkthrough.ipynb to a script, raises the figure DPI, runs it
headless (from notebooks/, as the notebook expects) and saves every figure as figNN.png.
Figure order = chart order in the notebook (fig01 raw tenure ... fig09 dwelling incl. MB).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = Path(__file__).resolve().parent / "img"

with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(
        [sys.executable, "-m", "jupyter", "nbconvert", "--to", "script",
         str(ROOT / "notebooks" / "walkthrough.ipynb"), "--output-dir", tmp, "--output", "nb"],
        check=True, capture_output=True,
    )
    src = (Path(tmp) / "nb.py").read_text()
    src = src.replace('"figure.dpi": 110', '"figure.dpi": 220').replace("shutil.rmtree(WORK)", "")
    src = "import matplotlib; matplotlib.use('Agg')\ndisplay = print\nget_ipython = None\n" + src
    src += f"""
import matplotlib.pyplot as _p
from pathlib import Path as _P
_P({str(IMG)!r}).mkdir(parents=True, exist_ok=True)
for _i, _n in enumerate(_p.get_fignums(), 1):
    _p.figure(_n).savefig(_P({str(IMG)!r}) / f"fig{{_i:02d}}.png", dpi=220)
print("saved", len(_p.get_fignums()), "figures to", {str(IMG)!r})
"""
    script = Path(tmp) / "run.py"
    script.write_text(src)
    subprocess.run([sys.executable, str(script)], cwd=ROOT / "notebooks", check=True,
                   env={**os.environ, "MPLBACKEND": "Agg"})
