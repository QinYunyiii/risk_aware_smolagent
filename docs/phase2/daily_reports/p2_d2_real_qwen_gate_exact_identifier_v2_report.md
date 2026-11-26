# P2-D2 Real-Qwen Gate — Exact Identifier v2 / Final Acceptance

日期：2026-11-26（Asia/Shanghai）。
**最终 Gate：P2_D2_REAL_QWEN_GATE_PASS。固定五个新增Train多步骤任务：5/5通过。**

Run ID：`p2_d2_full_gate_exact_identifier_20261126_78ed12bd`。
实验版本：`p2-d2-full-gate-1024-exact-identifiers-v2`。

## 1. 结果与范围

本次以真实Qwen3-8B，在四领域新增Train任务上完成第二次依赖工具Observation后的Checkpoint、
完整Agent/Environment/Runtime独立恢复、六类Hash、精确首次模型输入及真实恢复模型请求、工具重放防护。
不是Fake、参考Runner或仅凭汇总猜测成功；原始Provider响应、15份解析记录和183项核心产物均保存。

| Task | Domain | Worker | 工程验收 |
| --- | --- | --- | --- |
| p2_data_region_units_sum_001 | data_analysis | worker-0 | PASS |
| p2_file_report_control_001 | file_retrieval | worker-1 | PASS |
| p2_api_process_control_001 | api_workflow | worker-0 | PASS |
| p2_logic_chain_control_001 | calculation_logic | worker-1 | PASS |
| p2_data_region_units_sum_002 | data_analysis | worker-0 | PASS |

Checkpoint=5，六Hash全匹配=5，精确输入匹配=5，真实恢复请求=5，Replay Guard阻断=5。
模型请求失败、Usage缺失、重放失败、任务替换、自动重试、Worker Failover均0。

**P2-D2的“继续”范围仍按原冻结协议：独立恢复后的单次真实request_next_model_response。**
恢复返回的工具没有执行，不将该工程Gate宣称为完整resume到终止/最终Verifier成功。
完整CONTINUE属于既有B3范围；本轮没有进入B3新实验、P2-D3或正式160任务采集。
formal_experiment=false、research_dataset_eligible=false，Y_continue/Y_retry/Y_replan均为null。

## 2. 用户批准的唯一实验输入变化

此前Run `p2_d2_full_gate_1024_windows_20261126_8fb15d86`在T3失败：
任务要求order-83001，真实模型调用get_order(order_id="83001")。
该Run的2/5结果和全部文件原样保留，不回填为成功。

用户明确回复“是的”，批准仅把T3的完整标识符写得更明确，并在独立新Run执行原五任务。
本次新增配置`configs/p2_d2_t3_exact_identifier_v2.yaml`；没有覆盖原配置、Catalog或任务生成器。

原T3 Prompt SHA-256：`1dbdf3b4595c30d8c8aa4597be63f9d2d583dee1271beb4b280f2d9d0e9733cc`。
新版T3 Prompt SHA-256：`bd0c74266c093626e6ba65f2b984d1ed2035ed0bfcdd0733321b7fd80c66d71e`。
新增profile文件SHA-256：`c475a73789ae8649df1ec558ae0e6829a9f5cf37b44a1c24f9618c5bb935f40a`。

实际新版提示全文：

> Use the API tools to read the order whose exact order_id string is "order-83001" (including the "order-" prefix). Use the status returned by get_order as expected_status to update that same order to "processing" with idempotency_key "p2_api_process_control-001". Then query the same exact order_id again and return the observed final state. Do not shorten or normalize identifiers.

该文本仅明确原任务已有ID及其前缀，未提供最终答案、修改任务数据库、模型参数或工具参数。
T1、T2、T4、T5提示完全不变，Task ID/Template/Seed/Split/reference步骤与预期状态不变。

为保持Checkpoint v2严格的“Agent Task Prompt必须与TaskSpec一致”，仅在本次内存中的T3
TaskSpec派生副本替换同一Prompt；Source、独立Restore及离线verify_v2使用同一派生TaskSpec。
没有删除、跳过或放宽Checkpoint校验。
Manifest同时记录原TaskSpec Hash与有效TaskSpec Hash：
`479fc1287a3f25ec3e7c40cecf55dd85027af7b4b73e3f036467dffac986669c` → `b85b5b5468036ae98e6189eacb8cc75f19b9739121d2eae6e068792c2f4f641b`。
原Catalog/任务生成文件未改，base Catalog Hash与渲染输入版本分开追踪。

## 3. 保持不变的冻结项

