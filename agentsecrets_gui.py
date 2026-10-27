#!/usr/bin/env python3
"""AgentSecrets Manager — a tiny local GUI for the `agentsecrets` CLI.

Every action runs the official, installed `agentsecrets` binary, so auth,
encryption and the macOS keychain are handled exactly as in the terminal.
The CLI's interactive prompts are answered through a pseudo-terminal.

Only the Python standard library is used. Run:  python3 agentsecrets_gui.py
"""

import contextlib
import errno
import fcntl
import json
import os
import pty
import re
import secrets as pysecrets
import select
import shutil
import signal
import struct
import sys
import termios
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
UI_FILE = os.path.join(HERE, "ui.html")
DATA_DIR = os.path.expanduser("~/Library/Application Support/AgentSecretsManager")
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
ENVS = ["development", "staging", "production"]
PORT = int(os.environ.get("AGENTSECRETS_GUI_PORT", "8787"))
TOKEN = pysecrets.token_urlsafe(24)

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]")
SPINNER = re.compile(r"^[\s⣾⣽⣻⢿⡿⣟⣯⣷⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]*$")
PASSWORD_PROMPT = r"Enter your AgentSecrets password"

# Changes run one at a time (the CLI shares global config and keychain state);
# read-only commands run in parallel.
CLI_LOCK = threading.Lock()
READ_ONLY = [c.split() for c in ("status", "workspace list", "project list", "secrets list",
                                  "workspace allowlist list", "workspace allowlist logs")]


def is_read_only(args):
    return any(list(args[:len(c)]) == c for c in READ_ONLY)


class CLIError(Exception):
    pass


def find_cli():
    path = shutil.which("agentsecrets")
    for p in (path, "/opt/homebrew/bin/agentsecrets", "/usr/local/bin/agentsecrets"):
        if p and os.path.exists(p):
            return p
    return None


CLI = find_cli()


def clean(text):
    """Strip ANSI codes and spinner frames from terminal output."""
    text = ANSI.sub("", text)
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.split("\r")[-1].rstrip()  # keep only the last redraw of a line
        if SPINNER.match(line) and line.strip():
            continue
        lines.append(line)
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n")


def run_cli(args, cwd=None, env_name=None, answers=(), timeout=90):
    """Run `agentsecrets <args>` in a pty.

    `answers` is a list of (regex, text) pairs: when regex appears in the output
    (after the previous match), `text` is typed into the terminal, in order.
    Returns (exit_code, cleaned_output).
    """
    if not CLI:
        raise CLIError("The agentsecrets CLI was not found. Install it with: brew install the-17/tap/agentsecrets")
    # A "screen" TERM stops the CLI's TUI library from querying the terminal
    # (cursor position, background colour). Unanswered, each query costs a ~5 s
    # timeout; answered, the reply can be typed into a prompt as text.
    env = dict(os.environ, TERM="screen-256color", COLUMNS="200", LINES="50")
    env.pop("AGENTSECRETS_PROJECT_DIR", None)
    if env_name:
        env["AGENTSECRETS_ENV"] = env_name
    else:
        env.pop("AGENTSECRETS_ENV", None)

    lock = contextlib.nullcontext() if is_read_only(args) else CLI_LOCK
    with lock:
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                if cwd:
                    os.chdir(cwd)
                os.execve(CLI, [CLI] + list(args), env)
            finally:
                os._exit(127)

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        buf, pos, pending = "", 0, list(answers)
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                os.close(fd)
                raise CLIError("The agentsecrets command timed out.\n\n" + clean(buf))
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(fd, 65536)
                except OSError as e:
                    if e.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                # Don't answer the TUI's terminal queries (cursor position, background
                # colour): a reply can land in a text prompt as typed input.
                buf += chunk.decode("utf-8", "replace")
            if pending:
                m = re.search(pending[0][0], ANSI.sub("", buf[pos:]))
                if m:
                    text = pending.pop(0)[1]
                    # Send the text, then the Enter/Tab on its own so the TUI
                    # doesn't read it as part of a paste.
                    body, end = (text[:-1], text[-1]) if text[-1:] in "\r\t" else (text, "")
                    time.sleep(0.05)
                    if body:
                        os.write(fd, body.encode())
                    if end:
                        time.sleep(0.03)
                        os.write(fd, end.encode())
                    pos = len(buf)
        _, status = os.waitpid(pid, 0)
        os.close(fd)
    code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)
    return code, clean(buf)


