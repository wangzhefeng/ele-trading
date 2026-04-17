from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.evaluation.backtest import run_simple_backtest


if __name__ == '__main__':
    metrics = run_simple_backtest(horizon=4)
    print('=== 最小回测结果 ===')
    for key, value in metrics.items():
        print(f'{key}: {value:.4f}')
