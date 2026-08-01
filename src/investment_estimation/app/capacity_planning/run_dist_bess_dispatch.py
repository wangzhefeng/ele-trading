"""分布式储能容量搜索运行脚本

从 configs/capacity_planning/dist_bess_dispatch.yaml 加载参数，
运行分布式储能容量搜索算法。

用法:
    python app/capacity_planning/run_dist_bess_dispatch.py
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_ROOT = Path(__file__).resolve().parents[2]

from investment_estimation.todo import DistBESSDispatchInput
from investment_estimation.todo import run_dist_bess_dispatch
from investment_estimation.utils.io import read_yaml
from investment_estimation.utils.log_util import logger

CONFIG_PATH = PACKAGE_ROOT / 'configs' / 'capacity_planning' / 'dist_bess_dispatch.yaml'


def main() -> None:
    cfg = read_yaml(CONFIG_PATH)

    base_dir = PROJECT_ROOT / cfg['data']['base_dir']
    start_time = datetime.fromisoformat(cfg['time_range']['start_time'])
    end_time = datetime.fromisoformat(cfg['time_range']['end_time'])
    dispatch = cfg['dispatch']
    search = cfg['search']

    input_data = DistBESSDispatchInput(
        base_dir=str(base_dir),
        start_time=start_time,
        end_time=end_time,
        max_demand_price=dispatch['max_demand_price'],
        freq_minutes=dispatch['freq_minutes'],
        preset=dispatch['preset'],
        system_name=dispatch['system_name'],
        search_mode=dispatch['search_mode'],
        workers=search['workers'],
        min_cabinets_per_transformer=search['min_cabinets_per_transformer'],
    )

    logger.info(
        f"开始分布式储能容量搜索: preset={input_data.preset} "
        f"system={input_data.system_name} search_mode={input_data.search_mode}"
    )
    result = run_dist_bess_dispatch(input_data)

    logger.info(f"搜索完成: 最优组合={result.best_combo_key}")
    logger.info(f"  收益={result.best_revenue:.2f} 元")
    logger.info(f"  总柜数={result.best_total_cabinets} 总容量={result.best_total_capacity_kwh:.0f} kWh")
    logger.info(f"  输出目录={result.output_dir}")

    print("\n=== 容量搜索结果 ===")
    print(result.summary.to_string(index=False))


if __name__ == '__main__':
    main()