- 原T1–T5顺序及worker-0/1/0/1/0映射；全部新增Train，四领域，五个独立Task ID。
- Catalog文件及canonical Hash：c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c。
- 模型Qwen/Qwen3-8B，seed=20261118，temperature=0，max_tokens=1024，thinking=false。
- 10 Agent steps/10 model calls/12 tool calls/50,000 tokens/8,192 context/120秒request/900秒task预算。
- 每任务1次Source Attempt、失败即停、无自动重试/Failover/任务替换。
- Checkpoint v2、独立Restore、Source/Checkpoint/Restore首次输入比对、API幂等及Replay Guard。
- 严格TLS/CA/hostname、已有API Key和模型身份，无verify=False或curl -k。

原p2_d2_full_gate_1024.yaml及remote_model_p2_d2_1024.yaml SHA均与基线一致。
新增profile不允许改变其他任务、Prompt全文、原始提示hash、基线hash、版本或增加推理参数字段，
未注册的profile会在正式CLI运行前被拒绝。

## 4. 正式入口与版本记录

```text
.venv/bin/python scripts/run_p2_d2_full_gate_1024.py --mode full-five-task-gate-1024 --authorize-remote --run-id p2_d2_full_gate_exact_identifier_20261126_78ed12bd --task-prompt-profile configs/p2_d2_t3_exact_identifier_v2.yaml --remote-evidence runs/phase2_p2_d2_exact_identifier_gate/p2_d2_exact_identifier_gate_20261126_8d066a8c/remote_gate_evidence.json --connection-spec runs/phase2_p2_d2_exact_identifier_gate/p2_d2_exact_identifier_gate_20261126_8d066a8c/full_gate_connection_spec.json
```

CLI退出0。run_manifest记录baseline版本p2-d2-full-gate-1024-v1、新版本、profile全文/文件hash、
原/新Prompt及TaskSpec hash、原配置和实现hash。task_selection保留原Catalog分组与固定选项。
没有以旧版本名称混报新提示结果。

修改文件：

- p2_d2_completion_budget.py：独立精确ID profile注册、全文/基线约束。
- p2_d2_real_qwen_gate.py：显式版本门禁、T3派生Prompt进入一致TaskSpec。
- run_p2_d2_full_gate_1024.py：可选profile入口及完整版本/输入摘要。
- configs/p2_d2_t3_exact_identifier_v2.yaml、tests/test_p2_d2_exact_identifier_profile.py。

没有修改Checkpoint v2、V1 Frame、TLS Classifier、TaskGenerator、原Catalog和历史Fixture。

## 5. 当前远端服务、GPU与传输

沿用此前真实验证成功的Windows二进制TCP转接，WSL OpenSSH仍持有密钥、加密、Host Key校验、
本地127.0.0.1的-L及owned生命周期。Windows只负责原Endpoint的TCP字节，不获取API Key/私钥，
不启动远端服务、不创建本地额外listener、不迁移工具执行。

当前双Worker在36f825bcc5da、root、Ubuntu22.04，Physical GPU 0/1及对应UUID、PID、命令摘要和监听归属
前后匹配。两次限定GPU0/1采样时间：2026-11-26T08:38:08.220298+00:00、2026-11-26T08:39:13.388755+00:00。
现场7份权重/config/index文件SHA-256匹配冻结值；版本vLLM=0.34.0、
torch=2.14.0+cu132、transformers=5.22.2。
不使用GPU2–7，不重启、停止或启动vLLM，不修改远端配置/文件，不使用Docker或占用新GPU。

正式Gate的两个HTTPS API预检均TLS验证通过，返回Qwen/Qwen3-8B。
已有真实双WorkerSmoke Run `p2_d2_windows_transport_smoke_20261126_3d23818c`的PASS保留。
没有声称已确定WSL直连失败的底层网络根因；当前是经真实验证可用的替代TCP传输路径。

## 6. 逐项完成审计