def check(code, out, ok_markers=()):
    """Raise if the CLI reported a failure."""
    lowered = out.lower()
    failed = code != 0 or re.search(r"^\s*x |\[err-|failed to|incorrect password|not logged in", out, re.M | re.I)
    if ok_markers and any(m.lower() in lowered for m in ok_markers):
        failed = False
    if failed:
        raise CLIError(out or "The command failed.")
    return out


def table_rows(out):
    rows = []
    for line in out.split("\n"):
        if line.strip().startswith("│"):
            cells = [c.strip() for c in line.strip().strip("│").split("│")]
            rows.append(cells)
    return rows[1:] if rows else []  # drop header row


# ---------- project working directories ----------

def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"


def project_dir(name, relink=False):
    """A private folder bound to the project, so the CLI's cwd-based commands work."""
    d = os.path.join(PROJECTS_DIR, safe_name(name))
    os.makedirs(d, mode=0o700, exist_ok=True)
    pj = os.path.join(d, ".agentsecrets", "project.json")
    linked = False
    if os.path.exists(pj) and not relink:
        try:
            with open(pj) as f:
                linked = json.load(f).get("project_name") == name
        except (OSError, ValueError):
            pass
    if not linked:
        with preserve_selected_project():
            seed_project_dir(d)
            code, out = run_cli(["project", "use", name], cwd=d)
            check(code, out)
    return d


def seed_project_dir(d):
    """Give the folder its own project.json stub.

    The CLI finds its project by walking up from cwd; without a stub it would
    find ~/.agentsecrets/project.json and re-bind your home folder instead.
    """
    os.makedirs(os.path.join(d, ".agentsecrets"), mode=0o700, exist_ok=True)
    pj = os.path.join(d, ".agentsecrets", "project.json")
    if not os.path.exists(pj):
        with open(pj, "w") as f:
            json.dump({"environment": "development"}, f)


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class preserve_selected_project:
    """`project use/create` also changes the CLI's globally selected project.
    Put it back afterwards, so the GUI doesn't change what your terminal uses."""

    def __enter__(self):
        self.before = _read_json(os.path.expanduser("~/.agentsecrets/config.json")).get("selected_project_id")

    def __exit__(self, *exc):
        after = _read_json(os.path.expanduser("~/.agentsecrets/config.json")).get("selected_project_id")
        home = _read_json(os.path.expanduser("~/.agentsecrets/project.json"))
        if self.before and after != self.before and home.get("project_id") == self.before:
            run_cli(["project", "use", home["project_name"]], cwd=os.path.expanduser("~"))
        return False


def remove_project_dir(name):
    shutil.rmtree(os.path.join(PROJECTS_DIR, safe_name(name)), ignore_errors=True)


def with_password(answers, password):
    if password:
        answers = list(answers) + [(PASSWORD_PROMPT, password + "\r")]
    return answers


# ---------- actions ----------

def get_status():
    code, out = run_cli(["status"])
    info = {}
    for line in out.split("\n"):
        m = re.match(r"\s*([A-Za-z ]+):\s{2,}(.*)$", line)
        if m:
            info[m.group(1).strip()] = m.group(2).strip()
    return {"raw": out, "info": info, "loggedIn": "Logged in as" in info}


def list_workspaces():
    code, out = run_cli(["workspace", "list"])
    check(code, out)
    result = []
    for line in out.split("\n"):
        m = re.match(r"^\s*(→)?\s+(.+?)\s+\((\w+)\)\s*$", line)
        if m:
            result.append({"name": m.group(2), "type": m.group(3), "active": bool(m.group(1))})
    return result


def list_projects():
    code, out = run_cli(["project", "list"])
    check(code, out)
    if "no projects" in out.lower():
        return []
    result = []
    for cells in table_rows(out):
        if len(cells) < 3:
            continue
        name = cells[0]
        active = name.startswith("→")
        result.append({"name": name.lstrip("→ ").strip(), "workspace": cells[1],
                       "description": cells[2], "active": active})
    return result


def list_secrets(project):
    # The cloud list (~0.6 s) is the source of truth; the keychain-only list is
    # faster but keeps stale and renamed keys, so the UI shows a snapshot instead.
    d = project_dir(project)
    code, out = run_cli(["secrets", "list", "--remote"], cwd=d)
    check(code, out, ok_markers=["No secrets found"])
    keys = []
    for cells in table_rows(out):
        if len(cells) >= 4:
            keys.append({"key": cells[0], "development": cells[1] == "*",
                         "staging": cells[2] == "*", "production": cells[3] == "*"})
    return keys


