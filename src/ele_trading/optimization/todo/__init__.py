"""Inactive user-side optimization archive; import it explicitly.

CVXPY-backed APIs are loaded only when their specific archived name is
requested, so importing the archive or a PuLP-backed archived module remains
possible without the optional dependency.
"""

from importlib import import_module

from .interfaces import *  # noqa: F401,F403
from .user_side_bess_dispatch import run_user_side_bess_dispatch
from .user_side_pv_bess_dispatch import run_user_side_pv_bess_dispatch
from .user_side_pv_dispatch import run_user_side_pv_dispatch
from .user_side_renewable_bess_dispatch_class import run_user_side_renewable_bess_dispatch
from .user_side_renewable_dispatch_class import run_user_side_renewable_dispatch
from .user_side_wind_bess_dispatch import run_user_side_wind_bess_dispatch
from .user_side_wind_dispatch import run_user_side_wind_dispatch
from .user_side_wind_pv_bess_dispatch import run_user_side_wind_pv_bess_dispatch


_LAZY_CVXPY_EXPORTS = {
    "CVXP_PROFILES": (".user_side_bess_dispatch_cvxpy", "CVXP_PROFILES"),
    "CvxpBESSDispatcher": (".user_side_bess_dispatch_cvxpy", "CvxpBESSDispatcher"),
    "get_cvxp_profile": (".user_side_bess_dispatch_cvxpy", "get_cvxp_profile"),
    "run_cvxp_bess_dispatch": (".user_side_bess_dispatch_cvxpy", "run_cvxp_bess_dispatch"),
    "DistributedBESSDispatcher": (
        ".user_side_bess_distributed_dispatch_class",
        "DistributedBESSDispatcher",
    ),
    "run_distributed_bess_dispatch": (
        ".user_side_bess_distributed_dispatch_class",
        "run_distributed_bess_dispatch",
    ),
    "DistributedRenewableBESSDispatcher": (
        ".user_side_renewable_bess_distributed_dispatch_class",
        "DistributedRenewableBESSDispatcher",
    ),
    "run_user_side_renewable_bess_distributed_dispatch": (
        ".user_side_renewable_bess_distributed_dispatch_class",
        "run_user_side_renewable_bess_distributed_dispatch",
    ),
    "run_user_side_pv_bess_distributed_dispatch": (
        ".user_side_pv_bess_distributed_dispatch",
        "run_user_side_pv_bess_distributed_dispatch",
    ),
    "run_user_side_wind_bess_distributed_dispatch": (
        ".user_side_wind_bess_distributed_dispatch",
        "run_user_side_wind_bess_distributed_dispatch",
    ),
    "run_user_side_wind_pv_bess_distributed_dispatch": (
        ".user_side_wind_pv_bess_distributed_dispatch",
        "run_user_side_wind_pv_bess_distributed_dispatch",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attribute_name = _LAZY_CVXPY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
