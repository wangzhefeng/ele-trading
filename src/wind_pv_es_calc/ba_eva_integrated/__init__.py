# -*- coding: utf-8 -*-
"""储能容量规划算法整合模块。

从 ba_eva_optim_version/ 整合的四类算法：
- MILP 储能容量+调度联合优化
- 储能可行性评估（电价-负荷匹配分析）
- 多节点容量扫描 + 衰减经济分析
- 光储项目 IRR 扫描（三段式收益模型）
"""
from .milp_capacity_sizer import (
    MILPCapacitySizerConfig,
    MILPCapacitySizerResult,
    solve_milp_capacity,
)
from .feasibility_analyzer import (
    FeasibilityAnalyzerConfig,
    FeasibilityResult,
    PriceAnalysis,
    LoadAnalysis,
    TransformerAnalysis,
    MatchingAnalysis,
    StorageStrategyRecommendation,
    StorageFeasibilityAnalyzer,
)
from .multi_node_scanner import (
    StorageSizingConfig,
    CapacitySweepRow,
    NodeScanResult,
    MultiNodeScanResult,
    scan_single_node,
    scan_multiple_nodes,
    compute_irr,
)
from .data_cleaning import clean_and_merge_time, resample_to_15min
from .pv_storage_irr_scanner import (
    PVStorageIRRConfig,
    PVStorageIRRRow,
    DeltaIRRRow,
    PVStorageIRRResult,
    simulate_annual_gain,
    scan_pv_storage_irr,
)

__all__ = [
    # MILP 容量优化
    "MILPCapacitySizerConfig",
    "MILPCapacitySizerResult",
    "solve_milp_capacity",
    # 可行性评估
    "FeasibilityAnalyzerConfig",
    "FeasibilityResult",
    "PriceAnalysis",
    "LoadAnalysis",
    "TransformerAnalysis",
    "MatchingAnalysis",
    "StorageStrategyRecommendation",
    "StorageFeasibilityAnalyzer",
    # 多节点扫描
    "StorageSizingConfig",
    "CapacitySweepRow",
    "NodeScanResult",
    "MultiNodeScanResult",
    "scan_single_node",
    "scan_multiple_nodes",
    "compute_irr",
    # 光储 IRR 扫描
    "PVStorageIRRConfig",
    "PVStorageIRRRow",
    "DeltaIRRRow",
    "PVStorageIRRResult",
    "simulate_annual_gain",
    "scan_pv_storage_irr",
    # 数据清洗
    "clean_and_merge_time",
    "resample_to_15min",
]
