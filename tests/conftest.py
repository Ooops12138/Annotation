"""Keep the test suite deterministic and offline.

Production/API runs still load the repository `.env`; tests explicitly override
the provider before the factory reads it.
"""

import os


os.environ["MODEL_PROFILE"] = "offline"
os.environ["MODEL_PROVIDER"] = ""
os.environ["MODEL_NAME"] = "offline-fixture"
os.environ["RAG_ENABLED"] = "false"
os.environ.pop("MODEL_BASE_URL", None)
