from ele_trading.optimization.two_stage_cvar import build_two_stage_cvar_model


def test_two_stage_skeleton_can_build():
    model = build_two_stage_cvar_model(
        T=list(range(4)),
        OMEGA=['low', 'base', 'high'],
        p_omega={'low': 0.2, 'base': 0.5, 'high': 0.3},
    )

    assert len(model.T) == 4
    assert len(model.OMEGA) == 3
    assert len(model.cvar_cons) == 3
