"""Repo-rooted paths and universe config."""

import os
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("FARSEER_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"


def load_universe() -> dict:
    with open(CONFIG_DIR / "universe.yaml") as f:
        return yaml.safe_load(f)
