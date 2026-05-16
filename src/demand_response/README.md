# Demand_Response_optim

`Demand_Response_optim` is the new working area for subsequent demand response changes.

Structure:

- `main_RQ_RN.py`: unified public demand-response entry, including the inlined runtime skeleton and `ModelMainClass`
- `main_RQ_RN_localtest.py`: unified all-day local-test entry that exercises automatic dispatch
- `main_RN_rolling_declare.py`: rolling-declare entry for all-day orchestration
- `engine/`: runtime engine modules, including period context, input context, stage calculation, strategy adjustment, baseline and eligibility
- `models/`: shared storage simulation models
- `strategy/`: demand-response strategy business modules
- `testing/`: local-test support modules
- `utils/`: generic helpers only, such as visualization and preprocessing tools
- `data/`: shared input data used by the optimized project

Subsequent optimizations should be applied under this directory only.
