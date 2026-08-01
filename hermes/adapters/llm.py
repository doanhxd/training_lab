from __future__ import annotations

import json
import os
import shlex
import subprocess

from trading_lab.hermes.memory import HermesMemoryStore
from trading_lab.hermes.prompts.base import PROMPT_TEMPLATE
from trading_lab.hermes.skills import get_skill_profile
from trading_lab.models import StrategySpec


def generate_llm_strategy(skill_name: str | None = None, memory: HermesMemoryStore | None = None) -> StrategySpec:
    command = os.environ.get("HERMES_LLM_COMMAND")
    if not command:
        raise RuntimeError("HERMES_LLM_COMMAND is not set")
    memory = memory or HermesMemoryStore()
    skill = get_skill_profile(skill_name)
    prompt = _build_prompt(skill.name, memory)
    completed = subprocess.run(
        shlex.split(command),
        input=prompt,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    payload = json.loads(completed.stdout)
    return StrategySpec.model_validate(payload)


def _build_prompt(skill_name: str, memory: HermesMemoryStore) -> str:
    recent_reasons = memory.recent_rejection_reasons(limit=10)
    best = memory.best_strategies(limit=3)
    context = {
        "requested_skill": skill_name,
        "recent_rejection_reasons": recent_reasons,
        "best_prior_strategies": [record.spec for record in best],
    }
    return PROMPT_TEMPLATE + "\nContext JSON:\n" + json.dumps(context, indent=2, default=str)
