"""Start the PyroTV backend for the e2e tests.

Configuration comes entirely from the environment (PYROTV_DATA_DIR,
PYROTV_FAKE_HW=1, ...); the port from PYROTV_E2E_PORT (default 8790).
"""
import os

import uvicorn

from pyrotv.app import create_app
from pyrotv.config import load_config

app = create_app(load_config())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PYROTV_E2E_PORT", "8790")))
