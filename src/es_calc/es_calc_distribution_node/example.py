"""分布式储能测算统一模块使用示例。

使用 data/profit_calc/dist_es/ 下的测试数据，演示如何通过预设运行各版本算法。

用法:
    python -m es_calc_distribution_node.example --preset v4 --system park
    python -m es_calc_distribution_node.example --preset v1 --system 338
    python -m es_calc_distribution_node.example --preset v5 --system park --search-mode full_grid --workers 4
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .optimizer import run_systems


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分布式储能测算统一模块")
    parser.add_argument("--preset", choices=["v1", "v2", "v3", "v4", "v5"], default="v4",
                        help="算法预设版本")
    parser.add_argument("--system", choices=["338", "342", "park", "all"], default="park",
                        help="系统名称")
    parser.add_argument("--search-mode", choices=["max_capacity", "coordinate", "full_grid"],
                        default="coordinate", help="搜索模式")
    parser.add_argument("--workers", type=int, default=1, help="full_grid 并行进程数")
    parser.add_argument("--min-cabinets-per-transformer", type=int, default=1,
                        help="每台变压器最小柜数")
    parser.add_argument("--start-year", type=int, default=2025, help="起始年份")
    parser.add_argument("--start-month", type=int, default=1, help="起始月份")
    parser.add_argument("--end-year", type=int, default=2026, help="结束年份")
    parser.add_argument("--end-month", type=int, default=1, help="结束月份")
    parser.add_argument("--max-demand-price", type=float, default=33.8, help="需量电费单价")
    parser.add_argument("--freq-minutes", type=int, default=15, help="时间分辨率(分钟)")
    parser.add_argument("--data-dir", type=str, default="data/profit_calc/dist_es",
                        help="数据目录路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    start_time = datetime(args.start_year, args.start_month, 1)
    end_time = datetime(args.end_year, args.end_month, 1)
    base_dir = Path(args.data_dir)

    print(f"preset={args.preset} system={args.system} search_mode={args.search_mode}")
    print(f"data_dir={base_dir} time={start_time} ~ {end_time}")

    results = run_systems(
        base_dir=base_dir,
        opt_result_dir=base_dir / "opt_result",
        start_time=start_time,
        end_time=end_time,
        max_demand_price=args.max_demand_price,
        freq_minutes=args.freq_minutes,
        search_mode=args.search_mode,
        system_name=args.system,
        workers=args.workers,
        min_cabinets_per_transformer=args.min_cabinets_per_transformer,
        preset=args.preset,
    )
    for name, result in results.items():
        print(f"\nsystem={name}")
        print(result.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