# ---------- workspace allowlist ----------
# The proxy only injects credentials into requests to these domains.

DOMAIN_RE = re.compile(r"^[A-Za-z0-9*]([A-Za-z0-9*.-]*[A-Za-z0-9])?(:[0-9]+)?$")


def list_allowlist():
    code, out = run_cli(["workspace", "allowlist", "list"])
    check(code, out, ok_markers=["No domains in allowlist"])
    return [{"domain": c[0], "addedBy": c[1], "addedAt": c[2]} for c in table_rows(out) if len(c) >= 3]


def allowlist_logs():
    code, out = run_cli(["workspace", "allowlist", "logs"])
    check(code, out, ok_markers=["No allowlist activity"])
    return [{"time": c[0], "domain": c[1], "user": c[2], "action": c[3]} for c in table_rows(out) if len(c) >= 4]


def valid_domain(d):
    d = d.strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0]
    if not DOMAIN_RE.match(d):
        raise CLIError("Not a valid domain: %r" % d)
    return d


# ---------- unset keys ----------
# AgentSecrets only stores keys that have a value, and never hands values back
# (they're redacted even from child processes). So a key copied to another
# project starts as a local placeholder: shown as "Unset" in every environment
# until you give it a value somewhere.

PENDING_FILE = os.path.join(DATA_DIR, "unset_keys.json")


def load_pending():
    return _read_json(PENDING_FILE)


def save_pending(data):
    data = {p: sorted(set(keys)) for p, keys in data.items() if keys}
    fd = os.open(PENDING_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)


def add_pending(project, keys):
    data = load_pending()
    data[project] = data.get(project, []) + list(keys)
    save_pending(data)


def remove_pending(project, keys):
    data = load_pending()
    data[project] = [k for k in data.get(project, []) if k not in keys]
    save_pending(data)


def rename_pending(old, new):
    data = load_pending()
    if old in data:
        data[new] = data.get(new, []) + data.pop(old)
        save_pending(data)


def secrets_with_pending(project):
    keys = list_secrets(project)
    known = {s["key"] for s in keys}
    pending = [k for k in load_pending().get(project, []) if k not in known]
    if len(pending) != len(load_pending().get(project, [])):
        remove_pending(project, known)  # placeholders that now have a value somewhere
    for k in pending:
        keys.append({"key": k, "development": False, "staging": False, "production": False, "placeholder": True})
    return sorted(keys, key=lambda s: s["key"])


# The CLI has no command to move or copy a single secret (nothing takes a target
# project, and `environment copy` only copies a whole environment). So this chains
# documented commands: `secrets pull` syncs the source into the keychain,
# `agentsecrets env -- <cmd>` injects it into a child process, and that child runs
# `agentsecrets secrets set` for the target. Values are never printed or shown.
SET_FROM_ENV = """
import os, subprocess, sys
cli, target, target_env, keys = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
missing = [k for k in keys if k not in os.environ]
if missing:
    print("Missing in source: " + ", ".join(missing)); sys.exit(3)
env = dict(os.environ, AGENTSECRETS_ENV=target_env)
sys.exit(subprocess.call([cli, "secrets", "set"] + ["%s=%s" % (k, os.environ[k]) for k in keys], cwd=target, env=env))
"""


def delete_secret(project_dir_, key, env, password):
    answers = [(r"\(y/n\)", "y\r")] if env == "production" else []
    check(*run_cli(["secrets", "delete", key], cwd=project_dir_, env_name=env,
                   answers=with_password(answers, password if env == "production" else "")))


