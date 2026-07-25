from __future__ import annotations

from pathlib import Path

import yaml


def read_yaml(path: str | Path):
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    if not isinstance(config, dict):
        raise ValueError("config must be a mapping")
    
    return config


def write_text(path: str | Path, content: str):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
