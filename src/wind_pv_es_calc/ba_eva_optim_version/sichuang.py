# -*- coding: utf-8 -*-

# ***************************************************
# * File        : sichuang.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-11
# * Version     : 1.0.051115
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import os
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.pyplot as plt
from matplotlib import font_manager

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']   # 指定中文字体
plt.rcParams['axes.unicode_minus'] = False              # 解决负号显示问题


# ================================
# data
# ================================
data = {
    "month": ["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"],
    "PV": [52285,60178,83608,94602,88066,78431,95484,105585,68773,56048,49474,49965],
    "Load": [99252,125606,124538,129517,116651,120566,124086,132005,131141,108861,98649,83972],
    "Curtail": [24454,23088,40336,45751,42382,33912,44146,50418,27116,23514,23358,26360]
}
df = pd.DataFrame(data)


# ================================
# 2. 自定义 IRR（避免 np.irr NaN）
# ================================
def npv(rate, cashflows):
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))

def irr_bisection(cashflows, low=-0.9, high=10, tol=1e-6):
    # 无正现金流 → IRR不存在
    if all(cf <= 0 for cf in cashflows[1:]):
        return None

    f_low = npv(low, cashflows)
    f_high = npv(high, cashflows)

    if f_low * f_high > 0:
        return None  # 无变号 → 无 IRR

    for _ in range(200):
        mid = (low + high) / 2
        f_mid = npv(mid, cashflows)

        if abs(f_mid) < tol:
            return mid

        if f_low * f_mid < 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return mid


# ================================
# 3. 三段式收益模型（按优先级）
# ================================
def simulate_annual_gain(storage_MWh, buy_price, df):
    export_price = 0.285
    annual_energy = 0
    annual_gain = 0

    for _, row in df.iterrows():
        PV = row["PV"]
        Load = row["Load"]
        Curtail = row["Curtail"]

        # ① PV 自用
        PV_self = min(PV, Load)
        Gain1 = PV_self * 1000 * buy_price
        load_after_PV = max(Load - PV_self, 0)

        # ② 储能平移弃电
        storage_used = min(storage_MWh, Curtail, load_after_PV)
        Gain3 = storage_used * 1000 * buy_price

        # ③ PV 上网 ≤20%
        PV_left = PV - PV_self - storage_used
        PV_export = min(max(PV_left, 0), PV * 0.20)
        Gain2 = PV_export * 1000 * export_price

        annual_gain += (Gain1 + Gain2 + Gain3)
        annual_energy += (PV_self + storage_used + PV_export)  # 整体发电计 O&M

    return annual_gain, annual_energy


# ================================
# 4. IRR 计算（包含 PV CAPEX）
# ================================
def calculate_IRR(df, storage_range, buy_price,
                  PV_CAPEX=2300000000,           # 光伏 23 亿
                  storage_cost_per_kWh=800,      # 800 元/kWh
                  life_years=12,
                  platform_fee=9000000,          # 平台费用900万/年
                  o_and_m_price=0.04):           # O&M 单价 0.04 元/kWh

    rows = []

    for cap in storage_range:

        # 年收益（元） + 年发电量（MWh）
        annual_gain, annual_energy = simulate_annual_gain(cap, buy_price, df)

        # 年 O&M
        OandM = annual_energy * 1000 * o_and_m_price

        # 总架构的年度净现金流
        annual_CF = annual_gain - OandM - platform_fee

        # 储能 CAPEX
        Storage_CAPEX = cap * 1000 * storage_cost_per_kWh

        # 总 CAPEX = 光伏 + 储能
        Total_CAPEX = PV_CAPEX + Storage_CAPEX

        # 现金流序列（20 年寿命）
        cashflows = [-Total_CAPEX] + [annual_CF] * life_years

        IRR = irr_bisection(cashflows)

        rows.append([cap, annual_gain, annual_CF, Total_CAPEX, IRR])

    return pd.DataFrame(rows, columns=[
        "Storage_MWh", "AnnualRevenue_Yuan", "AnnualCF_Yuan",
        "TotalCAPEX_Yuan", "IRR"
    ])


# ================================
# 5. 参数区 & 运行
# ================================
cap_range = np.arange(400, 981, 50)        # 储能规模
price_range = np.arange(0.30, 0.501, 0.02) # 购电价


# ===========================
#  运行
# ===========================
result = scan_irrs(
    df,
    cap_range,
    price_range,
    storage_cost_per_kWh=800,
    life_years=25,
    o_and_m_rate=0.02
)
print(result)
# 保存 Excel
result.to_excel("storage_IRR_result.xlsx", index=False)
print("✔ 表格已生成：storage_IRR_result.xlsx")


# ===========================
# 绘制 IRR 曲线图
# ===========================

plt.figure(figsize=(10,6))

for price in price_range:
    subset = result[result["BuyPrice"] == price]
    plt.plot(subset["Storage_MWh"], subset["IRR"], label=f"购电价 {price:.2f}")

plt.axhline(0, color='gray', linestyle='--')
plt.xlabel("储能规模 (MWh)")
plt.ylabel("IRR")
plt.title("储能规模 vs IRR（不同购电价格）")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()

print("✔ IRR 曲线图已生成")




# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
