import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, config

DIST = Path(os.environ.get("FRANK_LAB_DIST", config.PKG_ROOT.parent / "monster-lab" / "dist"))

# Mounted apps don't get lifespan events, so the API's background loops are started from here.
app = FastAPI(title="Steinfrank", lifespan=lambda _: api.lifespan(api.app))
app.mount("/frank", api.app)
app.mount("/", StaticFiles(directory=DIST, html=True), name="lab")
