# -*- coding: utf-8 -*-

# ***************************************************
# * File        : weather.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-11
# * Version     : 1.0.051114
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

# 需要安装的包
import xarray as xr
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, List
import json
from pymongo import MongoClient
from urllib.parse import quote_plus
import pandas as pd


class NetCDFToJSON:
    """
    将 NetCDF 文件解析为多变量 JSON（与达卯气象接口格式一致）
    """

    def __init__(self, file_path: str):
        self.ds = xr.open_dataset(file_path)

    # -------------------
    # 自动识别所有变量单位
    # -------------------
    def extract_units(self) -> Dict[str, str]:
        units = {}
        for name, var in self.ds.variables.items():
            units[name] = var.attrs.get("units", None)
        return units

    # -------------------
    # 整体解析为 DataFrame
    # -------------------
    def to_dataframe(self) -> pd.DataFrame:
        """
        将整个数据集（所有变量 + 坐标）展开为二维表
        """
        df = self.ds.to_dataframe().reset_index()
        return df

    # -------------------
    # 解析 JSON（核心）
    # -------------------
    def to_json_records(self) -> List[Dict[str, Any]]:
        df = self.to_dataframe()

        # 将 numpy 转 Python 类型，避免 JSON 序列化失败
        df = df.where(pd.notnull(df), None)

        # 按行输出 JSON（每行是一个时刻+格点）
        json_list = df.to_dict(orient="records")
        return json_list

    # -------------------
    # 返回 {data: [...], units: {...}} 结构
    # -------------------
    def to_api_format(self) -> Dict[str, Any]:
        """
        生成类似达卯接口输出的数据结构：
        {
            "data": [... 每条记录 ...],
            "units": {... 每个变量的单位 ...}
        }
        """
        return {
            "data": self.to_json_records(),
            "units": self.extract_units()
        }


class WeatherMongoClient:
    def __init__(
        self,
        host="123.60.41.219",
        port=27017,
        database="weather",
        username="weather_app",
        password="weather!@#123",
        timeout_ms=5000
    ):
        username = quote_plus(username)
        password = quote_plus(password)

        # ✅ 关键修复：不再使用 authSource=admin
        self.uri = (
            f"mongodb://{username}:{password}"
            f"@{host}:{port}/{database}"
        )

        self.client = MongoClient(
            self.uri,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms
        )

        self.db = self.client[database]

    def list_collections(self):
        return self.db.list_collection_names()

    def find_to_df(self, collection, query=None, projection=None):
        if query is None:
            query = {}

        data = list(self.db[collection].find(query, projection))
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        if "_id" in df.columns:
            df.drop(columns=["_id"], inplace=True)

        return df

    def find_to_json(self, collection, query=None, projection=None):
        if query is None:
            query = {}

        return list(self.db[collection].find(query, projection or {"_id": 0}))


# 测试代码 main 函数
def main():
    df = xr.open_dataset(r"D:/weather_pre/PRE_1h_2024010100.nc", engine="netcdf4")
    # ------------------------------
    # 
    # ------------------------------
    parser = NetCDFToJSON(r"D:/weather_pre/PRE_1h_2024010100.nc")
    result = parser.to_api_format()
    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("已保存 output.json，可以完整查看解析结果")
    # ------------------------------
    # 
    # ------------------------------
    mongo = WeatherMongoClient()
    print(mongo.list_collections())
    data = list(
        mongo.db["real_data"]
        .find({}, {"_id": 0})
        .limit(1)
    )
    print(data)
    data = list(
        mongo.db["forecast_data"]
        .find({}, {"_id": 0})
        .limit(1)
    )
    print(data)
    data = list(
        mongo.db["area_data_v2"]
        .find({}, {"_id": 0})
        .limit(1)
    )
    print(data)
    data = list(mongo.db["area_data"].find(
        {"city": "通辽市"},
        {"areacode": 1, "_id": 0}
    ))
    print(data)
    mongo.db["forecast_data"].index_information()
    
    from pymongo import MongoClient
    from urllib.parse import quote_plus

    username = quote_plus("weather_app")
    password = quote_plus("weather!@#123")

    host = "123.60.41.219"
    port = 27017
    db_name = "weather"

    uri = f"mongodb://{username}:{password}@{host}:{port}/{db_name}"

    client_index = MongoClient(
        uri,
        socketTimeoutMS=10 * 60 * 1000,
        connectTimeoutMS=60 * 1000,
        serverSelectionTimeoutMS=60 * 1000
    )

    db = client_index[db_name]

    print("✅ MongoDB 认证连接成功")



if __name__ == "__main__":
    main()
