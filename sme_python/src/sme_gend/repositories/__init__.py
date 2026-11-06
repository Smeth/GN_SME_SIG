from sme_gend.repositories.access_connection import AccessConnection

__all__ = ["AccessConnection", "ListeGeneraleRepository"]


def __getattr__(name: str):
    if name == "ListeGeneraleRepository":
        from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository

        return ListeGeneraleRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
