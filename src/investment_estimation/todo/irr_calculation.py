"""
风光储一体化项目 IRR 测算模型
===================================
基于「风光储一体化项目 -乌兰察布V3.xlsx」精确复现。

核心变量参数：
  - wind_capacity:  风电规模 (MW)
  - solar_capacity: 光伏规模 (MW)
  - storage_capacity: 储能规模 (MWh)

输出指标：
  - IRR税前 / IRR税后 / 资本金IRR税前 / 资本金IRR税后
  - 自用累计发电量 / 上网累计发电量 / 总发电量
  - 总成本 / 风度电成本 / 光度电成本 / 度电成本

基准: 乌兰察布V3 (风电200MW + 光伏100MW + 储能180MWh)
测试: python irr_calculation.py (默认参数结果应与乌兰察布V3.xlsx一致)

用法:
  python irr_calculation.py
  python irr_calculation.py --wind 300 --solar 150 --storage 200
  python irr_calculation.py --json
"""

import numpy as np
import argparse


# ==============================================================================
# IRR 计算 (Newton-Raphson, 与Excel IRR函数一致)
# ==============================================================================

def compute_irr(cashflows, guess=0.05, max_iter=1000, tol=1e-8):
    """Newton-Raphson法计算IRR, 返回小数(如0.05=5%)。

    仅返回使 NPV 真正归零的根；若现金流无实数 IRR（如全周期净现流为负、任意利率下
    NPV 恒负），返回 None，避免牛顿法停在 NPV 残差巨大的伪根上被误当作 IRR。
    """
    cf = np.array(cashflows, dtype=np.float64)
    if np.all(cf >= 0) or np.all(cf <= 0):
        return None
    scale = float(np.sum(np.abs(cf)))
    if scale <= 0.0:
        return None
    rate = guess
    t = np.arange(len(cf), dtype=np.float64)
    for _ in range(max_iter):
        if rate <= -0.99 or rate >= 50.0:  # 防溢出边界
            return None
        d = (1 + rate) ** t
        npv = np.sum(cf / d)
        dnpv = np.sum(-t * cf / ((1 + rate) ** (t + 1)))
        if abs(dnpv) < 1e-15:
            break
        step = npv / dnpv
        # 限制步长防止跳入无效区间
        if abs(step) > 0.5:
            step = 0.5 if step > 0 else -0.5
        rate -= step
        if abs(step) < tol:
            break
    # 根有效性校验：step 收敛 ≠ NPV 归零，残差过大（伪根）必须丢弃
    if rate <= -0.99 or rate >= 50.0:
        return None
    npv_final = float(np.sum(cf / (1 + rate) ** t))
    if abs(npv_final) <= 1e-6 * scale:
        return rate
    return None


def irr_robust(cashflows):
    """多初值尝试, 返回最佳IRR；若无实根（所有初值都得不到 NPV≈0 的解）返回 None。"""
    cf = np.array(cashflows, dtype=np.float64)
    if np.all(cf >= 0) or np.all(cf <= 0):
        return None
    scale = float(np.sum(np.abs(cf)))
    if scale <= 0.0:
        return None
    best, best_e = None, float('inf')
    for g in [-0.3, -0.1, 0.0, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30]:
        try:
            r = compute_irr(cf, guess=g)
            if r is not None and -0.5 < r < 1.0:
                t = np.arange(len(cf), dtype=np.float64)
                e = abs(np.sum(cf / ((1 + r) ** t)))
                if e < best_e:
                    best, best_e = r, e
        except Exception:
            pass
    # 防御：最佳候选仍非真实根（残差过大）时判定无解，避免返回伪 IRR
    if best is None or best_e > 1e-6 * scale:
        return None
    return best


# ==============================================================================
# 风光储一体化 IRR 模型 (精确匹配乌兰察布V3.xlsx)
# ==============================================================================

