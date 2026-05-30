from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.data_provider import (
    build_synthetic_user_side_bess_dispatch_frame,
    build_user_side_bess_dispatch_input,
    load_user_side_bess_dispatch_config,
)
from ele_trading.optimization.user_side_bess_dispatch import (
    run_user_side_bess_dispatch,
)
from ele_trading.utils.log_util import logger


CONFIG_PATH = PROJECT_ROOT / 'configs' / 'user_side_bess_dispatch.yaml'


if __name__ == "__main__":
    config = load_user_side_bess_dispatch_config(CONFIG_PATH)
    input_frame = build_synthetic_user_side_bess_dispatch_frame(config)
    dispatch_input = build_user_side_bess_dispatch_input(config)
    result = run_user_side_bess_dispatch(dispatch_input)

    result_df = pd.DataFrame(
        {
            "timestamp": input_frame["timestamp"],
            "load_forecast": input_frame["load_forecast"],
            "buy_price": input_frame["buy_price"],
            "price_type": input_frame["price_type"],
            "charge_power": result.charge_power,
            "discharge_power": result.discharge_power,
            "soc": result.soc,
            "grid_import": result.grid_import,
        }
    )

    logger.info("=== 用户侧储能调度 demo ===")
    logger.info(f"config_path={CONFIG_PATH}")
    logger.info(f"energy_cost={result.energy_cost:.4f}")
    logger.info(f"demand_cost={result.demand_cost:.4f}")
    logger.info(f"total_cost={result.total_cost:.4f}")
    logger.info(f"max_grid_import={result.max_grid_import:.4f}")
    logger.info(f"constraint_violations={result.constraint_violations}")
    logger.info("=== 用户侧储能调度逐时结果 ===")
    logger.info(f"\n{result_df.to_string(index=False)}")
