"""Prime's permission controller: decides, in code, how much approval each action needs.

The model only *requests* actions; this module (not the model) classifies them:

  A  routine       runs automatically (looking, searching, opening apps, its own memory)
  B  consequential  shown to the user first; "allow once" or "allow for this task"
  C  sensitive      explicit confirmation by click every single time; voice alone never counts

Every decision is appended to a daily activity log in Prime's memory folder.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import paths
from core.redact import redact

LOG_DIR = paths.DATA / "logs"
MEMORY_DIR = paths.lower_slash(paths.DATA) + "/"
VAULT_DIR = paths.lower_slash(paths.DATA / "vault")

A_TOOLS = {
    "Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "ToolSearch",
    "mcp__jarvis__screenshot", "mcp__jarvis__list_windows", "mcp__jarvis__list_apps",
    "mcp__jarvis__open_app", "mcp__jarvis__list_accounts", "mcp__jarvis__list_secrets",
    "mcp__jarvis__save_secret", "mcp__jarvis__look", "mcp__jarvis__watch_for", "mcp__jarvis__watch_screen", "mcp__jarvis__stop_watching",
    "mcp__jarvis__record_video", "mcp__jarvis__stop_recording",
    "mcp__jarvis__set_reminder", "mcp__jarvis__list_reminders", "mcp__jarvis__cancel_reminder",
}
C_TOOLS = {"mcp__jarvis__delete_secret"}

# Shell commands that delete data, weaken security, or change the system: always category C.
SENSITIVE_COMMANDS = [
    (r"\b(remove-item|rm|rmdir|del|erase|rd)\b", "deletes files"),
    (r"\bclear-recyclebin\b|\bcipher\s+/w\b", "destroys deleted data"),
    (r"\b(format-volume|format\s+[a-z]:|diskpart|clear-disk|initialize-disk)\b", "formats or repartitions disks"),
    (r"\b(bcdedit|bcdboot)\b", "changes boot settings"),
    (r"\breg(\.exe)?\s+(delete|add|import)\b|\b(remove-itemproperty|set-itemproperty|new-itemproperty)\b.*hk(lm|cu)",
     "changes the registry"),
    (r"\bset-mppreference\b|\badd-mppreference\b|\bwindefend\b|\bdefender\b", "changes antivirus settings"),
    (r"\bnetsh\s+(advfirewall|firewall)\b|\bset-netfirewall", "changes the firewall"),
    (r"\bset-executionpolicy\b", "changes script security policy"),
    (r"\b(new-localuser|add-localgroupmember|net\s+user|net\s+localgroup|set-localuser|remove-localuser)\b",
     "changes user accounts"),
    (r"\b(icacls|takeown|set-acl)\b", "changes file permissions"),
    (r"\b(stop-computer|restart-computer|shutdown(\.exe)?\s)", "shuts down or restarts"),
    (r"\bvssadmin\b|\bwbadmin\b", "touches backups or shadow copies"),
    (r"(invoke-webrequest|iwr|curl|wget|invoke-restmethod|irm)\b.*\|\s*(iex|invoke-expression)", "runs code downloaded from the internet"),
    (r"\b(uninstall|winget\s+uninstall|remove-appxpackage)\b", "uninstalls software"),
    # macOS
    (r"\bsudo\b|\bosascript\b.*with administrator privileges", "runs as administrator"),
    (r"\bdiskutil\s+(erase\w*|partition\w*|zerodisk|secureerase|apfs\s+delete\w*|reformat)\b|\bnewfs_|\basr\s+restore\b",
     "formats or repartitions disks"),
    (r"\b(shutdown|reboot|halt)\b|\bosascript\b.*\b(shut down|restart|log out)\b", "shuts down or restarts"),
    (r"\bcsrutil\b|\bspctl\b.*(--master-disable|--disable)|\bnvram\b|\bbless\b", "weakens system security or boot settings"),
    (r"\b(dscl|sysadminctl|dseditgroup)\b", "changes user accounts"),
    (r"\b(chmod|chown|chflags)\b|\bxattr\s+-[a-z]*d", "changes file permissions"),
    (r"\btmutil\s+(delete\w*|disable|thinlocalsnapshots)\b", "touches Time Machine backups"),
    (r"\b(srm|shred)\b|\bfind\b.*\s-delete\b|\bunlink\b|\bemptytrash\b|empty the trash", "deletes files"),
    (r"(curl|wget)\b.*\|\s*(sudo\s+)?(sh|bash|zsh|python3?)\b", "runs code downloaded from the internet"),
    (r"\bbrew\s+(uninstall|remove|rm)\b|\bpkgutil\s+--forget\b", "uninstalls software"),
    (r"\blaunchctl\s+(unload|bootout|disable|remove)\b", "changes background services"),
    (r"\bsocketfilterfw\b|\bpfctl\b", "changes the firewall"),
    (r"\bsecurity\s+(delete-\w+|dump-keychain|find-\w+-password\b.*\s-w\b|set-keychain-settings)", "touches the Keychain"),
    (r"\btccutil\s+reset\b|\bdefaults\s+delete\b", "resets system or app settings"),
]
SYSTEM_PATHS = ("c:/windows", "c:/program files", "c:/programdata",
                "/system/", "/library/", "/usr/", "/bin/", "/sbin/", "/etc/", "/private/", "/applications/")


def _norm_path(p):
    return (p or "").replace("\\", "/").lower()


def classify(tool, inp):
    """(category, reason) for one requested action."""
    if tool in A_TOOLS:
        return "A", "routine"
    if tool in C_TOOLS:
        return "C", "deletes a saved password"

    if tool in ("PowerShell", "Bash"):
        cmd = (inp.get("command") or "").lower()
        for pattern, why in SENSITIVE_COMMANDS:
            if re.search(pattern, cmd):
                return "C", why
        return "B", "runs a command"

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = _norm_path(inp.get("file_path") or inp.get("notebook_path"))
        if path.startswith(VAULT_DIR):
            return "C", "touches the password vault"
        if path.startswith(SYSTEM_PATHS):
            return "C", "changes a system file"
        if path.startswith(MEMORY_DIR):
            return "A", "Prime's own memory"
        return "B", "changes a file"

    if tool in ("mcp__jarvis__fill_login", "mcp__jarvis__type_email_code", "mcp__jarvis__type_secret"):
        return "B", "signs in to an account"
    if tool.startswith("mcp__jarvis__"):
        return "B", "controls the mouse or keyboard"
    return "B", "unfamiliar tool"


_store = None  # memory.store.Store once the app has opened the database


def use_store(store):
    global _store
    _store = store


def log(tool, category, decision, summary):
    """Record one decision in the activity log, with likely secrets masked."""
    summary = redact(summary)[:300]
    if _store is not None:
        _store.log_activity(tool, category, decision, summary)
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)  # no database (console mode): daily JSONL file
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "tool": tool,
             "category": category, "decision": decision, "summary": summary}
    with open(LOG_DIR / f"activity-{datetime.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def recent(limit=200):
    """The latest activity entries, newest last, as {time, tool, category, decision, summary}."""
    if _store is not None:
        return [{"time": r["ts"], **{k: r[k] for k in ("tool", "category", "decision", "summary")}}
                for r in _store.recent_activity(limit)]
    files = sorted(LOG_DIR.glob("activity-*.jsonl"))[-3:] if LOG_DIR.exists() else []
    lines = []
    for f in files:
        lines += f.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines[-limit:] if l.strip()]
