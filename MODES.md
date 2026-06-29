# MJAI Modes

Modes are defined in `~/.mjai/config.yaml`. Each mode runs a list of action scripts when activated. Claude picks the best mode based on what you say.

## Built-in modes

### DEEP
**Trigger phrases:** "time to grind", "heads down", "let me focus", "coding session", "deep work"

Turns on DND, lowers volume, opens VS Code and iTerm2.

### COMMS
**Trigger phrases:** "let me check messages", "catch up on slack", "comms time"

Turns off DND, opens Slack, Discord, WhatsApp.

### FLOW
**Trigger phrases:** "creative mode", "let me write", "design time", "flow state"

Turns on DND, opens Spotify for background music.

### ADMIN
**Trigger phrases:** "admin stuff", "let me check email", "planning time", "scheduling"

Turns off DND, opens browser for email/calendar.

### REST
**Trigger phrases:** "taking a break", "stepping away", "rest time", "afk"

Turns on DND, mutes volume.

### BOOT
System startup only. Never suggested by Claude.

## Adding a custom mode

```yaml
# In ~/.mjai/config.yaml
modes:
  STUDY:
    description: "Deep reading and research — no interruptions, references open"
    actions:
      - script: set_dnd
        args: ["on"]
      - script: open_app
        args: ["Safari"]
      - script: set_volume
        args: ["20"]
      - script: notify
        args: ["STUDY mode. Reading time."]
```

## Conversational responses

If your intent doesn't match any mode, Claude responds conversationally and the answer shows in the overlay bar. Examples:

- "what time is it?" → shows current time
- "what am I working on?" → mentions active app or recent git commits
- "how's my focus today?" → reflects on mode history

## Future modes (planned)

- **SHIP** — deploy mode: opens terminal, pushes code, opens PR dashboard
- **MEET** — meeting mode: opens Zoom/Teams, joins calendar event
- **REVIEW** — code review: opens GitHub PRs, diff view
