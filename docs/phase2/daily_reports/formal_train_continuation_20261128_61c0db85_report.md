# Formal Train Continuation — 2026-11-28

## Result

**Train 原定排程已完成：96 次唯一 Source attempts，95 个有效主标签（85 成功、10 失败），
1 个保留的历史空标签，19 个有效的同 Checkpoint 重复分支。**

子 Run：`formal_train_child_20261128_61c0db85`，仅执行剩余 17 个任务和 6 个预选重复，
未重放父 Run `formal_train_20261127_ad34d6d2` 已尝试的 79 个任务。
子 Gate 为 `TRAIN_CONTINUATION_COMPLETE`；合并 Gate 为
`TRAIN_COHORT_COMPLETE_WITH_CENSORING`，`quality_passed=true`。
旧父 Run 的 BLOCKED、原始数据和 `p2_api_process_invalid_001` 的 null 全部保持原样。

本报告的 PASS 仅限 Train 采集及其质量审核。Validation/Test 未执行，未训练模型，
`formal_dataset_ready=false`，不宣称完整数据集或正式泛化实验已完成。

## Frozen Implementation and Scope

- 执行 Commit：`ee3ad5b8bceeb7b1999f63818bc9d8ffdf0ee147`，启动时工作区干净，已推送 GitHub。
- 研究协议仍为 `risk-continue-v2.2.0`，Task/Catalog/Split/Prompt/Verifier/捕获规则/预算不变。
- 正式父清单：`553ce3c15b34588889775f7c95cadd5ece94219e1f45b18b165e4858a1c82d08`。
- 预算审计修订：`86374f8ba1e3770cc4a0d45544c1ca0ac6fd21aba5f66e09e4a1cbb20b9f4951`。
- 决策顺序修订：`bbd4bc3bb302590f74bd1626600efed0eedc218e16ed0ee5f691f7eb9bbffefd`。
- 精确续跑 scope Hash：`585429a0abcb97b3b1edc75c32b051b0038b4c0f4b06b1a5bac724104eb5a380`。
- 原候选排程的 Worker、F6 分配、F5 可见 no-progress 和预选重复全部保留；无任务替换。
- Completion 1024、Context 8192、Seed 20261118、Temperature 0、thinking=false；原每任务
  10 模型调用/12 工具调用/50000 Token/900 秒及重复继承 Source 剩余预算的规则不变。

UTC 仍保存原值，不改系统时间。特征先落盘和 SDK 委托先后的证明改由独立序号、同进程
单调时间、特征文件 Hash 和 Hash 链提供，不改变 18 个特征值/定义、不加入模型输入。
预算仍使用原单调时钟。实现、本地验收及旧 Fixture 的受控时钟详见
[Stage A 日报](decision_order_stage_a_20261128_report.md)。

## Preflight and Remote Work

重新核验当前进程原项目 API Key/CA 的存在性，未输出、复制或记录凭据值。
专用 SSH 密钥、严格 known_hosts、BatchMode、禁止密码回退和隔离转发配置均核验通过。
本地初始 8000/8001 未占用，可用磁盘约 939 GiB，CA SHA-256 与可信基准一致。

一次只读远端预检退出 0，单调耗时 96.976 秒：容器/root 身份、Ubuntu 版本、CA、两个
Worker 进程归属、GPU 映射、间隔 GPU 采样、7 个模型权重文件 Hash 和模型软件版本均通过。
本次实际观测 Worker 0 → GPU 0、Worker 1 → GPU 1，均为原有项目服务；这不构成后续运行
的固定映射。未启动/重启 vLLM、抢占 GPU、修改远端文件或管理远端进程。

随后仅建立本次拥有的两个独立 LocalForward，双端点严格 TLS、鉴权和模型身份预检通过。
只读 SSH 用 ClearAllForwardings；隧道按已批准的 `-F /dev/null` 隔离和显式 LocalForward
方案，不把二者混淆。原 TLS 验证策略未改变。远端证明绑定新 child Run，沿用既有
0..300 秒新鲜度门禁；监督入口在检查前有一次本地 3 秒等待，不产生网络重试。

正式 child 监督进程退出码 0，非超时、已回收；单调耗时 **152.079 秒**。
原始 UTC 起止为 2026-11-28 04:29:19.095711–04:31:40.632678 UTC（北京时间 12:29–12:31）。
UTC 差值与单调耗时不同，不能把 UTC 相减当作预算耗时。源 UTC 保持原样，不伪造校时。

## Results and Costs

| 指标 | 父 Run（不变） | 本次 child | 合并 |
| --- | --- | --- | --- |
| 唯一 Source attempts | 79 | 17 | 96 |
| 捕获 Checkpoint | 79 | 17 | 96，均在首个合格点 step 1 |
| 有效主标签 | 78 | 17 | 95 |
| 主成功 / 主失败 | 72 / 6 | 13 / 4 | 85 / 10 |
| 空标签 | 1 | 0 | 1，旧审计缺口保留 |
| 有效预选重复 | 13 | 6 | 19 |
| 真实 Provider 请求 | 286 | 129 | 415 |
| Source / 主 CONTINUE / 重复请求 | 79 / 176 / 31 | 17 / 81 / 31 | 96 / 257 / 62 |
| Provider Usage 完整 | 286/286 | 129/129 | 415/415 |
| Parsing 收据 | 285 | 129 | 414，旧空标签的 1 条缺口不补写 |
| Prompt Tokens | 631800 | 261968 | 893768 |
| Completion Tokens | 15520 | 3825 | 19345 |
| 总 Tokens | 647320 | 265793 | 913113 |

