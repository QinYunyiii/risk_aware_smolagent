# P1-D2 Report

## Objectives

审计 Day 10 风险模型的 26 个特征是否含人工故障构造捷径；追踪完整生成路径；在不
改变样本、标签、fold、阈值和超参数的前提下完成固定 template-grouped OOF 消融；
形成明确的部署 allowlist。本次只执行 P1-D2。

## Local Environment Work

- 工作目录：`/home/three/projects/risk-aware-smolagents`。
- 起始 Git 基线：`d116cbf chore: establish audited project baseline`；起始工作区干净。
- 完整阅读四份根目录指南和 P1-D1 日报。
- 只读加载 Day 9/10 的正式 run、Day 10 配置和原始模型 metadata/结果。
- 新增独立审计脚本和测试；历史 Day 9/10 训练代码、模型、数据与日报未修改。
- 所有计算使用项目 `.venv`，没有访问官方 Validation/Test。

## Remote Environment Work

无。没有执行 SSH、HTTPS API、Qwen 请求、GPU 检查、vLLM 启停或 Docker 操作。

## Work Completed

1. 逐项追踪 26 个特征从 Day 9 scenario、`runtime_state.json`、
   `extract_risk_features()` 到 Day 10 dataset 的生成路径。
2. 将每个特征标为 agent-visible proxy、simulator-internal、injection metadata 或
   post-decision，并记录决策时刻可用性、部署许可和当前 Day 9 可见性证据。
3. 验证 `failure_response_received` 与标签完全分离：9 个 failure 全为 1，11 个
   success 全为 0。
4. 验证 Day 9 `_failure()` 根据 fault type 生成 `response_received`，不是从真实
   Qwen tool observation 解析。
5. 验证 20 个 runtime 中 15 个有 `last_failure`，也恰好有 15 个 synthetic error
   history；Agent memory 中没有 tool/observation role。
6. 完成用户指定六组消融，并补充指南要求的 observed-response、injection-only 和
   deployment-allowlist 诊断。
7. 所有 feature set 共享同一个 5 折 `StratifiedGroupKFold` 对象；复算 fold assignment
   与原 Day 10 逐 Checkpoint 完全一致。
8. 加入 training-fold failure prevalence constant baseline。
9. 为每个模型/feature set 保存 overall 和逐折样本数、类别数、AUROC、AUPRC、
   Brier、Log Loss、Precision、Recall、calibration curve 和 OOF prediction。
10. 重新计算全特征模型，原始 Day 10 OOF prediction 最大绝对差为 0。

## Files Changed

新增且未覆盖历史文件：

- `scripts/audit_p1_d2_risk_features.py`
- `tests/test_p1_d2_risk_audit.py`
- `docs/phase2/risk_feature_audit.md`
- `docs/phase2/daily_reports/p1_d2_report.md`
- `runs/phase2_audit/risk_feature_ablation.json`
- `runs/phase2_audit/risk_feature_ablation.csv`
- `runs/phase2_audit/risk_oof_predictions.jsonl`

`runs/` 仍按现有 `.gitignore` 作为实验产物目录。本次三份 P1-D2 必交付审计结果体积
较小，且是复核本文结论所需的机器可读证据；经人工批准后仅对其精确路径强制纳入
本次 Git 提交，没有纳入其他历史 run。

## Artifacts Generated

| 产物 | 内容 | SHA-256 |
|---|---|---|
| `docs/phase2/risk_feature_audit.md` | 完整来源、26 特征表、消融结论和部署规则 | `bbf0389ebba9f4605b930b0c1c1ede3a18598057b7ec1ea3dc81ce743bb66a70` |
| `runs/phase2_audit/risk_feature_ablation.json` | 完整审计、fold、指标、校准和 importance | `697e1064f86ef8a31ced7650f4dd17cdd0bdf5542b2c45e1b4b4df5f79271c83` |
| `runs/phase2_audit/risk_feature_ablation.csv` | 114 条 overall/fold 指标记录 | `edd8e584870faf0b30f50aa89522b21e5e1d4e213904873d0a7c879602850c45` |
| `runs/phase2_audit/risk_oof_predictions.jsonl` | 380 条逐 Checkpoint OOF 预测 | `8fdf487b6975d8fb0f89870def852f54153ef679f0d328b81900839ccd346cce` |

## Commands and Tests

