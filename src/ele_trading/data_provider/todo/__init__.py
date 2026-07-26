"""Inactive user-side sample-data archive.

CVXPY sample builders remain available through lazy attribute access so a
normal archive import does not require the optional CVXPY dependency.
"""

from importlib import import_module

from .user_side_bess_sample import build_synthetic_user_side_bess_dispatch_frame, build_user_side_bess_dispatch_input
from .user_side_pv_bess_dispatch_sample import build_synthetic_user_side_pv_bess_dispatch_frame, build_user_side_pv_bess_dispatch_input
from .user_side_pv_dispatch_sample import build_synthetic_user_side_pv_dispatch_frame, build_user_side_pv_dispatch_input


_LAZY_CVXPY_EXPORTS = {
    "build_synthetic_cvxp_dispatch_frame",
    "build_cvxp_bess_dispatch_input",
}


def __getattr__(name: str):
    if name in _LAZY_CVXPY_EXPORTS:
        module = import_module(".cvxp_bess_sample", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