本次最大观测 Completion 为 62，低于固定 1024。Parsing 为 125 条 success、4 条
not_attempted（预算前置保护），均与实际响应逐项关联，不伪造 success。
本次新增重复不额外创建独立风险样本。真实运行无 SDK 自动重试、无 Source 重试、无 Failover。

本次 4 个有效失败：

- `p2_api_process_invalid_002`：budget_exhaustion。
- `p2_logic_chain_invalid_001`：budget_exhaustion。
- `p2_logic_chain_invalid_002`：budget_exhaustion。
- `p2_logic_repeated_calculation_002`：final_answer 后 verification_failure。

F3 API 任务在 Source 1 + CONTINUE 9 次真实请求后触达预算，本次 Provider/Parsing 为
10/10，决策顺序有效；原 Verifier 规则产生有效 0。还有一次本地最终兜底尝试被旧
public_tool_schemas 保护拒绝，没有增加第 11 次网络调用。这验证了预算审计修复的真实路径，
没有提高预算，也没有重跑旧的 `_001` 空标签任务。

## Restore, Feature and Repetition Audit

- **95/95 有效主分支、19/19 有效重复分支**的六类 Restore Hash、首次模型输入、
  Checkpoint 不变性、Source 不变性及 Replay Guard 全部通过。
- 95 个正式风险行都从原 Source/Checkpoint 重算 18 特征，值/Hash/provenance 匹配；
  Risk→Checkpoint/Outcome 的记录 Hash 关联全部匹配，没有导入 Smoke 或 Fixture。
- 父有效样本的原 UTC cutoff 证据复核通过；child 的 146 条顺序记录覆盖 129 个请求边界
  和 17 个特征 cutoff，**23/23 child 分支**（17 主 + 6 重复）的因果顺序与归档子集一致；
  该顺序字段不作为风险特征。
- 19 个同 Checkpoint 重复有 **18 个与主标签相同、1 个不同**：
  `p2_logic_repeated_calculation_002` 主标签 0、重复标签 1。两个分支的初始恢复 Hash 和
  首次输入均匹配、参数和 Worker 不变。保留两个真实结果；差异原因未进一步定位，
  不能假定完全确定，也不能强改标签一致或作为两个独立训练行。
- 四 Domain 的 F0/F1/F2/F3/F5 均实际覆盖；API 另覆盖 F4/F6。
  F6 committed/not_committed/unknown 三种真值均在有效主样本中实际观测，不依赖重复补覆盖。

## Integrity, Eligibility and Resource Release

原始数据仍仅本地保存：

- child：`runs/phase2_formal_train_continuation/formal_train_child_20261128_61c0db85/`。
- 预检/监督/资源释放及最终核验：
  `runs/phase2_formal_train_continuation_preflight/formal_child_preflight_20261128_61c0db85/`。

child Manifest 的 **993 个文件**逐一 SHA-256/长度匹配；SHA-256：
`bf5b52442f49a1c101c5ac3b2eb9c7963ae6d4a49cc9f40449e59da4380fd957`。
父 Run 可信 Manifest 的 **4325 个文件**及任务不可变归档再次全部匹配，未写入父目录。
所有新增规范化记录链、源尝试唯一性、显式 parent/child 绑定和 cohort launch claim 核验通过。

整批质量门禁满足最低有效数 72、两类标签、原故障覆盖、原重复规则和有效样本完整性。
在释放连接后发布 append-only 的 `train_eligibility_release.json`，引用 **95 个原始风险记录
Hash**；旧 raw eligibility 标记不回写，所有 Smoke/Fixture 不在索引中。此索引只限定 Train。
一条历史删失与版本边界必须在后续分析中披露，不能当作随机无关缺失或补为 0。

本次 40 个模型客户端均已关闭。两个 SSH 隧道退出 0，两个本地端口关闭；3 个 Windows
TCP Proxy 的原 PID/启动身份均已不存在，未终止其他进程。核验只使用本地进程元数据和
端口表，收尾新增实验室连接/模型请求为 0。所有原始产物均未发现继承 API Key 明文。
没有通过额外远端请求声称服务仍健康；本轮操作记录可确认未操作/清理远端 Worker。

## Tests, Git and Remaining Work

实施前置全仓 **1114 passed**；真实运行后离线专项 **11 passed，1 deselected**，5.04 秒，
排除的仅为已在全仓通过的较长模拟整批项。compileall 和 diff check 通过，原始数据未上传。
实际入口：预检目录 `run_remote_preflight.py`、`run_child_once.py`、`verify_local_release.py`；
实际 CLI argv 保存在同目录 `gate_argv.json`，包含新 Run、精确父 Manifest、原授权范围、
新版本本地资格和新鲜远端证明。所有模型请求、退出码和费用为真实记录，不以 Mock 替代。

生产代码 `ee3ad5b` 已推送；本脱敏日报按仓库规范单独提交。历史产物、配置和父清单不变。
下一步是审核本批 Train 数据及删失/重复分歧，再按独立授权安排 Validation/Test；不自动
进入下一采集阶段。95 行及 10 个失败类仍未达到原 LightGBM 200 Train/每类 30 门禁，
不降低训练门槛、不通过重复扩样，也不据当前 Train 宣称泛化或风险模型有效。

## Status: PASS

限 Train 原定采集、续跑与带显式删失的质量验收；原父 Run BLOCKED 保留，整个正式数据集
未就绪，Validation/Test、风险模型训练及 RETRY/REPLAN 扩展均未执行。