| 要求 | 独立复核证据 |
| --- | --- |
| 五个真实新增Train多步骤任务、四领域 | task_selection + 5份task_attempts，固定唯一Task ID、split=train |
| 两次真实依赖调用后捕获 | 每任务source_model_calls=2、source_tool_calls=2，dependency/trajectory |
| Checkpoint完整可验证 | 5份checkpoint manifest，全部再运行verify_v2，原产物Hash不变 |
| Agent/Environment/Runtime独立恢复 | 六Hash每任务全部true，SQLite独立、integrity=ok |
| 首次恢复请求精确匹配 | source_next_input_hash = checkpoint_next_input_hash = restored_actual_input_hash，5/5 |
| 真实恢复请求 | 每任务1次，Provider phase=restore；计数延续为3，不额外执行工具 |
| 重放防护 | 5次blocked_before_execution，不改变环境、History或工具预算 |
| API写入幂等 | T3 update_order_history_count=1、idempotency_key_count=1，order-83001 processing/version2 |
| Checkpoint不可变 | 全部checkpoint_immutable=true；183个核心产物大小/SHA复核 |
| 真实响应/Usage/Parsing | 15个Provider response + 15个Parsing记录，上限1024，Usage全部完整 |
| 资源释放 | 2个WSL SSH child退出、端口关闭；所有本次Windows helper PID本地复核不存在 |

以上由本轮verify_real_gate_acceptance.py针对实际Run读取原始记录和Checkpoint独立执行，
不只读取passed字段。复核没有追加模型请求或修改Source/Restore/Checkpoint历史文件。

## 7. 本次调用成本

真实Qwen Completion：**15次**（每任务Source2次+Restore1次）。
Prompt tokens：**31002**；Completion tokens：**1231**；
总tokens：**32233**；Provider累计Latency：**39.765380秒**。
五任务累计wall_clock约40.292720秒，不与Provider latency混用。
两个GET模型身份预检另计，不当作Completion；没有自动重试、Failover或替换任务。

原始Provider数据及工具轨迹属于私有工程证据，不是可自动上传的正式训练样本。
Provider Request ID为null时原样保留，保留实际Provider Response ID，不伪造缺失字段。

## 8. 测试与本地验证

新版专项及相关P2-D2回归：**32 passed in 9.63s**。
全仓：**787 passed in 65.20s，0 failed，0 skipped**，新增9例版本/Prompt/正式入口测试。
compileall、git diff --check、敏感信息扫描通过。

正式CLI Fake E2E完整运行15个Mock Provider调用和5个Checkpoint/Restore，
验证只有T3输入变化、参数/其他任务不变、原子写入一次、六Hash和精确恢复输入5/5。
负向测试拒绝未经批准的其他任务、Prompt、版本、基线摘要和额外推理参数。
离线防护记录依赖导入时被拒绝的本地IPv6 bind能力检查，无真实网络连接。

测试开发阶段首次发现TaskSpec/Memory提示不一致，被原严格校验正确拒绝；
修复为一致派生TaskSpec。另一次断言未考虑smolagents的New task前缀，修正了测试的字符串定位。
所有失败/通过输出独立保留，前两次失败均发生于Fake测试，没有执行真实模型或弱化Checkpoint。

```text
.venv/bin/python runs/phase2_p2_d2_exact_identifier_gate/p2_d2_exact_identifier_gate_20261126_8d066a8c/offline_test_guard.py -q tests/test_p2_d2_exact_identifier_profile.py tests/test_p2_d2_full_gate_1024.py tests/test_p2_d2_real_qwen_gate.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_exact_identifier_gate/p2_d2_exact_identifier_gate_20261126_8d066a8c/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_exact_identifier_gate/p2_d2_exact_identifier_gate_20261126_8d066a8c/verify_real_gate_acceptance.py
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## 9. 证据、历史完整性与Git

真实Gate：`runs/phase2_p2_d2_full_gate_1024/p2_d2_full_gate_exact_identifier_20261126_78ed12bd/`。
审核/准备目录：`runs/phase2_p2_d2_exact_identifier_gate/p2_d2_exact_identifier_gate_20261126_8d066a8c/`。
原artifact_manifest.json包含**183项**，SHA-256：`138566169a62b98030ce121cbadfbf487da7b0e4d1965bd64f37c61770c9c8f7`。
原manifest不重写；本次独立完成审计、报告和其他派生产物由审核目录独立Manifest覆盖。
实际原始文件没有截断；API Key、Authorization、密码、SSH私钥未进入产物/报告。

历史 **7630个文件**SHA-256保持不变，**145份历史Manifest、4776个条目**复核匹配。
原失败2/5 Run及所有历史BLOCKED结果不改判，旧配置和Catalog均保持字节不变。
HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，分支main，原有未提交/未跟踪修改保护，未Commit/Push。

## 10. 最终判断

**P2_D2_REAL_QWEN_GATE_PASS — 获批精确标识符提示v2版本，5/5真实工程验收完成。**
本结论不表示原提示v1版本通过，不表示5个任务均已运行到最终Verifier成功，
不表示正式风险数据或研究泛化结论成立。
本次P2-D2任务完成后停止，不自动进入P2-D3或正式采集。
