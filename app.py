"""Entry point for a Hugging Face Space running the Gradio SDK.

Docker Spaces are a paid feature, and Gradio Spaces are not, so this serves the
same FastAPI application the Dockerfile does. The SDK only decides which image is
built and which file is run; nothing here has to be a Gradio app, and this is not
one. Exactly one server binds the port, which is the whole point.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# Run from the checkout without installing it. A Space installs requirements.txt and
# then runs this file, so relying on the package being installed adds a step that can
# fail on an image we do not control.
SRC = ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SEED = ROOT / "deploy" / "factlayer.db.gz"
DB = Path(os.getenv("FACTLAYER_DB", "/tmp/factlayer.db"))


def unpack_corpus() -> None:
    """Lay down the prepared knowledge layer on first boot.

    The Space filesystem resets when the container restarts, so this runs every
    time and the site always opens with the six starter documents already read.
    """
    if DB.exists() or not SEED.exists():
        return
    DB.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(SEED, "rb") as packed, open(DB, "wb") as unpacked:
        shutil.copyfileobj(packed, unpacked)


unpack_corpus()
os.environ.setdefault("FACTLAYER_DB", str(DB))
os.environ.setdefault("FACTLAYER_UPLOADS", "/tmp/uploads")

from factlayer.api import app  # noqa: E402  (import after the corpus is in place)

if __name__ == "__main__":
    import uvicorn

    # The host names the port it expects to reach; take it from the environment
    # rather than assuming, and fall back to the Spaces default.
    port = os.getenv("GRADIO_SERVER_PORT") or os.getenv("PORT") or "7860"
    uvicorn.run(app, host="0.0.0.0", port=int(port))
