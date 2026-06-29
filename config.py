"""Config loader for MJAI. Reads ~/.mjai/config.yaml."""
import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MJAI_DIR = Path.home() / ".mjai"
CONFIG_PATH = MJAI_DIR / "config.yaml"
ACTIONS_DIR = MJAI_DIR / "actions"


@dataclass
class Action:
    script: str
    args: list[str] = field(default_factory=list)


@dataclass
class Mode:
    name: str
    description: str
    actions: list[Action]


@dataclass
class Config:
    modes: dict[str, Mode]
    hotkey: str
    claude_model: str
    claude_system_prompt: str

    def mode_list_for_claude(self) -> str:
        """Returns a formatted list of modes + descriptions for the Claude prompt."""
        lines = []
        for name, mode in self.modes.items():
            lines.append(f"- {name}: {mode.description}")
        return "\n".join(lines)


def load() -> Config:
    """Load and parse config.yaml. Raises on invalid config."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}\nRun: cp config.yaml.example ~/.mjai/config.yaml")

    with open(CONFIG_PATH) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    modes = {}
    for name, mode_data in raw.get("modes", {}).items():
        actions = [
            Action(script=a["script"], args=[str(x) for x in a.get("args", [])])
            for a in mode_data.get("actions", [])
        ]
        modes[name] = Mode(name=name, description=mode_data["description"], actions=actions)

    triggers = raw.get("triggers", {})
    claude_cfg = raw.get("claude", {})

    return Config(
        modes=modes,
        hotkey=triggers.get("hotkey", "cmd+shift+space"),
        claude_model=claude_cfg.get("model", "claude-sonnet-4-6"),
        claude_system_prompt=claude_cfg.get("system_prompt", ""),
    )
