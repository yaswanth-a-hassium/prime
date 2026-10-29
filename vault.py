"""Prime's vault: logins, other secrets and the 2FA email account, encrypted on this Mac.

The vault file (secrets.dat in Prime's data folder) is encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
Its key is a random 32-byte key kept in your login Keychain (service "Prime vault"), created the first time the
vault is used: only your macOS account, unlocked, can read it, and no key or password is ever written to a file.
Prime (Claude) sees services, account names and usernames, never passwords, secrets or 2FA codes; those are typed
straight into apps by Prime's tools.
"""
import json

import paths

VAULT_FILE = paths.DATA / "vault" / "secrets.dat"
KEYCHAIN_SERVICE = "Prime vault"
KEYCHAIN_ACCOUNT = "vault-key"
TWOFA = ("none", "email", "app", "phone")  # how an account's verification codes arrive

IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "yahoo.com": "imap.mail.yahoo.com", "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
}

_fernet = None


def _cipher():
    """The Fernet cipher, its key fetched from (or, the first time, created in) the macOS Keychain."""
    global _fernet
    if _fernet is None:
        import keyring
        from cryptography.fernet import Fernet
        key = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        if not key:
            if VAULT_FILE.exists():
                raise RuntimeError("The vault's key is missing from the Keychain, so the vault can't be opened. "
                                   "Restore the Keychain item \"Prime vault\" or delete secrets.dat to start over.")
            key = Fernet.generate_key().decode()
            keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key)
        _fernet = Fernet(key.encode())
    return _fernet


def _load():
    if not VAULT_FILE.exists():
        return {"accounts": {}, "secrets": {}, "email": None}
    data = json.loads(_cipher().decrypt(VAULT_FILE.read_bytes()).decode("utf-8"))
    if "accounts" not in data:  # first version stored only {name: secret}
        data = {"accounts": {}, "secrets": data, "email": None}
    return data


def _save(data):
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        VAULT_FILE.parent.chmod(0o700)
    except OSError:
        pass
    blob = _cipher().encrypt(json.dumps(data).encode("utf-8"))
    tmp = VAULT_FILE.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.chmod(0o600)
    tmp.replace(VAULT_FILE)


def _key(name):
    return name.strip().lower()


# ---- logins -----------------------------------------------------------------

def accounts(service=None):
    """Saved logins without their passwords."""
    out = []
    for acc_id, a in sorted(_load()["accounts"].items()):
        if service and _key(service) not in a["service"] and a["service"] not in _key(service):
            continue
        out.append({"id": acc_id, "service": a["service"], "username": a["username"],
                    "usual": a["usual"], "twofa": a["twofa"]})
    return out


def add_account(service, username, password, usual=False, twofa="none"):
    data = _load()
    service = _key(service)
    acc_id = f"{service}:{username.strip().lower()}"
    if usual or not any(a["service"] == service for a in data["accounts"].values()):
        usual = True  # the first account for a service is its usual one
        for a in data["accounts"].values():
            if a["service"] == service:
                a["usual"] = False
    data["accounts"][acc_id] = {"service": service, "username": username.strip(), "password": password,
                                "usual": usual, "twofa": twofa if twofa in TWOFA else "none"}
    _save(data)
    return acc_id


def get_account(service, account=None):
    """The login to use: `account` (id or username) if given, else the service's usual one,
    else its only one. None if there's no clear match."""
    matches = [a for a in _load()["accounts"].values()
               if _key(service) in a["service"] or a["service"] in _key(service)]
    if account:
        wanted = _key(account)
        matches = [a for a in matches
                   if wanted in (a["username"].lower(), f"{a['service']}:{a['username'].lower()}")]
    if len(matches) == 1:
        return matches[0]
    usual = [a for a in matches if a["usual"]]
    return usual[0] if usual else None


def delete_account(acc_id):
    data = _load()
    removed = data["accounts"].pop(acc_id, None) is not None
    _save(data)
    return removed


# ---- other secrets ------------------------------------------------------------

def names():
    return sorted(_load()["secrets"])


def set_secret(name, value):
    data = _load()
    data["secrets"][_key(name)] = value
    _save(data)


def get_secret(name):
    return _load()["secrets"].get(_key(name))


def delete_secret(name):
    data = _load()
    removed = data["secrets"].pop(_key(name), None) is not None
    _save(data)
    return removed


# ---- the inbox 2FA codes arrive in -------------------------------------------

def set_email(address, app_password, host=None):
    address = address.strip()
    host = (host or "").strip() or IMAP_HOSTS.get(address.split("@")[-1].lower())
    if not host:
        raise ValueError("Unknown email provider: enter its IMAP server.")
    data = _load()
    data["email"] = {"address": address, "password": app_password, "host": host}
    _save(data)


def get_email():
    return _load()["email"]


def email_info():
    e = get_email()
    return {"address": e["address"], "host": e["host"]} if e else None


def clear_email():
    data = _load()
    data["email"] = None
    _save(data)
