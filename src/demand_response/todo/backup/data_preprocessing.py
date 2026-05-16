# -*- coding: utf-8 -*-

# ***************************************************
# * File        : data.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2025-11-13
# * Version     : 1.0.111317
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

import pandas as pd

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
from utils.log_util import logger


project_dir = Path(__file__).parent.parent
print(project_dir)

# strategy
df_strategy_A = pd.read_csv(project_dir.joinpath("data/df_strategy_load_hist_pred_A.csv"), encoding="utf-8")
df_strategy_B = pd.read_csv(project_dir.joinpath("data/df_strategy_load_hist_pred_B.csv"), encoding="utf-8")
df_strategy = pd.DataFrame({"time": df_strategy_A["time"]})
df_strategy["value_A"] = df_strategy["time"].map(df_strategy_A.set_index("time")["value"])
df_strategy["value_B"] = df_strategy["time"].map(df_strategy_B.set_index("time")["value"])
df_strategy.to_csv(project_dir.joinpath("data/df_strategy_load_hist_pred.csv"), encoding="utf-8", index=False)
print(df_strategy)


# demand load hist and pred
df_load_hist_A = pd.read_csv(project_dir.joinpath("data/df_demand_load_hist_pred_A.csv"), encoding="utf-8")
df_load_hist_B = pd.read_csv(project_dir.joinpath("data/df_demand_load_hist_pred_B.csv"), encoding="utf-8")
df_load_hist = pd.DataFrame({"time": df_load_hist_A["time"]})
df_load_hist["value_A"] = df_load_hist["time"].map(df_load_hist_A.set_index("time")["value"])
df_load_hist["value_B"] = df_load_hist["time"].map(df_load_hist_B.set_index("time")["value"])
df_load_hist.to_csv(project_dir.joinpath("data/df_demand_load_hist_pred.csv"), encoding="utf-8", index=False)
print(df_load_hist)


# aidc load hist and pred
df_aidc = pd.DataFrame({"time": df_load_hist_A["time"]})
df_aidc["value_A"] = df_load_hist["value_A"] - df_strategy["value_A"]
df_aidc["value_B"] = df_load_hist["value_B"] - df_strategy["value_B"]
df_aidc.to_csv(project_dir.joinpath("data/df_aidc_load_hist_pred.csv"), encoding="utf-8", index=False)
print(df_aidc)




# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
