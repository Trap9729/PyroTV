"""Production entry point: a zero-argument app factory reading the environment.

The systemd unit runs::

    uvicorn pyrotv.main:app --factory --host 0.0.0.0 --port 80 \
        --no-proxy-headers --no-server-header --no-access-log \
        --timeout-graceful-shutdown 3

`create_app` needs a `Config`, so it cannot be used with `--factory` directly.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .app import create_app
from .config import load_config


def app() -> FastAPI:
    # Let the app's own INFO messages (boot cleanup, catalogue errors) reach
    # the journal; uvicorn only configures its own loggers.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return create_app(load_config())
