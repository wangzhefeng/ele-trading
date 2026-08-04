"""Inactive user-side wind/PV/BESS dispatch archive (用户侧风光储调度模块).

该模块合并了原 ``data_provider.todo``(样例构建)与 ``optimization.todo``
(调度求解器 + 契约),统一为 ``ele_trading.user_side_dispatch``。归档状态
不变:活动 v2 API 不导出本包,消费方必须显式 import
``ele_trading.user_side_dispatch`` 路径。

CVXPY-backed APIs are loaded only when their specific archived name is
requested, so importing the archive or a PuLP-backed archived module remains
possible without the optional dependency.
"""

from importlib import import_module

from .interfaces import *  # noqa: F401,F403
from .landed_price import (
    LandedPrice,
    PriceMode,
    TariffSchedule,
    TariffVersion,
    build_landed_price,
    load_tariff_schedule,
)
from .adapters.dispatch_adapters import (
    run_user_side_pv_dispatch,
    run_user_side_pv_bess_dispatch,
    run_user_side_wind_dispatch,
    run_user_side_wind_bess_dispatch,
    run_user_side_wind_pv_bess_dispatch,
)
from .algorithms.user_side_bess_dispatch_pulp import run_user_side_bess_dispatch
from .algorithms.user_side_renewable_bess_dispatch_class import (
    run_user_side_renewable_bess_dispatch,
)
from .algorithms.user_side_renewable_dispatch_class import (
    run_user_side_renewable_dispatch,
)
from .user_side_bess_sample import (
    build_synthetic_user_side_bess_dispatch_frame,
    build_user_side_bess_dispatch_input,
)
from .user_side_pv_bess_dispatch_sample import (
    build_synthetic_user_side_pv_bess_dispatch_frame,
    build_user_side_pv_bess_dispatch_input,
)
from .user_side_pv_dispatch_sample import (
    build_synthetic_user_side_pv_dispatch_frame,
    build_user_side_pv_dispatch_input,
)


_LAZY_CVXPY_EXPORTS = {
    "CVXP_PROFILES": (".algorithms.user_side_bess_dispatch_cvxpy", "CVXP_PROFILES"),
    "CvxpBESSDispatcher": (".algorithms.user_side_bess_dispatch_cvxpy", "CvxpBESSDispatcher"),
    "get_cvxp_profile": (".algorithms.user_side_bess_dispatch_cvxpy", "get_cvxp_profile"),
    "run_cvxp_bess_dispatch": (".algorithms.user_side_bess_dispatch_cvxpy", "run_cvxp_bess_dispatch"),
    "build_synthetic_cvxp_dispatch_frame": (".cvxp_bess_sample", "build_synthetic_cvxp_dispatch_frame"),
    "build_cvxp_bess_dispatch_input": (".cvxp_bess_sample", "build_cvxp_bess_dispatch_input"),
    "DistributedBESSDispatcher": (
        ".algorithms.user_side_bess_distributed_dispatch_class",
        "DistributedBESSDispatcher",
    ),
    "run_distributed_bess_dispatch": (
        ".algorithms.user_side_bess_distributed_dispatch_class",
        "run_distributed_bess_dispatch",
    ),
    "DistributedRenewableBESSDispatcher": (
        ".algorithms.user_side_renewable_bess_distributed_dispatch_class",
        "DistributedRenewableBESSDispatcher",
    ),
    "run_user_side_renewable_bess_distributed_dispatch": (
        ".algorithms.user_side_renewable_bess_distributed_dispatch_class",
        "run_user_side_renewable_bess_distributed_dispatch",
    ),
    "run_user_side_pv_bess_distributed_dispatch": (
        ".adapters.distributed_dispatch_adapters",
        "run_user_side_pv_bess_distributed_dispatch",
    ),
    "run_user_side_wind_bess_distributed_dispatch": (
        ".adapters.distributed_dispatch_adapters",
        "run_user_side_wind_bess_distributed_dispatch",
    ),
    "run_user_side_wind_pv_bess_distributed_dispatch": (
        ".adapters.distributed_dispatch_adapters",
        "run_user_side_wind_pv_bess_distributed_dispatch",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attribute_name = _LAZY_CVXPY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from exc
    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
