import os
import sys

# Ensure backend modules are importable when Vercel runs this file
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from main import app  # noqa: E402

