from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.optimization.two_stage_cvar import build_two_stage_cvar_model


if __name__ == '__main__':
    T = list(range(4))
    OMEGA = ['low', 'base', 'high']
    p_omega = {'low': 0.2, 'base': 0.5, 'high': 0.3}
    model = build_two_stage_cvar_model(T=T, OMEGA=OMEGA, p_omega=p_omega)
    print('=== Two-stage + CVaR 模型骨架 ===')
    print(f'T size={len(list(model.T.data()))}')
    print(f'OMEGA size={len(list(model.OMEGA.data()))}')
    print(f'q vars={len(model.q)}')
    print(f'p_ch vars={len(model.p_ch)}')
    print(f'cvar constraints={len(model.cvar_cons)}')
