"""Keep the test suite deterministic and offline.

Production/API runs still load the repository `.env`; tests explicitly override
the provider before the factory reads it.
"""

import os
import tempfile
import atexit
from pathlib import Path

# Set this before test collection imports configuration. Subprocesses inherit
# the same isolated root, including modules that import path constants.
_storage = tempfile.TemporaryDirectory(prefix="annotation-pytest-", ignore_cleanup_errors=True)
atexit.register(_storage.cleanup)
os.environ["ANNOTATION_STORAGE_DIR"] = str(Path(_storage.name).resolve())
os.environ["BLUEPRINT_CACHE_PATH"] = str(Path(_storage.name) / "cache" / "blueprint")
os.environ["RAG_CHROMA_PATH"] = str(Path(_storage.name) / "chroma")


os.environ["MODEL_PROFILE"] = "offline"
os.environ["MODEL_PROVIDER"] = ""
os.environ["MODEL_NAME"] = "offline-fixture"
os.environ["RAG_ENABLED"] = "false"
os.environ.pop("MODEL_BASE_URL", None)
