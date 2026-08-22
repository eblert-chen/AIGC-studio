"""Provider-neutral media generation relay."""

from .auth import ClientCredential, StaticClientAuthenticator

__all__ = ["ClientCredential", "StaticClientAuthenticator", "create_app"]


def __getattr__(name: str):
    """Keep package imports side-effect free for worker entry points."""
    if name == "create_app":
        from .main import create_app

        return create_app
    raise AttributeError(name)
