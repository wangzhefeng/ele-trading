from __future__ import annotations

from pyomo.environ import ConcreteModel, Constraint, NonNegativeReals, Objective, Reals, Set, Var, maximize


def build_two_stage_cvar_model(T, OMEGA, p_omega, alpha=0.95, lam=1.0):
    """构建 Two-stage + CVaR 建模骨架。

    注意：当前只提供结构模板，不包含完整收益项和物理约束。
    """
    m = ConcreteModel()

    m.T = Set(initialize=T)
    m.OMEGA = Set(initialize=OMEGA)

    # 第一阶段变量：日前申报或资源承诺。
    m.q = Var(m.T, domain=NonNegativeReals)

    # 第二阶段变量：不同场景下的充放电、SOC 与偏差量。
    m.p_ch = Var(m.T, m.OMEGA, domain=NonNegativeReals)
    m.p_dis = Var(m.T, m.OMEGA, domain=NonNegativeReals)
    m.soc = Var(m.T, m.OMEGA, domain=NonNegativeReals)
    m.dev_pos = Var(m.T, m.OMEGA, domain=NonNegativeReals)
    m.dev_neg = Var(m.T, m.OMEGA, domain=NonNegativeReals)

    # 每个场景的收益变量，用于后续拼接完整收益表达式。
    m.R = Var(m.OMEGA)

    # CVaR 变量：eta 是分位阈值，z[w] 表示超损失部分。
    m.eta = Var(domain=Reals)
    m.z = Var(m.OMEGA, domain=NonNegativeReals)

    def cvar_rule(mm, w):
        return mm.z[w] >= mm.eta - mm.R[w]

    m.cvar_cons = Constraint(m.OMEGA, rule=cvar_rule)

    m.obj = Objective(
        expr=sum(p_omega[w] * m.R[w] for w in m.OMEGA)
        - lam * (m.eta + 1.0 / (1 - alpha) * sum(p_omega[w] * m.z[w] for w in m.OMEGA)),
        sense=maximize,
    )

    return m
