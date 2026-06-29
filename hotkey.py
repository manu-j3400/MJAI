"""pynput push-to-talk hotkey listener."""
from typing import Callable, Optional
from pynput import keyboard
from pynput.keyboard import Key, Listener


_KEY_MAP: dict[str, Key] = {
    "cmd": Key.cmd, "command": Key.cmd,
    "ctrl": Key.ctrl, "control": Key.ctrl,
    "shift": Key.shift,
    "alt": Key.alt, "option": Key.alt,
    "space": Key.space,
    "tab": Key.tab,
    "esc": Key.esc, "escape": Key.esc,
    "enter": Key.enter, "return": Key.enter,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
}

_ALIASES = {
    Key.cmd_l: Key.cmd, Key.cmd_r: Key.cmd,
    Key.ctrl_l: Key.ctrl, Key.ctrl_r: Key.ctrl,
    Key.shift_l: Key.shift, Key.shift_r: Key.shift,
    Key.alt_l: Key.alt, Key.alt_r: Key.alt,
}


def parse_hotkey(hotkey_str: str) -> frozenset:
    parts = [p.strip().lower() for p in hotkey_str.split("+")]
    keys = set()
    for part in parts:
        if part in _KEY_MAP:
            keys.add(_KEY_MAP[part])
        else:
            keys.add(keyboard.KeyCode.from_char(part))
    return frozenset(keys)


def start_listener(
    hotkey_str: str,
    on_start: Optional[Callable] = None,
    on_stop: Optional[Callable] = None,
) -> Listener:
    """
    Push-to-talk listener.
    on_start fires when the full combo is held down (once per press).
    on_stop fires when any combo key is released while combo was active.
    """
    combo = parse_hotkey(hotkey_str)
    pressed: set = set()
    fired = False

    def _norm(key):
        return _ALIASES.get(key, key)

    def on_press(key):
        nonlocal fired
        pressed.add(_norm(key))
        if not fired and frozenset(pressed) >= combo:
            fired = True
            if on_start:
                on_start()

    def on_release(key):
        nonlocal fired
        nk = _norm(key)
        was_fired = fired
        pressed.discard(nk)
        if was_fired and nk in combo:
            fired = False
            if on_stop:
                on_stop()

    listener = Listener(on_press=on_press, on_release=on_release)
    listener.start()
    return listener