def transfer(src, dst, keys, password, move=True, src_env=None, dst_env=None, present=None):
    """Move (default) or copy keys with their values.

    Between projects: each environment goes to the same environment; ones where
    the key isn't set stay unset. With src_env/dst_env: one environment to another
    (same or different project). `present` ({key: {env: bool}}) is what the UI
    already knows; it saves listing the source again."""
    pairs = [(src_env, dst_env or src_env)] if src_env else [(e, e) for e in ENVS]
    if src == dst and all(a == b for a, b in pairs):
        raise CLIError("Drop it somewhere else.")
    if present is None:
        present = {s["key"]: s for s in list_secrets(src)}
    keys = [k for k in keys if k in present]
    src_dir, dst_dir = project_dir(src), project_dir(dst)
    done = []
    for from_env, to_env in pairs:
        env_keys = [k for k in keys if present[k].get(from_env)]
        if not env_keys:
            continue
        args = ["env", "--", "/usr/bin/python3", "-I", "-c", SET_FROM_ENV, CLI, dst_dir, to_env] + env_keys
        answers = with_password([], password if "production" in (from_env, to_env) else "")
        code, out = run_cli(args, cwd=src_dir, env_name=from_env, answers=answers)
        if code == 3:  # not in this Mac's keychain yet: sync it from the cloud once, then retry
            check(*run_cli(["secrets", "pull", "--force"], cwd=src_dir, env_name=from_env))
            code, out = run_cli(args, cwd=src_dir, env_name=from_env, answers=answers)
        check(code, out)
        done.append((from_env, env_keys))
    if src != dst:
        remove_pending(dst, keys)
    if move:
        for from_env, env_keys in done:
            for k in env_keys:
                delete_secret(src_dir, k, from_env, password)
        if src != dst:
            remove_pending(src, keys)
    return {"envs": [e for e, _ in done]}


def valid_key(key):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", key):
        raise CLIError("Invalid key %r: use letters, digits, _ . - and don't start with a digit." % key)
    return key


def handle(action, p):
    env = p.get("env")
    if env is not None and env not in ENVS and env != "all":
        raise CLIError("Unknown environment: %s" % env)
    pw = p.get("password") or ""

    if action == "workspace.switch":
        return check(*run_cli(["workspace", "switch", p["name"]]))
    if action == "workspace.create":
        return check(*run_cli(["workspace", "create", p["name"]]))

    if action == "allowlist.add":
        domains = [valid_domain(d) for d in p["domains"] if d.strip()]
        if not domains:
            raise CLIError("Enter at least one domain.")
        return check(*run_cli(["workspace", "allowlist", "add"] + domains,
                              answers=with_password([], pw)))
    if action == "allowlist.remove":
        return check(*run_cli(["workspace", "allowlist", "remove", valid_domain(p["domain"])],
                              answers=with_password([], pw)))

    if action == "project.create":
        name = p["name"].strip()
        d = os.path.join(PROJECTS_DIR, safe_name(name))
        os.makedirs(d, mode=0o700, exist_ok=True)
        with preserve_selected_project():
            seed_project_dir(d)
            code, out = run_cli(["project", "create", name], cwd=d,
                                answers=[("Description", (p.get("description") or "") + "\r")])
        out = check(code, out)
        if "created" not in out.lower():
            raise CLIError(out or "Project was not created.")
        return out
    if action == "project.update":
        code, out = run_cli(["project", "update", p["name"]], answers=[
            ("New Project Name", (p.get("newName") or "") + "\t"),
            ("New Description", (p.get("description") or "") + "\r"),
        ])
        out = check(code, out)
        if p.get("newName") and p["newName"] != p["name"]:
            remove_project_dir(p["name"])
            rename_pending(p["name"], p["newName"])
        return out
    if action == "project.delete":
        code, out = run_cli(["project", "delete", p["name"]],
                            answers=with_password([("cannot be undone", "y")], pw))
        out = check(code, out)
        if "deleted" not in out.lower():
            raise CLIError(out or "Project was not deleted.")
        remove_project_dir(p["name"])
        remove_pending(p["name"], load_pending().get(p["name"], []))
        return out

    if action == "secret.set":
        d = project_dir(p["project"])
        pairs = p.get("pairs") or {p["key"].strip(): p.get("value", "")}
        for key in pairs:
            valid_key(key)
        kv = ["%s=%s" % (k, v) for k, v in pairs.items()]
        if env == "all":
            out = check(*run_cli(["secrets", "set", "--all-envs"] + kv, cwd=d,
                                 answers=with_password([(r"Continue\? \(y/n\)", "y\r")], pw)))
            remove_pending(p["project"], list(pairs))
            return out
        out = check(*run_cli(["secrets", "set"] + kv, cwd=d, env_name=env,
                             answers=with_password([], pw)))
        remove_pending(p["project"], list(pairs))
        return out
    if action == "secret.delete":
        return delete_secret(project_dir(p["project"]), p["key"], env, pw)
    if action == "secret.copykeys":
        if p["from"] == p["to"]:
            raise CLIError("Drop it on a different project.")
        add_pending(p["to"], [valid_key(k) for k in p["keys"]])
        return "ok"
    if action == "secret.transfer":
        return transfer(p["from"], p["to"], [valid_key(k) for k in p["keys"]], pw, move=bool(p.get("move")),
                        src_env=p.get("fromEnv"), dst_env=p.get("toEnv"), present=p.get("present"))
    if action == "secret.addkey":
        add_pending(p["project"], [valid_key(p["key"].strip())])
        return "ok"
    if action == "secret.unsetkey":
        remove_pending(p["project"], [p["key"]])
        return "ok"

    if action == "env.copy":
        d = project_dir(p["project"])
        return check(*run_cli(["environment", "copy", p["from"], p["to"]], cwd=d,
                              answers=[(r"Continue\? \(y/n\)", "y\r")]), ok_markers=["Nothing to copy"])
    if action == "env.clean":
        d = project_dir(p["project"])
        return check(*run_cli(["environment", "clean"], cwd=d, env_name=env,
                              answers=with_password([(r"Continue\? \(y/n\)", "y\r")], pw)),
                     ok_markers=["No secrets found"])

    raise CLIError("Unknown action: %s" % action)


