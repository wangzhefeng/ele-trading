from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.user_side_dispatch.cvxp_bess_sample import (
    build_synthetic_cvxp_dispatch_frame,
    build_cvxp_bess_dispatch_input,
)
from ele_trading.user_side_dispatch.algorithms.user_side_bess_dispatch_cvxpy import (
    run_cvxp_bess_dispatch,
)
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger


CONFIG_PATH = PROJECT_ROOT / 'configs' / 'user_side_dispatch' / 'cvxp_bess_dispatch.yaml'


if __name__ == "__main__":
    # config
    config = read_yaml(CONFIG_PATH)
    # input
    input_frame = build_synthetic_cvxp_dispatch_frame(config)
    # dispatch input
    dispatch_input = build_cvxp_bess_dispatch_input(config)
    # model
    result = run_cvxp_bess_dispatch(dispatch_input)
    # model result
    result_df = pd.DataFrame(
        {
            "timestamp": input_frame["timestamp"],
            "demand_load": input_frame["demand_load"],
            "ele_prices": input_frame["ele_prices"],
            "ele_types": input_frame["ele_types"],
            "charge_power": result.charge_power,
            "discharge_power": result.discharge_power,
            "net_power": result.net_power,
            "soc": result.soc,
        }
    )

    logger.info("=== CVXPY 储能调度 demo ===")
    logger.info(f"config_path={CONFIG_PATH}")
    logger.info(f"version={config['dispatch'].get('version', 'optim')}")
    logger.info(f"objective_value={result.objective_value:.4f}")
    logger.info("=== CVXPY 储能调度逐时结果 ===")
    logger.info(f"\n{result_df.to_string(index=False)}")
