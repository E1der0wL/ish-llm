"""Explicit account setup outside the running prompt: python -m dok.connections."""
import argparse
import sys

from ..integrations.google.auth import GoogleAuth
from ..settings.store import ConfigStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage dok external account connections")
    parser.add_argument("action", choices=("login", "logout", "status"))
    parser.add_argument("--config", required=True, help="Path to your existing dok config.jsonc")
    parser.add_argument("--account", required=True, help="Connection ID declared in connections.google")
    args = parser.parse_args(argv)
    store = ConfigStore(args.config)
    account = store.load().connections.account(args.account)
    if account is None:
        parser.error("Account ID is not configured")
    auth = GoogleAuth(store.path.parent, account)
    try:
        if args.action == "login":
            auth.login()
            print("Connected. Enable this account and calendar source in config.jsonc, then reload dok or press F5 in Calendar.")
        elif args.action == "logout":
            auth.disconnect()
            print("Local credentials removed. Reload dok or press F5 to clear active cached reads. Google account grants are unchanged.")
        else:
            print("Credential present (not verified online)" if auth.connected else "Not connected")
    except Exception as exc:
        # OAuth errors may include tokens, request URLs or authorization codes.
        print(f"Connection operation failed ({type(exc).__name__}). Check the OAuth client and permissions.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
