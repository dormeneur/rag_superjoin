"""Entry point for a Hugging Face Space running the Gradio SDK.

Docker Spaces are a paid feature, and Gradio Spaces are not, so this serves the
same FastAPI application the Dockerfile does. Gradio is mounted on a side path
only to satisfy the SDK; the site itself is the FastAPI app at "/".
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

try:
    import gradio as gr

    with gr.Blocks() as _sdk_probe:
        gr.Markdown("The Fact Knowledge Layer is served at [/](/).")

    app = gr.mount_gradio_app(app, _sdk_probe, path="/gradio")
except Exception:
    # Gradio only exists to keep the Space SDK happy. Without it the FastAPI app
    # still serves everything, which is what matters.
    pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