class IRRCalculator:
    """
    风光储一体化项目 IRR 测算模型。

    完全按照Excel「风光储一体化项目-乌兰察布V3.xlsx」的计算逻辑构建。
    默认参数(200,100,180)结果与Excel精确一致。
    """

    # ==== 基准项目参数 (乌兰察布V3, 200MW风电+100MW光伏+180MWh储能) ====

    # 容量
    _B_WIND_MW = 200.0
    _B_SOLAR_MW = 100.0
    _B_STOR_MWH = 180.0
    _B_TOTAL_MW = 300.0

    # 单位造价
    _WIND_UNIT = 3.5       # 元/W
    _SOLAR_UNIT = 3.1      # 元/W
    _STOR_UNIT = 0.54      # 元/Wh
    _DELIVERY = 0.9        # 送出/升压站, 亿元 → 9000万元
    _SURVEY_UNIT = 0.43    # 土地勘察等, 元/W
    _OTHER_UNIT = 0.2      # 其他费用, 元/W

    # 基准投资构成 (万元, 含税)
    _B_WIND_INV = 70000.0
    _B_SOLAR_INV = 31000.0
    _B_STOR_INV = 9720.0
    _B_LAND = 9000.0
    _B_SURVEY = 12900.0
    _B_CONST_INV = 132620.0      # 建设投资 = 以上5项之和
    _B_CONNECTION = 6000.0       # 接入/升压站(单独列支)
    _B_CONST_INT = 1989.3        # 建设期利息
    _B_WORK_CAP = 795.72         # 流动资金
    _B_TOTAL_INV = 135405.02     # 项目总投资

    # 投资原值 (不含税)
    _B_WIND_ORIG = 64220.1834862385
    _B_SOLAR_ORIG = 28440.3669724771
    _B_STOR_ORIG = 8917.43119266055
    _B_LAND_ORIG = 8256.88073394495
    _B_SURVEY_ORIG = 11834.8623853211
    _B_CONN_ORIG = 5504.58715596330   # 6000/1.09

    # 资金筹措基准值
    _B_EQUITY = 27319.72         # 项目资本金
    _B_DEBT = 108085.3           # 债务资金
    _B_LOAN_CONST = 106096.0     # 长期借款(建设投资部分)
    _B_LOAN_INT = 1989.3         # 长期借款(建设期利息部分)

    # 发电参数
    _WIND_HOURS = 2388.335
    _SOLAR_HOURS = 1721.0
    _SYS_EFF = 0.9424

    # 电价 (含税)
    _WIND_PRICE = 0.23
    _SOLAR_PRICE = 0.23
    _VAT_RATE = 0.13

    # 光伏衰减
    _SOLAR_FIRST_DEGR = 0.0
    _SOLAR_ANNUAL_DEGR = 0.004

    # 建设/运营期
    _CONST_YRS = 2
    _OPER_YRS = 20           # Excel中有效运营期为20年(共27列但后5年为零)
    _TOTAL_YRS = 27          # Excel列E-AE共27年
    _CONST_R1 = 0.95
    _CONST_R2 = 0.05

    # 折旧
    _DEPR_YRS = 20

    # 储能电池更换
    _BATT_REPL_YR = 10       # 运营第11年, 0-index=10

    # 贷款
    _LOAN_RATE = 0.03
    _LOAN_TERM = 18

    # 所得税 (三免三减半)
    _TAX_RATE = 0.25
    _TAX_HOLIDAY = 3
    _TAX_HALF = 3

    # 增值税附加
    _VAT_SUR_RATE = 0.10

    # 折现率
    _LCOE_R = 0.03

    # ==== 成本费率 (基于不含税原值) ====
    _MAT_WIND = 15.0     # 风电材料费, 元/KW
    _MAT_SOLAR = 5.0     # 光伏材料费, 元/KW
    _MAT_STOR = 30.0     # 储能换电池材料, 万元/MWh... actually this is 0.3元/Wh * 100 conversion
    # Actually from Excel: storage battery cost = D5 * 0.3 * 100 (万元)
    # = 180 * 0.3 * 100 = 5400 万元

    _LABOR = 300.0       # 人员工资, 万元/年

    # 维修费率 (基于不含税原值)
    _MAINT_WIND_1 = 0.005   # 前10年
    _MAINT_WIND_2 = 0.01    # 11年起
    _MAINT_SOLAR = 0.003
    _MAINT_STOR = 0.01

    # 保险费率 (基于不含税原值)
    _INS_RATE = 0.001

    # 其他费用
    _OTHER_WIND = 30.0    # 元/KW (风电)
    _OTHER_SOLAR = 15.0   # 元/KW (光伏)
    _OTHER_FIXED = 156.0  # 万元 (固定)

    def __init__(self, wind_capacity=200.0, solar_capacity=100.0, storage_capacity=180.0,
                 wind_unit_cost=None, solar_unit_cost=None, storage_unit_cost=None,
                 operating_years=None, construction_years=None,
                 loan_rate=None, loan_term=None,
                 external_revenue=None, external_opex=None,
                 delivery_cost=None, survey_unit_cost=None, other_unit_cost=None,
                 wind_price=None, solar_price=None,
                 wind_hours=None, solar_hours=None, sys_eff=None):
        self.w_mw = float(wind_capacity)
        self.s_mw = float(solar_capacity)
        self.e_mwh = float(storage_capacity)
        self.t_mw = self.w_mw + self.s_mw

        # ---- 可配置参数 ----
        _wind_unit = float(wind_unit_cost) if wind_unit_cost is not None else self._WIND_UNIT
        _solar_unit = float(solar_unit_cost) if solar_unit_cost is not None else self._SOLAR_UNIT
        _storage_unit = float(storage_unit_cost) if storage_unit_cost is not None else self._STOR_UNIT
        _delivery = float(delivery_cost) if delivery_cost is not None else self._DELIVERY
        _survey_unit = float(survey_unit_cost) if survey_unit_cost is not None else self._SURVEY_UNIT
        _other_unit = float(other_unit_cost) if other_unit_cost is not None else self._OTHER_UNIT

        self._oper_years = int(operating_years) if operating_years is not None else self._OPER_YRS
        self._const_years = int(construction_years) if construction_years is not None else self._CONST_YRS
        self._total_years = self._const_years + self._oper_years
        self._loan_rate = float(loan_rate) if loan_rate is not None else self._LOAN_RATE
        self._loan_term = int(loan_term) if loan_term is not None else min(self._LOAN_TERM, self._oper_years)
        self._depr_yrs = min(self._DEPR_YRS, self._oper_years)
        self._wind_hours = float(wind_hours) if wind_hours is not None else self._WIND_HOURS
        self._solar_hours = float(solar_hours) if solar_hours is not None else self._SOLAR_HOURS
        self._sys_eff = float(sys_eff) if sys_eff is not None else self._SYS_EFF
        self._wind_price = float(wind_price) if wind_price is not None else self._WIND_PRICE
        self._solar_price = float(solar_price) if solar_price is not None else self._SOLAR_PRICE

        # ---- 外部收入/成本覆盖 ----
        _ext_rev = None
        if external_revenue is not None:
            if isinstance(external_revenue, (int, float)):
                _ext_rev = np.full(self._oper_years, float(external_revenue), dtype=np.float64)
            else:
                _ext_rev = np.array(external_revenue, dtype=np.float64)
        self._external_revenue = _ext_rev  # 不含税收入, 万元/年

        _ext_opex = None
        if external_opex is not None:
            if isinstance(external_opex, (int, float)):
                _ext_opex = np.full(self._oper_years, float(external_opex), dtype=np.float64)
            else:
                _ext_opex = np.array(external_opex, dtype=np.float64)
        self._external_opex = _ext_opex  # 经营成本, 万元/年

        # ---- 含税投资 (使用可配置单价公式) ----
        # H3 = D6*D3*100, H4 = D7*D4*100, H5 = D8*D5*100
        self.wind_inv = self.w_mw * _wind_unit * 100       # 风电投资 (万元)
        self.solar_inv = self.s_mw * _solar_unit * 100     # 光伏投资 (万元)
        self.stor_inv = self.e_mwh * _storage_unit * 100   # 储能投资 (万元)
        # H6 = D9*10000: 送出工程
        self.land = _delivery * 10000 if _delivery > 0 else 0.0
        # H7 = D10*(D3+D4)*100: 土地勘察按总容量
        self.survey = _survey_unit * self.t_mw * 100 if _survey_unit > 0 else 0.0
        # H8 = SUM(H3:H7)
        self.const_inv = self.wind_inv + self.solar_inv + self.stor_inv + self.land + self.survey
        # H9 = D11*(D3+D4)*100: 接入/升压站按总容量
        self.connection = _other_unit * self.t_mw * 100 if _other_unit > 0 else 0.0

        # 建设期利息 = const_inv * (loan_rate/2)  (半年计息)
        self.const_int = self.const_inv * (self._loan_rate / 2.0)
        # 流动资金 = const_inv * 0.6%
        self.wc = self.const_inv * 0.006
        self.total_inv = self.const_inv + self.const_int + self.wc

        # ---- 不含税原值 (含税/1.09, 建筑业增值税率9%) ----
        _TAX_DEDUCTION = 1.0 + 0.09  # 1.09
        self.wind_orig = self.wind_inv / _TAX_DEDUCTION
        self.solar_orig = self.solar_inv / _TAX_DEDUCTION
        self.stor_orig = self.stor_inv / _TAX_DEDUCTION
        self.land_orig = self.land / _TAX_DEDUCTION if self.land > 0 else 0.0
        self.survey_orig = self.survey / _TAX_DEDUCTION if self.survey > 0 else 0.0
        self.conn_orig = self.connection / _TAX_DEDUCTION if self.connection > 0 else 0.0

        # ---- 资金筹措 ----
        # 长期借款 = const_inv * 80%, 建设期利息全贷款, 流动资金全资本金
        self.loan_const = self.const_inv * 0.80
        self.loan_int_part = self.const_int
        self.total_debt = self.loan_const + self.loan_int_part
        self.equity = self.total_inv - self.total_debt  # = const_inv*0.2 + wc

        # ---- 发电量 (仅当无外部收入时使用) ----
        self.wind_gen_yr = self.w_mw * self._wind_hours * self._sys_eff  # MWh/年
        self.solar_theo = self.s_mw * self._solar_hours
        self.solar_gen0 = self.solar_theo * self._sys_eff

        # ---- 不含税电价 ----
        self.w_price_ex = self._wind_price / (1 + self._VAT_RATE)
        self.s_price_ex = self._solar_price / (1 + self._VAT_RATE)

        # ---- 折旧基数 (不含税原值 + 接入含税, 与Excel完全一致) ----
        self.depr_base = (self.wind_orig + self.solar_orig + self.stor_orig
                          + self.land_orig + self.survey_orig + self.connection)
        self.annual_depr = self.depr_base / self._depr_yrs if self._depr_yrs > 0 else 0.0

        # ---- 储能电池更换 ----
        self.batt_cost = self.e_mwh * 0.3 * 100       # 万元(含税)
        self.batt_cost_ex = self.batt_cost / 1.13      # 不含税
        # 电池更换年份: 运营第 self._BATT_REPL_YR+1 年
        self._batt_repl_idx = min(self._BATT_REPL_YR, self._oper_years - 1) if self._oper_years > 1 else 0
        rem = max(1, self._oper_years - self._batt_repl_idx)
        self.batt_depr = self.batt_cost_ex / min(rem, 12)

        # ---- 年度成本 (人工固定300万, 其他费用固定156万) ----
        self.mat_wind_yr = self.w_mw * self._MAT_WIND / 10.0
        self.mat_solar_yr = self.s_mw * self._MAT_SOLAR / 10.0
        self.labor_yr = self._LABOR      # 固定300万元/年, 不随容量变化
        self.ins_base = self.wind_orig + self.solar_orig + self.stor_orig

        self._res = None

    # ==================================================================
    # 年度数组
    # ==================================================================

    def _gen_arrays(self):
        """发电量"""
        n = self._oper_years
        w = np.full(n, self.wind_gen_yr)
        s = np.array([self.solar_gen0 * max(0, 1 - self._SOLAR_FIRST_DEGR
                                             - self._SOLAR_ANNUAL_DEGR * y)
                      for y in range(n)])
        return w, s, w + s

    def _revenue(self, total_gen):
        """发电收入(不含税, 万元) = 发电量(MWh) * 不含税电价(元/kWh) / 10

        若提供了 external_revenue, 则直接使用外部收入数据。
        """
        if self._external_revenue is not None:
            return self._external_revenue.copy()
        wind_gen, solar_gen, _ = self._gen_arrays()
        wr = wind_gen * self.w_price_ex / 10.0
        sr = solar_gen * self.s_price_ex / 10.0
        return wr + sr

    def _costs(self):
        """全部成本。若提供了 external_opex, 则经营成本使用外部值,
        折旧、摊销、利息仍按财务模型计算。"""
        n = self._oper_years

        if self._external_opex is not None:
            # 外部 OPEX 直接作为经营成本
            op_cost = self._external_opex.copy()
            # 电池更换成本叠加到对应年份
            if 0 <= self._batt_repl_idx < n:
                op_cost[self._batt_repl_idx] += self.batt_cost
            mat = np.zeros(n)
            labor = np.zeros(n)
            maint = np.zeros(n)
            ins = np.zeros(n)
            other = np.zeros(n)
            # 将外部 OPEX 主体归入 other, 电池更换归入 mat (保持 VAT 分类兼容)
            base_opex = self._external_opex.copy()
            other[:] = base_opex
            if 0 <= self._batt_repl_idx < n:
                mat[self._batt_repl_idx] = self.batt_cost
        else:
            # 材料费
            mat = np.full(n, self.mat_wind_yr + self.mat_solar_yr)
            if self._batt_repl_idx < n:
                mat[self._batt_repl_idx] += self.batt_cost

            # 人工
            labor = np.full(n, self.labor_yr)

            # 维修费
            maint = np.zeros(n)
            for i in range(n):
                rw = self._MAINT_WIND_1 if i < 10 else self._MAINT_WIND_2
                maint[i] = (self.wind_orig * rw + self.solar_orig * self._MAINT_SOLAR
                            + self.stor_orig * self._MAINT_STOR)

            # 保险费
            ins = np.full(n, self.ins_base * self._INS_RATE)

            # 其他费用
            other = np.full(n, (self.w_mw * self._OTHER_WIND
                                + self.s_mw * self._OTHER_SOLAR) / 10.0
                            + self._OTHER_FIXED)

            # 经营成本
            op_cost = mat + labor + maint + ins + other

        # 折旧
        depr = np.full(n, self.annual_depr)
        if self._batt_repl_idx < n:
            depr[self._batt_repl_idx:] += self.batt_depr

        # 摊销
        amort = np.zeros(n)

        # 利息
        interest = self._loan_interest()

        # 总成本
        total = op_cost + depr + amort + interest

        return {
            'mat': mat, 'labor': labor, 'maint': maint, 'ins': ins,
            'other': other, 'op_cost': op_cost, 'depr': depr,
            'amort': amort, 'interest': interest, 'total': total,
        }

    def _loan_interest(self):
        """等额本息利息"""
        n = self._oper_years
        arr = np.zeros(n)
        P = self.total_debt
        r = self._loan_rate
        t = min(self._loan_term, n)
        if P <= 0 or r <= 0:
            return arr
        pmt = P * r * (1 + r) ** t / ((1 + r) ** t - 1)
        rem = P
        for i in range(t):
            arr[i] = rem * r
            rem -= (pmt - arr[i])
            rem = max(rem, 0)
        return arr

    def _loan_principal(self):
        """等额本息还本"""
        n = self._oper_years
        arr = np.zeros(n)
        P = self.total_debt
        r = self._loan_rate
        t = min(self._loan_term, n)
        if P <= 0 or r <= 0:
            return arr
        pmt = P * r * (1 + r) ** t / ((1 + r) ** t - 1)
        rem = P
        for i in range(t):
            inte = rem * r
            arr[i] = pmt - inte
            rem -= arr[i]
            rem = max(rem, 0)
        return arr

    # ==================================================================
    # 增值税 (累计法, 与Excel完全一致)
    # ==================================================================

    def _vat_ti(self, revenue, costs):
        """全投资应缴增值税 - 进项不含利息"""
        n = self._oper_years
        out_tax = revenue * self._VAT_RATE
        # 当使用外部 OPEX 时, 简化为统一的 6% 进项税率
        if self._external_opex is not None:
            in_tax = costs['other'] / 1.06 * 0.06
            if np.any(costs['mat'] > 0):
                in_tax = in_tax + costs['mat'] / 1.13 * 0.13
        else:
            in_tax = (costs['mat'] / 1.13 * 0.13
                      + (costs['maint'] + costs['ins'] + costs['other'])
                      / 1.06 * 0.06)
        const_in = ((self.wind_inv - self.wind_orig)
                    + (self.solar_inv - self.solar_orig)
                    + (self.stor_inv - self.stor_orig)
                    + (self.land - self.land_orig)
                    + (self.survey - self.survey_orig))
        vat = np.zeros(n)
        cum_out, cum_in = 0.0, const_in
        for i in range(n):
            cum_out += out_tax[i]
            cum_in += in_tax[i]
            vat[i] = max(0, cum_out - cum_in - np.sum(vat[:i]))
        return vat

    def _vat_eq(self, revenue, costs):
        """资本金应缴增值税 - 进项含利息(财务费用可抵扣)"""
        n = self._oper_years
        out_tax = revenue * self._VAT_RATE
        # 当使用外部 OPEX 时, 简化为统一的 6% 进项税率
        if self._external_opex is not None:
            in_tax = (costs['other'] + costs['interest']) / 1.06 * 0.06
            if np.any(costs['mat'] > 0):
                in_tax = in_tax + costs['mat'] / 1.13 * 0.13
        else:
            in_tax = (costs['mat'] / 1.13 * 0.13
                      + (costs['maint'] + costs['ins'] + costs['other'] + costs['interest'])
                      / 1.06 * 0.06)
        const_in = ((self.wind_inv - self.wind_orig)
                    + (self.solar_inv - self.solar_orig)
                    + (self.stor_inv - self.stor_orig)
                    + (self.land - self.land_orig)
                    + (self.survey - self.survey_orig))
        vat = np.zeros(n)
        cum_out, cum_in = 0.0, const_in
        for i in range(n):
            cum_out += out_tax[i]
            cum_in += in_tax[i]
            vat[i] = max(0, cum_out - cum_in - np.sum(vat[:i]))
        return vat

    # ==================================================================
    # 全投资现金流量 (Excel rows 143-163)
    # ==================================================================

    def _ti_cashflow(self):
        nt = self._total_years
        no = self._oper_years
        nc = self._const_years
        _, _, tgen = self._gen_arrays()
        rev = self._revenue(tgen)
        costs = self._costs()
        vat = self._vat_ti(rev, costs)
        vat_sur = vat * self._VAT_SUR_RATE

        # 现金流入
        cf_in = np.zeros(nt)
        # 运营期: 不含税收入 + 销项税额
        for i in range(no):
            cf_in[nc + i] = rev[i] + rev[i] * self._VAT_RATE
        # 回收流动资金 (最后一年)
        cf_in[nc + no - 1] += self.wc

        # 现金流出
        cf_out = np.zeros(nt)
        if nc >= 2:
            # 2 年建设期: 第 1 年投建设投资*95%+接入, 第 2 年投建设投资*5%+流动资金
            cf_out[0] = self.const_inv * self._CONST_R1 + self.connection
            cf_out[1] = self.const_inv * self._CONST_R2 + self.wc
        else:
            # 0/1 年建设期: 初始投资全部在第 0 年一次性投出(避免漏投建设投资或流动资金)
            cf_out[0] = self.const_inv + self.connection + self.wc
        # 运营期: 经营成本 + 增值税 + 附加
        for i in range(no):
            cf_out[nc + i] = costs['op_cost'][i] + vat[i] + vat_sur[i]

        pre_tax = cf_in - cf_out

        # 所得税: 利润 = 收入 - 利润表总成本 - 税金附加
        # 利润表总成本 = 总成本(含息) - 利息 - 进项税额 - 电池更换支出(资本化)
        profit = np.zeros(no)
        for i in range(no):
            if self._external_opex is not None:
                total_input_tax = costs['other'][i] / 1.06 * 0.06
                if costs['mat'][i] > 0:
                    total_input_tax += costs['mat'][i] / 1.13 * 0.13
            else:
                total_input_tax = (costs['mat'][i] / 1.13 * 0.13
                                   + (costs['maint'][i] + costs['ins'][i] + costs['other'][i])
                                   / 1.06 * 0.06)
            battery_deduction = self.batt_cost if i == self._batt_repl_idx else 0.0
            income_stmt_cost = (costs['total'][i] - costs['interest'][i]
                                - total_input_tax - battery_deduction)
            profit[i] = rev[i] - income_stmt_cost - vat_sur[i]

        inc_tax = np.zeros(nt)
        for i in range(no):
            yr = i + 1
            taxable = max(0, profit[i])
            if yr <= self._TAX_HOLIDAY:
                rate = 0
            elif yr == self._TAX_HOLIDAY + 1:
                rate = 0.15 * 0.5  # 第4年: 15%减半=7.5%
            elif yr <= self._TAX_HOLIDAY + self._TAX_HALF:
                rate = self._TAX_RATE * 0.5
            else:
                rate = self._TAX_RATE
            inc_tax[nc + i] = taxable * rate

        post_tax = pre_tax - inc_tax
        return pre_tax, post_tax, inc_tax, profit

    # ==================================================================
    # 资本金现金流量 (Excel rows 166-190)
    # ==================================================================

    def _equity_cashflow(self):
        nt = self._total_years
        no = self._oper_years
        nc = self._const_years
        _, _, tgen = self._gen_arrays()
        rev = self._revenue(tgen)
        costs = self._costs()
        vat = self._vat_eq(rev, costs)
        vat_sur = vat * self._VAT_SUR_RATE
        prin = self._loan_principal()
        inte = self._loan_interest()

        # 现金流入
        cf_in = np.zeros(nt)
        for i in range(no):
            cf_in[nc + i] = rev[i] + rev[i] * self._VAT_RATE
        cf_in[nc + no - 1] += self.wc

        # 现金流出
        cf_out = np.zeros(nt)
        # 资本金: 按建设投资与贷款的比例拆分到各年
        eq_construction = self.const_inv * 0.20  # 资本金用于建设投资部分
        if nc >= 2:
            cf_out[0] = eq_construction * self._CONST_R1 + self.connection
            cf_out[1] = eq_construction * self._CONST_R2 + self.wc
        else:
            cf_out[0] = eq_construction + self.connection + self.wc
        # 运营期
        for i in range(no):
            cf_out[nc + i] = (prin[i] + inte[i]
                               + costs['op_cost'][i] + vat[i] + vat_sur[i])

        pre_tax = cf_in - cf_out

        # 资本金所得税: 利润表成本 = 总成本 - 进项税(含利息) + 建设期利息摊销 - 电池更换资本化
        const_int_amort = self.const_int / max(1, self._depr_yrs) if self.const_int > 0 else 0.0
        profit = np.zeros(no)
        for i in range(no):
            if self._external_opex is not None:
                eq_input_tax = (costs['other'][i] + costs['interest'][i]) / 1.06 * 0.06
                if costs['mat'][i] > 0:
                    eq_input_tax += costs['mat'][i] / 1.13 * 0.13
            else:
                eq_input_tax = (costs['mat'][i] / 1.13 * 0.13
                                + (costs['maint'][i] + costs['ins'][i] + costs['other'][i]
                                   + costs['interest'][i]) / 1.06 * 0.06)
            battery_deduction = self.batt_cost if i == self._batt_repl_idx else 0.0
            income_stmt_cost = (costs['total'][i] - eq_input_tax
                                + const_int_amort - battery_deduction)
            profit[i] = rev[i] - income_stmt_cost - vat_sur[i]

        inc_tax = np.zeros(nt)
        for i in range(no):
            yr = i + 1
            taxable = max(0, profit[i])
            if yr <= self._TAX_HOLIDAY:
                rate = 0
            elif yr == self._TAX_HOLIDAY + 1:
                rate = 0.15 * 0.5
            elif yr <= self._TAX_HOLIDAY + self._TAX_HALF:
                rate = self._TAX_RATE * 0.5
            else:
                rate = self._TAX_RATE
            inc_tax[nc + i] = taxable * rate

        post_tax = pre_tax - inc_tax
        return pre_tax, post_tax, inc_tax

    # ==================================================================
    # LCOE
    # ==================================================================

    def _lcoe(self):
        """度电成本 = 全生命周期总成本 / 总发电量 (Excel: Z11=AB11/Z8*10, 无折现)"""
        n = self._oper_years
        wg, sg, tg = self._gen_arrays()
        costs = self._costs()
        rw = self.w_mw / self.t_mw if self.t_mw > 0 else 0.5
        rs = self.s_mw / self.t_mw if self.t_mw > 0 else 0.5

        # 共用成本全生命周期合计
        shared_total = (self.stor_inv + self.land + self.survey + self.connection
                        + np.sum(costs['mat'] - self.mat_wind_yr - self.mat_solar_yr)  # 储能材料
                        + np.sum(costs['labor'])
                        + np.sum(np.full(n, self.stor_orig * self._MAINT_STOR))
                        + np.sum(np.full(n, self.stor_orig * self._INS_RATE))
                        + np.sum(np.full(n, self._OTHER_FIXED)))

        # 风电专属成本全生命周期合计
        wind_specific_total = (self.wind_inv
                               + self.mat_wind_yr * n
                               + np.sum(self.wind_orig * np.where(np.arange(n) < 10,
                                                                   self._MAINT_WIND_1,
                                                                   self._MAINT_WIND_2))
                               + self.wind_orig * self._INS_RATE * n
                               + self.w_mw * self._OTHER_WIND / 10.0 * n)

        # 光伏专属成本全生命周期合计
        solar_specific_total = (self.solar_inv
                                + self.mat_solar_yr * n
                                + self.solar_orig * self._MAINT_SOLAR * n
                                + self.solar_orig * self._INS_RATE * n
                                + self.s_mw * self._OTHER_SOLAR / 10.0 * n)

        wind_total = wind_specific_total + shared_total * rw
        solar_total = solar_specific_total + shared_total * rs
        grand_total = wind_total + solar_total
        total_gen = float(np.sum(tg))
        wind_gen_total = float(np.sum(wg))
        solar_gen_total = float(np.sum(sg))

        return {
            'wind': (wind_total / wind_gen_total * 10) if wind_gen_total > 0 else 0,
            'solar': (solar_total / solar_gen_total * 10) if solar_gen_total > 0 else 0,
            'total': (grand_total / total_gen * 10) if total_gen > 0 else 0,
            'total_cost_lifetime': grand_total,
        }

    # ==================================================================
    # 运行
    # ==================================================================

    def run(self):
        _, _, tgen = self._gen_arrays()
        rev = self._revenue(tgen)
        costs = self._costs()
        ti_pre, ti_post, ti_tax, ti_profit = self._ti_cashflow()
        eq_pre, eq_post, eq_tax = self._equity_cashflow()
        lcoe = self._lcoe()

        self._res = {
            'wind_gen': self._gen_arrays()[0],
            'solar_gen': self._gen_arrays()[1],
            'total_gen': tgen,
            'revenue': rev,
            'costs': costs,
            'ti_pre': ti_pre,
            'ti_post': ti_post,
            'ti_tax': ti_tax,
            'ti_profit': ti_profit,
            'eq_pre': eq_pre,
            'eq_post': eq_post,
            'eq_tax': eq_tax,
            'irr_ti_pre': irr_robust(ti_pre),
            'irr_ti_post': irr_robust(ti_post),
            'irr_eq_pre': irr_robust(eq_pre),
            'irr_eq_post': irr_robust(eq_post),
            'lcoe': lcoe,
            'tot_gen': float(np.sum(tgen)),
            'w_gen_sum': float(np.sum(self._gen_arrays()[0])),
            's_gen_sum': float(np.sum(self._gen_arrays()[1])),
            'tot_cost': float(np.sum(costs['total'])),
            'tot_cost_lifetime': lcoe['total_cost_lifetime'],
        }
        return self._res

    def output(self):
        if self._res is None:
            self.run()
        r = self._res
        return {
            'IRR税前': round(r['irr_ti_pre'] * 100, 4) if r['irr_ti_pre'] else None,
            'IRR税后': round(r['irr_ti_post'] * 100, 4) if r['irr_ti_post'] else None,
            '资本金IRR税前': round(r['irr_eq_pre'] * 100, 4) if r['irr_eq_pre'] else None,
            '资本金IRR税后': round(r['irr_eq_post'] * 100, 4) if r['irr_eq_post'] else None,
            '自用累计发电量_MWh': 0,
            '上网累计发电量_MWh': round(r['tot_gen'], 0),
            '风电累计发电量_MWh': round(r['w_gen_sum'], 0),
            '光伏累计发电量_MWh': round(r['s_gen_sum'], 0),
            '总发电量_MWh': round(r['tot_gen'], 0),
            '总成本_万元': round(r['tot_cost_lifetime'], 2),
            '风度电成本_元perKWh': round(r['lcoe']['wind'], 6),
            '光度电成本_元perKWh': round(r['lcoe']['solar'], 6),
            '度电成本_元perKWh': round(r['lcoe']['total'], 6),
        }

    def report(self):
        if self._res is None:
            self.run()
        r = self._res
        c = r['costs']

        print("=" * 72)
        print("  风光储一体化项目 IRR 测算报告")
        print("=" * 72)
        print(f"\n  【规模】风电 {self.w_mw:.0f}MW + 光伏 {self.s_mw:.0f}MW + 储能 {self.e_mwh:.0f}MWh")
        print(f"\n  【投资(万元)】")
        print(f"    风电: {self.wind_inv:>12.2f}    光伏: {self.solar_inv:>12.2f}")
        print(f"    储能: {self.stor_inv:>12.2f}    土地: {self.land:>12.2f}")
        print(f"    勘察: {self.survey:>12.2f}    接入: {self.connection:>12.2f}")
        print(f"    建设投资: {self.const_inv:>10.2f}    利息: {self.const_int:>12.2f}")
        print(f"    流动资金: {self.wc:>10.2f}    总投资: {self.total_inv:>12.2f}")
        print(f"    资本金: {self.equity:>12.2f}    借款: {self.total_debt:>12.2f}")

        print(f"\n  【发电量(MWh)】风电年 {r['wind_gen'][0]:.0f}  光伏首年 {r['solar_gen'][0]:.0f}")
        print(f"    累计: 风电 {r['w_gen_sum']:.0f}  光伏 {r['s_gen_sum']:.0f}  总 {r['tot_gen']:.0f}")

        print(f"\n  【成本】全生命周期总成本: {r['tot_cost_lifetime']:.2f} 万元")
        print(f"  【度电成本】风电 {r['lcoe']['wind']:.6f}  光伏 {r['lcoe']['solar']:.6f}  综合 {r['lcoe']['total']:.6f} 元/kWh")

        def p(v): return f"{v*100:.4f}%" if v else "N/A"
        print(f"\n  【IRR】")
        print(f"    全投资 IRR 税前: {p(r['irr_ti_pre']):>10s}    税后: {p(r['irr_ti_post']):>10s}")
        print(f"    资本金 IRR 税前: {p(r['irr_eq_pre']):>10s}    税后: {p(r['irr_eq_post']):>10s}")

        # 年度明细
        print(f"\n{'='*90}")
        hdr = f"{'年':>4} {'收入':>10} {'经营成本':>10} {'折旧':>10} {'利息':>10} {'利润':>10} {'税前CF':>12} {'税后CF':>12}"
        print(hdr)
        print("-" * 90)
        for i in range(self._oper_years):
            idx = self._const_years + i
            print(f"{i+1:>4} {r['revenue'][i]:>10.1f} {c['op_cost'][i]:>10.1f} "
                  f"{c['depr'][i]:>10.1f} {c['interest'][i]:>10.1f} "
                  f"{r['ti_profit'][i]:>10.1f} "
                  f"{r['ti_pre'][idx]:>12.1f} {r['ti_post'][idx]:>12.1f}")
        print("-" * 90)


# ==============================================================================
# 便捷函数
# ==============================================================================

def calculate(wind=200, solar=100, storage=180):
    return IRRCalculator(wind, solar, storage).output()


# ==============================================================================
# CLI
# ==============================================================================

def main():
    p = argparse.ArgumentParser(description='风光储一体化项目 IRR 测算模型')
    p.add_argument('--wind', type=float, default=200, help='风电规模 (MW)')
    p.add_argument('--solar', type=float, default=100, help='光伏规模 (MW)')
    p.add_argument('--storage', type=float, default=180, help='储能规模 (MWh)')
    p.add_argument('--json', action='store_true', help='JSON格式输出')
    a = p.parse_args()
    # a.wind = 150

    m = IRRCalculator(a.wind, a.solar, a.storage)
    if a.json:
        import json
        print(json.dumps(m.output(), ensure_ascii=False, indent=2))
    else:
        m.report()


if __name__ == '__main__':
    main()
