"""Config loader for UnamOS. Reads ~/.mjai/config.yaml."""
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
class AppTrigger:
    app: str          # app name (partial match)
    mode: str = ""    # switch to this mode
    workflow: str = ""  # or run this workflow


@dataclass
class BatteryTrigger:
    below: int        # fire when battery % drops below this
    workflow: str = ""
    mode: str = ""


@dataclass
class NetworkTrigger:
    ssid: str         # WiFi SSID (partial match)
    workflow: str = ""
    mode: str = ""


@dataclass
class FileTrigger:
    path: str         # directory to watch
    pattern: str = "*"
    workflow: str = ""


@dataclass
class AutoTriggers:
    app_events: list[AppTrigger] = field(default_factory=list)
    battery: list[BatteryTrigger] = field(default_factory=list)
    network: list[NetworkTrigger] = field(default_factory=list)
    files: list[FileTrigger] = field(default_factory=list)
    imessage: bool = False


@dataclass
class Config:
    modes: dict[str, Mode]
    hotkey: str
    claude_model: str
    claude_system_prompt: str
    windows_host: str = ""
    auto_triggers: AutoTriggers = field(default_factory=AutoTriggers)

    def mode_list_for_claude(self) -> str:
        lines = []
        for name, mode in self.modes.items():
            lines.append(f"- {name}: {mode.description}")
        return "\n".join(lines)


def load() -> Config:
    """Load and parse config.yaml."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")

    with open(CONFIG_PATH) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    modes = {}
    for name, mode_data in raw.get("modes", {}).items():
        actions = [
            Action(script=a["script"], args=[str(x) for x in a.get("args", [])])
            for a in mode_data.get("actions", [])
        ]
        modes[name] = Mode(name=name, description=mode_data["description"], actions=actions)

    triggers_raw = raw.get("triggers", {})
    auto_raw = triggers_raw.get("auto", {})
    claude_cfg = raw.get("claude", {})

    app_events = [
        AppTrigger(app=t["app"], mode=t.get("mode", ""), workflow=t.get("workflow", ""))
        for t in auto_raw.get("app_events", [])
    ]
    battery = [
        BatteryTrigger(below=t["below"], workflow=t.get("workflow", ""), mode=t.get("mode", ""))
        for t in auto_raw.get("battery", [])
    ]
    network = [
        NetworkTrigger(ssid=t["ssid"], workflow=t.get("workflow", ""), mode=t.get("mode", ""))
        for t in auto_raw.get("network", [])
    ]
    files = [
        FileTrigger(path=t["path"], pattern=t.get("pattern", "*"), workflow=t.get("workflow", ""))
        for t in auto_raw.get("files", [])
    ]
    imessage = auto_raw.get("imessage", False)

    auto_triggers = AutoTriggers(
        app_events=app_events,
        battery=battery,
        network=network,
        files=files,
        imessage=imessage,
    )

    return Config(
        modes=modes,
        hotkey=triggers_raw.get("hotkey", "cmd+shift+space"),
        claude_model=claude_cfg.get("model", "claude-sonnet-4-6"),
        claude_system_prompt=claude_cfg.get("system_prompt", ""),
        windows_host=raw.get("windows_host", ""),
        auto_triggers=auto_triggers,
    )
