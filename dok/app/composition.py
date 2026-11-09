"""Bind provider implementations at the application edge, never in feature views."""
from ..connections.registry import ConnectionSpec
from ..integrations.google.auth import GoogleAuth
from ..integrations.google.calendar import GoogleConnection


def connections(config, directory):
    return tuple(ConnectionSpec(account.id, GoogleAuth.PROVIDER, account.enabled, (account, str(directory)),
        lambda account=account: GoogleConnection(directory, account)) for account in config.google)
