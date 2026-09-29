import json
import importlib.resources
from shared.models.domain_policy import DomainPolicy


def load_seed_domain_policies() -> list[DomainPolicy]:
    """
    Carga las politicas de dominio semilla empaquetadas en la libreria shared
    utilizando importlib.resources estandar de Python.
    """
    try:
        resource_file = importlib.resources.files("shared.config").joinpath(
            "seed_domain_policies.json"
        )
        with resource_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return [DomainPolicy(**item) for item in data.get("policies", [])]
    except Exception:
        return [
            DomainPolicy(
                domain="default",
                hard_rate_limit=None,
                default_rate_limit=None,
                default_use_proxy=True,
                default_respect_robots_txt=True,
                description="Default Fallback",
            ),
        ]
