from __future__ import annotations

from pathlib import Path

import yaml


def read_yaml(path: str | Path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def write_text(path: str | Path, content: str):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
