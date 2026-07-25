"""Unit tests for Mengxi settlement formulas."""

from __future__ import annotations

import numpy as np
import pytest

from ele_trading.trading.settlement_mengxi import (
    aggregate_to_settle_periods,
    compute_cpen_dayah,
    compute_cpen_long,
    compute_settlement_C,
    compute_settlement_C2,
)


class TestSettlementIdentity:
    """Test that C and C2 are algebraically identical."""

    def test_settlement_identity(self):
        """Random quantity-price sequences must satisfy |ΣC - ΣC2| < 1e-6 * ΣC."""
        rng = np.random.default_rng(42)
        n = 96
        q_long = rng.uniform(0, 10, n)
        p_long = rng.uniform(200, 400, n)
        q_dayah = rng.uniform(0, 10, n)
        p_dayah = rng.uniform(200, 400, n)
        q_real = rng.uniform(0, 10, n)
        p_real = rng.uniform(200, 400, n)

        C = compute_settlement_C(q_long, p_long, q_dayah, p_dayah, q_real, p_real)
        C2 = compute_settlement_C2(q_long, p_long, q_dayah, p_dayah, q_real, p_real)

        sum_C = np.sum(C)
        sum_C2 = np.sum(C2)
        assert abs(sum_C - sum_C2) < 1e-6 * abs(sum_C)


class TestCpenDayah:
    """Test day-ahead deviation penalty branches."""

    def test_over_declaration_favorable_spread(self):
        """Q_dayah > lam_u*Q_real and p_dayah < p_real → penalty."""
        q_dayah = np.array([10.0])
        p_dayah = np.array([300.0])
        q_real = np.array([8.0])
        p_real = np.array([350.0])
        lam_l, lam_u = 0.95, 1.05

        cpen = compute_cpen_dayah(q_dayah, p_dayah, q_real, p_real, lam_l, lam_u)
        expected = (10.0 - 1.05 * 8.0) * (350.0 - 300.0)
        assert cpen[0] == pytest.approx(expected)

    def test_under_declaration_unfavorable_spread(self):
        """Q_dayah < lam_l*Q_real and p_dayah > p_real → penalty."""
        q_dayah = np.array([6.0])
        p_dayah = np.array([400.0])
        q_real = np.array([8.0])
        p_real = np.array([300.0])
        lam_l, lam_u = 0.95, 1.05

        cpen = compute_cpen_dayah(q_dayah, p_dayah, q_real, p_real, lam_l, lam_u)
        expected = (0.95 * 8.0 - 6.0) * (400.0 - 300.0)
        assert cpen[0] == pytest.approx(expected)

    def test_within_band_no_penalty(self):
        """Within band → no penalty regardless of price."""
        q_dayah = np.array([8.0])
        p_dayah = np.array([300.0])
        q_real = np.array([8.0])
        p_real = np.array([350.0])
        lam_l, lam_u = 0.95, 1.05

        cpen = compute_cpen_dayah(q_dayah, p_dayah, q_real, p_real, lam_l, lam_u)
        assert cpen[0] == 0.0

    def test_over_declaration_unfavorable_spread_no_penalty(self):
        """Over-declaration but p_dayah > p_real → no penalty."""
        q_dayah = np.array([10.0])
        p_dayah = np.array([400.0])
        q_real = np.array([8.0])
        p_real = np.array([300.0])
        lam_l, lam_u = 0.95, 1.05

        cpen = compute_cpen_dayah(q_dayah, p_dayah, q_real, p_real, lam_l, lam_u)
        assert cpen[0] == 0.0


class TestCpenLong:
    """Test mid-long-term monthly recovery."""

    def test_over_sign_favorable(self):
        """sign_ratio > lam_u_long and p_long < p_spot → penalty."""
        cpen = compute_cpen_long(
            q_long_month=1000.0,
            p_long_month=300.0,
            q_real_month=800.0,
            p_spot_month=350.0,
            lam_l_long=0.90,
            lam_u_long=1.05,
            m_long=1.2,
        )
        sign_ratio = 1000.0 / 800.0  # 1.25 > 1.05
        expected = 1.2 * (1000.0 - 1.05 * 800.0) * (350.0 - 300.0)
        assert cpen == pytest.approx(expected)

    def test_under_sign_unfavorable(self):
        """sign_ratio < lam_l_long and p_long > p_spot → penalty."""
        cpen = compute_cpen_long(
            q_long_month=500.0,
            p_long_month=400.0,
            q_real_month=800.0,
            p_spot_month=300.0,
            lam_l_long=0.90,
            lam_u_long=1.05,
            m_long=1.2,
        )
        sign_ratio = 500.0 / 800.0  # 0.625 < 0.90
        expected = 1.2 * (0.90 * 800.0 - 500.0) * (400.0 - 300.0)
        assert cpen == pytest.approx(expected)

    def test_within_band_no_penalty(self):
        """Within band → no penalty."""
        cpen = compute_cpen_long(
            q_long_month=800.0,
            p_long_month=300.0,
            q_real_month=800.0,
            p_spot_month=350.0,
            lam_l_long=0.90,
            lam_u_long=1.05,
            m_long=1.2,
        )
        assert cpen == 0.0

    def test_zero_real_month(self):
        """Zero real month → no penalty."""
        cpen = compute_cpen_long(
            q_long_month=1000.0,
            p_long_month=300.0,
            q_real_month=0.0,
            p_spot_month=350.0,
            lam_l_long=0.90,
            lam_u_long=1.05,
            m_long=1.2,
        )
        assert cpen == 0.0


class TestSettlePeriodsAggregation:
    """结算时段折算：96 点决策量聚合到 settle_periods 点后总量守恒（§14.1）。"""

    def test_energy_conserved_96_to_48(self):
        rng = np.random.default_rng(0)
        q = rng.uniform(1, 5, 96)
        out = aggregate_to_settle_periods(q, 48)
        assert len(out) == 48
        assert out.sum() == pytest.approx(q.sum())

    def test_identity_when_96(self):
        q = np.random.default_rng(1).uniform(1, 5, 96)
        out = aggregate_to_settle_periods(q, 96)
        np.testing.assert_allclose(out, q)

    def test_rejects_non_divisor(self):
        q = np.ones(96)
        with pytest.raises(ValueError, match="divisor"):
            aggregate_to_settle_periods(q, 50)