# ---------- snapshot ----------
# The last results of every list call (names only, never values), so the UI can
# show something instantly at launch and on every switch, then refresh.

SNAPSHOT_FILE = os.path.join(DATA_DIR, "snapshot.json")
SNAPSHOT_LOCK = threading.Lock()


def remember(kind, value, project=None):
    with SNAPSHOT_LOCK:
        snap = _read_json(SNAPSHOT_FILE)
        if project is None:
            snap[kind] = value
        else:
            snap.setdefault(kind, {})[project] = value
        fd = os.open(SNAPSHOT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(snap, f)
    return value


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        # Block DNS-rebinding and other sites calling this server.
        host = self.headers.get("Host", "")
        if host not in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT):
            return False
        return self.path.startswith("/?") or self.path == "/" or self.headers.get("X-Token") == TOKEN

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                with open(UI_FILE, "rb") as f:
                    html = f.read().replace(b"__TOKEN__", TOKEN.encode())
                return self._send(200, html, "text/html; charset=utf-8")
            if url.path == "/api/snapshot":
                with SNAPSHOT_LOCK:
                    return self._send(200, _read_json(SNAPSHOT_FILE))
            if url.path == "/api/status":
                return self._send(200, remember("status", get_status()))
            if url.path == "/api/workspaces":
                return self._send(200, remember("workspaces", list_workspaces()))
            if url.path == "/api/allowlist":
                return self._send(200, {"domains": list_allowlist(), "logs": allowlist_logs()})
            if url.path == "/api/projects":
                return self._send(200, remember("projects", list_projects()))
            if url.path == "/api/secrets":
                return self._send(200, remember("secrets", secrets_with_pending(q["project"]), q["project"]))
            return self._send(404, {"error": "not found"})
        except CLIError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_POST(self):
        if not self._allowed() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if url.path == "/api/quit":
                self._send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown).start()
                return
            if url.path.startswith("/api/do/"):
                result = handle(url.path[len("/api/do/"):], payload)
                return self._send(200, {"ok": True, "result": result})
            return self._send(404, {"error": "not found"})
        except KeyError as e:
            return self._send(400, {"error": "Missing field: %s" % e})
        except CLIError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})


def prelink_projects():
    """Link every project's folder once, so the first switch to it is instant."""
    try:
        for p in list_projects():
            project_dir(p["name"])
    except Exception as e:  # noqa: BLE001
        print("prelink:", e)


def main():
    os.makedirs(PROJECTS_DIR, mode=0o700, exist_ok=True)
    threading.Thread(target=prelink_projects, daemon=True).start()
    url_file = os.path.join(DATA_DIR, "url")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # Already running (e.g. the app was opened twice): just show it again.
        if os.path.exists(url_file) and "--no-browser" not in sys.argv:
            with open(url_file) as f:
                webbrowser.open(f.read().strip())
            sys.exit(0)
        print("Port %d is busy. Set AGENTSECRETS_GUI_PORT to use another port." % PORT)
        sys.exit(1)
    url = "http://127.0.0.1:%d/" % PORT
    fd = os.open(url_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, url.encode())
    os.close(fd)
    print("AgentSecrets Manager running at", url)
    print("Press Ctrl+C to stop.")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
