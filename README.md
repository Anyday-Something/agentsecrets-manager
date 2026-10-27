# AgentSecrets Menu

**A fast, local-first macOS menu bar app for [AgentSecrets](https://agentsecrets.tech).**
Manage workspaces, projects, environments, secrets and allowed domains from one small popover, without ever showing a secret value.

The AgentSecrets CLI is great, but juggling `cd`, `project use`, `AGENTSECRETS_ENV=…` and three environments per key adds up. The official dashboard is still *Coming Soon*. This app puts all of it one click away in your menu bar.

```
┌──────────────────────────────────────────────────────────────┐
│  Projects        │  my-api                         ✎  🗑      │
│  ─────────       │  ───────────────────────────────────────  │
│  ● my-api        │  DATABASE_URL           DEV  STG  PROD  │
│    web-app       │  STRIPE_SECRET_KEY      DEV  STG  PROD  │
│    worker        │  OPENAI_API_KEY         DEV  STG  PROD  │
│                  │  SENTRY_DSN             DEV  STG  PROD  │
│  Allowed domains │                                           │
└──────────────────────────────────────────────────────────────┘
        green tag = set · grey tag = unset · drag to move
```

## Why you'll like it

- **One glance per project.** Every key is one row with three small tags: **DEV · STG · PROD**. Green means set, grey means unset. You see the gaps at once.
- **Drag & drop secrets between projects.** Drop a key on another project to **move** it with all its values. Hold **⌥** to **copy** it instead.
- **Drag between environments.** Drag a green tag onto another tag to move that value from `development` to `production`. ⌥ copies it.
- **Edit in place.** Click a tag and a one-line editor opens right under the row. Press ↵ to save or Esc to cancel. No dialogs, no windows.
- **Click a name to copy it** for your code or `.env.example`.
- **Instant.** It shows the last known lists in about 20 ms and refreshes from the cloud in the background. Switching projects takes milliseconds, and edits are optimistic: they appear at once and roll back if the CLI refuses them.
- **Values stay secret.** The app never reads or shows a value. Moves are handed from one CLI call to the next, so values never reach the UI.
- **Allowed domains.** View, add and remove the workspace allowlist (the domains the proxy may send credentials to), with recent activity.
- **Calm UI.** Icon buttons with tooltips, the CLI's own teal palette, and automatic light and dark mode.

## Install

You need macOS 12 or later, the Xcode Command Line Tools (`xcode-select --install`), and the [AgentSecrets CLI](https://agentsecrets.tech), installed and logged in:

```bash
agentsecrets login
```

Then build the app:

```bash
git clone https://github.com/Anyday-Something/agentsecrets-manager.git
cd agentsecrets-manager
./build_menubar.sh
open ~/Applications/"AgentSecrets Menu.app"
```

A key icon appears in your menu bar. Left-click opens the manager. Right-click offers *Open in Browser*, *Reload* and *Quit*.

Prefer a browser tab? Run `python3 agentsecrets_gui.py` and open http://127.0.0.1:8787. Or use `./build_app.sh`, which builds a small launcher app that does the same thing.

## What you can do

| Area | Actions |
|---|---|
| Workspaces | switch, create |
| Projects | list, create, rename and edit the description, delete |
| Secrets | add, set or change per environment, unset, delete everywhere, filter, import a pasted `.env`, click to copy the name |
| Environments | copy all secrets from one environment to another, clear an environment |
| Drag & drop | move or copy (⌥) keys between projects, and single values between environments |
| Allowed domains | list, add several at once, remove, view recent activity |

AgentSecrets has three fixed environments (`development`, `staging`, `production`). Changes to production, and deleting a project, ask for your AgentSecrets password, just like the CLI. You type it once, inline, and it stays in memory only while the app runs. It is never written to disk.

## How it works

It is a thin GUI over the official CLI. Encryption, auth and the keychain stay the CLI's job, and nothing is reimplemented.

- **`agentsecrets_gui.py`** is a Python server that uses only the standard library and listens on `127.0.0.1` only. It runs `agentsecrets` commands in a pseudo-terminal and answers their interactive prompts.
- **`ui.html`** is the whole interface: one file with no dependencies and no build step.
- **`menubar/main.swift`** is a tiny AppKit menu bar app. It starts the server and shows the UI in a popover.

Some details worth knowing:

- **Per-project folders.** Each project gets its own folder under `~/Library/Application Support/AgentSecretsManager/projects/`. The environment is chosen per command with `AGENTSECRETS_ENV`, so the project and environment active in your terminal never change.
- **Moving a secret** uses only documented CLI commands, because there is no `secrets move`. `agentsecrets env -- …` injects the source value from the keychain into a child process, the child runs `secrets set` for the target, and then the source is deleted with `secrets delete`.
- **Local first.** Key names (never values) are cached in `snapshot.json`. Reads run in parallel, writes run one at a time, and all projects are preloaded at startup.
- **Snappy CLI calls.** Commands run with `TERM=screen-256color`, so the CLI's terminal library never probes for colours or the cursor position. That saves about 5 seconds per command.
- **Unset keys.** AgentSecrets stores a key only once it has a value. A key with no value anywhere (just added, or dragged in) is kept locally in `unset_keys.json` until you set it.
- **Locked down.** The server checks the `Host` header and a random token created on each run, so other websites can't drive it.

Logs are written to `~/Library/Logs/AgentSecretsManager.log`.

## Project layout

```
agentsecrets_gui.py   local server wrapping the CLI
ui.html               the interface
menubar/main.swift    menu bar app (NSStatusItem + WKWebView popover)
build_menubar.sh      builds "AgentSecrets Menu.app"
build_app.sh          builds the browser launcher "AgentSecrets Manager.app"
```

## Disclaimer

This is an unofficial community tool. It is not affiliated with or endorsed by AgentSecrets. It only calls the `agentsecrets` CLI you have installed.

## License

MIT