```bash
pwd
git status --short
git log -3 --oneline --decorate
sed -n '/## P1-D2/,/## P1-D3/p' NEXT_STAGE_EXPERIMENT_GUIDE.md
rg -n 'FEATURE|last_failure|failure_response_received|StratifiedGroupKFold' ...
PYTHONPATH=src .venv/bin/python scripts/audit_p1_d2_risk_features.py --check-only
PYTHONPATH=src .venv/bin/python scripts/audit_p1_d2_risk_features.py
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts/audit_p1_d2_risk_features.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_p1_d2_risk_audit.py
PYTHONPATH=. .venv/bin/python -m pytest -q
.venv/bin/python scripts/audit_phase2_history.py --check-only
```

另在 `mktemp -d` 临时目录完整重算三份机器产物并用 `cmp` 与正式 P1-D2 输出比较，
比较后删除临时副本。

## Test Results

- P1-D2 audit checks：12/12 true，Gate PASS。
- P1-D2 专项测试：`4 passed in 1.69s`。
- 全量回归：`82 passed in 9.01s`。
- `pip check`：`No broken requirements found.`
- `compileall`：PASS。
- P1-D1 历史基线：11/11 checks 保持 true，Day 7--14 历史摘要未变化。
- 三份机器产物临时重算：`reproducible_outputs=3/3`，字节级一致。
- 首次专项测试收集曾因测试 `importlib` 未注册 dataclass 临时模块而失败；修正测试
  loader 后通过。该失败不涉及数据、fold 或模型协议。

## Key Results

| Feature set | Logistic AUROC/AUPRC | LightGBM AUROC/AUPRC |
|---|---:|---:|
| All 26 | 1.000 / 1.000 | 1.000 / 1.000 |
| Without `failure_response_received` | 1.000 / 1.000 | 1.000 / 1.000 |
| Without `fault_*` | 1.000 / 1.000 | 1.000 / 1.000 |
| Without all `last_failure` metadata | 0.631 / 0.517 | 0.646 / 0.556 |
| History-only | 0.682 / 0.563 | 0.677 / 0.565 |
| Strict Agent-visible proxy | 0.591 / 0.520 | 0.591 / 0.520 |
| Injection metadata only | 1.000 / 1.000 | 1.000 / 1.000 |
| Constant probability | 0.419 / 0.428 | N/A |

原 LightGBM 的 80/80 splits 全部来自 `failure_response_received`。删掉该列后，模型
改用 `fault_f2_api_503`、`fault_f3_invalid_argument`、
`fault_f4_stale_observation` 等注入类型保持满分。因此必须删除整组内部
`last_failure` metadata，而非只删除单列。

## Acceptance Checklist

- [x] 26 个特征全部有名称、含义、来源、生成路径和可观测时间说明。
- [x] 已区分 Agent-visible、simulator-internal、injection metadata 和 post-decision。
- [x] 每个特征都有 `allowed_for_deployment` 判定。
- [x] 用户指定六组消融全部完成。
- [x] 指南要求的 observed/injection/deployment 诊断完成。
- [x] 所有消融使用相同 template-grouped folds，且与原 Day 10 一致。
- [x] 每折样本量、正负类数和六项指标已报告。
- [x] calibration curve 已保存。
- [x] 常数概率基线已加入。
- [x] 标签、样本、CV、阈值和超参数均未改变。
- [x] 只访问 Train OOF，没有访问 Validation/Test。
- [x] 原始 Day 9/10 模型、数据、日志和报告未覆盖。
- [x] 未启动远程 GPU 或调用 Qwen API。
- [x] 未开始 P1-D3。

## Known Issues

1. 只有 20 个同源 Train Checkpoint，每个验证折仅 4 个样本；fold 指标方差很大。
2. Day 9 controller 是 `deterministic_local_reference`，不是 Qwen model-backed branch。
3. 所谓 strict Agent-visible 五特征在当前数据中仍来自 synthetic history；尚未证明
   真实 Qwen 收到了对应 tool response。
4. deployment allowlist 的 simulator-internal 特征必须在未来真实运行时重新证明其
   决策时可获得性，不能复用注入器字段。
5. 当前没有独立 Validation/Test Checkpoint、置信区间或真实模型泛化证据。

## Remaining Work

P1-D2 无剩余核心工作。等待人工审核。P1-D3 未开始；不得用本次指标宣称真实 Qwen
风险预测有效。

## Status: PASS

P1-D2 工程 Gate 全部通过。研究判定是负面且明确：Day 10 满分由 injection metadata
捷径主导，当前 20 个 Train OOF 样本不能支持真实 Qwen 风险预测能力。

## Git Commit

起始基线为 `d116cbf`。人工审核后已批准提交 P1-D2；本报告、审计说明、审计脚本、
专项测试和三份机器可读结果统一包含在题为
`audit: complete P1-D2 risk feature shortcut analysis` 的提交中。提交 ID 以仓库
`git log` 为准，避免在提交内容中写入会因 amend 而失效的自引用哈希。
