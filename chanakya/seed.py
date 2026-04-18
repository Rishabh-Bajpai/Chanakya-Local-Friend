from __future__ import annotations

import json
from pathlib import Path

from chanakya.model import AgentProfileModel
from chanakya.store import ChanakyaStore


def load_agent_seeds(store: ChanakyaStore, seed_file: Path) -> None:
    if not seed_file.exists():
        return
    raw_items = json.loads(seed_file.read_text(encoding="utf-8"))
    for item in raw_items:
        agent_id = str(item["id"])
        try:
            profile = store.get_agent_profile(agent_id)
        except KeyError:
            store.upsert_agent_profile(AgentProfileModel.from_seed(item))
            continue
        profile.update_from_seed(item)
        store.upsert_agent_profile(profile)
