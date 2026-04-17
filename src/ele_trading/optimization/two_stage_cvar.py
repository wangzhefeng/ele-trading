from __future__ import annotations

from pyomo.environ import (
    ConcreteModel, Constraint, NonNegativeReals, Objective,
    Reals, Set, Var, maximize,
)


def build_two_stage_cvar_model(
    T,
    OMEGA,
    p_omega: dict,
    pi_da: dict,
    pi_rt: dict,
    soc0: float,
    soc_min: float,
    soc_max: float,
    p_ch_max: float,
    p_dis_max: float,
    eta_ch: float,
    eta_dis: float,
    deg_cost: float,
    dt: float = 1.0,
    kappa_pos: float = 0.25,
    kappa_neg: float = 0.25,
    alpha: float = 0.95,
    lam: float = 1.0,
):
    """构建完整 Two-stage + CVaR 模型（含收益等式约束和储能物理约束）。"""
    m = ConcreteModel()

    m.T = Set(initialize=T)
    m.OMEGA = Set(initialize=OMEGA)

    # 第一阶段：日前申报量，上界为最大放电功率（物理容量限制）
    m.q = Var(m.T, domain=NonNegativeReals, bounds=(0, p_dis_max))

    # 第二阶段：各场景下的充放电、SOC 和偏差量
    m.p_ch = Var(m.T, m.OMEGA, domain=NonNegativeReals, bounds=(0, p_ch_max))
    m.p_dis = Var(m.T, m.OMEGA, domain=NonNegativeReals, bounds=(0, p_dis_max))
    m.soc = Var(m.T, m.OMEGA, domain=NonNegativeReals, bounds=(soc_min, soc_max))
    m.dev_pos = Var(m.T, m.OMEGA, domain=NonNegativeReals)
    m.dev_neg = Var(m.T, m.OMEGA, domain=NonNegativeReals)

    # 每个场景的收益（等式约束将其绑定到物理量）
    m.R = Var(m.OMEGA, domain=Reals)

    # CVaR 变量
    m.eta = Var(domain=Reals)
    m.z = Var(m.OMEGA, domain=NonNegativeReals)

    # 预排序时段列表，避免每次规则调用时重复排序
    t_list = sorted(T)

    # --- SOC 动态约束 ---
    def soc_dynamics_rule(mm, t, w):
        t_idx = t_list.index(t)
        soc_prev = soc0 if t_idx == 0 else mm.soc[t_list[t_idx - 1], w]
        return mm.soc[t, w] == soc_prev + eta_ch * mm.p_ch[t, w] * dt - mm.p_dis[t, w] / eta_dis * dt

    m.soc_dynamics = Constraint(m.T, m.OMEGA, rule=soc_dynamics_rule)

    # --- 偏差定义约束：实时净出力 - 日前申报 = dev_pos - dev_neg ---
    def deviation_rule(mm, t, w):
        net_output = mm.p_dis[t, w] - mm.p_ch[t, w]
        return net_output - mm.q[t] == mm.dev_pos[t, w] - mm.dev_neg[t, w]

    m.deviation_cons = Constraint(m.T, m.OMEGA, rule=deviation_rule)

    # --- 收益等式约束 ---
    def revenue_rule(mm, w):
        return mm.R[w] == sum(
            (
                pi_da[t] * mm.q[t]
                + pi_rt[(t, w)] * (mm.p_dis[t, w] - mm.p_ch[t, w])
                - kappa_pos * mm.dev_pos[t, w]
                - kappa_neg * mm.dev_neg[t, w]
                - deg_cost * (mm.p_ch[t, w] + mm.p_dis[t, w])
            ) * dt
            for t in mm.T
        )

    m.revenue_cons = Constraint(m.OMEGA, rule=revenue_rule)

    # --- CVaR 约束（Rockafellar & Uryasev 2000）---
    # loss[w] = -R[w]；z[w] >= loss[w] - eta = -R[w] - eta
    # 与 z[w] >= 0 联合，使 eta 收敛到 VaR_α(loss) 的最优值
    def cvar_rule(mm, w):
        return mm.z[w] >= -mm.R[w] - mm.eta

    m.cvar_cons = Constraint(m.OMEGA, rule=cvar_rule)

    # --- 目标函数：最大化期望收益 - λ·CVaR ---
    m.obj = Objective(
        expr=(
            sum(p_omega[w] * m.R[w] for w in m.OMEGA)
            - lam * (m.eta + 1.0 / (1.0 - alpha) * sum(p_omega[w] * m.z[w] for w in m.OMEGA))
        ),
        sense=maximize,
    )

    return m
