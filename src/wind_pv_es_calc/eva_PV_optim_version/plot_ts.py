# -*- coding: utf-8 -*-

# ***************************************************
# * File        : utils.py
# * Author      : Zhefeng Wang
# * Email       : wangzhefengr@163.com
# * Date        : 2023-05-22
# * Version     : 0.1.052220
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-whitegrid')
# plt.rcParams['font.sans-serif']=['SimHei', 'Arial Unicode MS'] # 用来正常显示中文标签
# plt.rcParams['axes.unicode_minus'] = False # 用来显示负号
plt.rcParams['figure.autolayout'] = True # layout
plt.rcParams['axes.grid'] = True # grid
plt.rc(
    "figure",
    autolayout=True,
    figsize=(11, 4.5),
    titleweight="bold",
    titlesize=18,
)
plt.rc(
    "axes",
    labelweight="bold",
    labelsize="large",
    titleweight="bold",
    titlesize=16,
    titlepad=10,
)
train_scatter_plot_params = dict(
    color="0.75",
    style=".-",
    linewidth=2,
    markeredgecolor="0.25",
    markerfacecolor="0.25",
    legend=True,
    label="Train trues",
)
test_scatter_plot_params = dict(
    color="C2",
    style=".-",
    linewidth=2,
    markeredgecolor="0.25",
    markerfacecolor="0.25",
    legend=True,
    label="Test trues",
)
fit_line_plot_params = dict(
    color="C0",
    style=".-",
    linewidth=2,
    legend=True,
    label="Train preds",
)
pred_line_plot_params = dict(
    color="C1",
    style=".-",
    linewidth=2,
    legend=True,
    label="Test preds",
)
fore_line_plot_params = dict(
    color="C3",
    style=".-",
    linewidth=2,
    legend=True,
    label="Forecast",
)
# global variable
LOGGING_LABEL = Path(__file__).name[:-3]


def series_plot(df: pd.DataFrame, time_col, value_col):
    """
    单时序图

    Args:
        df (_type_): 时序数据
        time_col (_type_): 时间变量
        value_col (_type_): 待预测变量
    """
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False
    
    plt.figure(figsize=(25, 8))
    plt.plot(df[time_col], df[value_col], marker = ".", linestyle = "-.", color='C0', linewidth=1.0)
    plt.legend()
    plt.xlabel(time_col)
    plt.ylabel(value_col)
    plt.title(f"{value_col} 时序图")
    plt.tight_layout()
    plt.grid(True)
    plt.show();


def plot_ele_series(df, year: str, month: str):
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False
    
    plt.figure(figsize=(16, 6))
    plt.plot(df.index, df["Load_kW"], label="负荷 Load (kW)", linewidth=1.5)
    plt.plot(df.index, df["Wind_kW"], label="风电 Wind (kW)", linewidth=1.2, alpha=0.85)
    plt.title(f"{year} 年 {month} 月负荷与风电出力对比（15min）")
    plt.xlabel("时间")
    plt.ylabel("功率 (kW)")
    plt.legend(loc="upper right")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show();


def plot_load_pv_wind_netload(df):
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False
    
    plt.figure(figsize=(16, 6))
    # plt.plot(df["Time"], df["P_kw"], label="负荷 Load (kW)", linewidth=1.8)
    plt.plot(df["Time"], df["PV_kw"], label="光伏 PV (kW)", linewidth=1.5)
    plt.plot(df["Time"], df["Wind_kw"], label="风电 Wind (kW)", linewidth=1.5)
    plt.plot(df["Time"], df["NetLoad_kw"], label="Net Load (kW)", linewidth=1.6, color="black")
    # plt.axhline(0, linestyle="--", color="red", alpha=0.7)
    plt.xlabel("Time")
    plt.ylabel("Power (kW)")
    plt.title("Load / PV / Wind / NetLoad(Net Load = Load - PV - Wind) Power Time Series")
    plt.legend(loc="upper right")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()




# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
