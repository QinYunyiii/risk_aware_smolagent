# Day 01 Report

## Objectives

- 审计本地 WSL、Python/Conda/Git/Docker 和项目状态。
- 定位并审计 Recovery-Bench、Harbor、日志、Docker、verifier、Checkpoint/Restore/Branch 能力。
- 审计远程 GPU、vLLM、Qwen3-8B 和本地到远程 SSH/HTTPS/TLS 条件。
- 初始化项目目录、Git 和 `.gitignore`。
- 生成 Day 1 环境、复用和架构文档。

## Local Environment Work

- 确认工作区为 Windows 主机上的 WSL2 Ubuntu 24.04.5 LTS，不是远程 GPU 服务器。
- 系统 `python3` 为 3.12.3；`python` 和 `conda` 不在 PATH，未安装任何依赖。
- Git 2.43.0 可用；项目原有空 `.git`，已执行 `git init`。
- Docker CLI 29.8.2 可用；普通沙箱访问 socket 被拒绝，经用户授权完成只读 engine 资源查询。
- 审计本地磁盘（约 945 GiB 可用）与内存（约 15 GiB available）。
- 创建 `configs/`、`src/`、`data/`、`models/`、`scripts/`、`tests/`、`runs/`、`docs/daily_reports/`。没有创建 Day 2 代码或空占位文件。

## Remote Environment Work

- 用户提供 `root@121.48.161.243:4035` 后，先扫描 Host Key；用户通过服务器控制台确认 ED25519 指纹一致。
- 创建项目专用本地 ED25519 key，私钥权限 600、未写入仓库；远程授权后以 `BatchMode`、`IdentitiesOnly`、`StrictHostKeyChecking` 登录成功。
- 实测目标为 Ubuntu 22.04 Docker 容器，不是设计中的 Ubuntu 24.04 宿主机。
- 容器可见 8 × RTX 4090；用户确认它们是共享实验室资源，本项目每次动态选择两张空闲卡，禁止抢占在用卡。
- Driver 580.178.04，CUDA toolkit 12.5；Conda 25.1.1，base Python 3.12.9。
- 初始审计未发现 vLLM 或 Qwen3 权重；用户随后明确授权安装和下载。
- 保留已有 `agent` 环境不动，新建隔离环境 `risk-agent-vllm`：Python 3.12.16、vLLM 0.34.0、torch 2.14.0+cu132、transformers 5.22.2。
- 通过 ModelScope Hub 将官方 `Qwen/Qwen3-8B` 下载至 `/mnt/risk-aware-smolagents/remote_models/Qwen3-8B`；5/5 safetensors 分片、0 incomplete，索引和配置校验通过。
- 远程 overlay 使用率 98%（安装、下载后约 683 GiB 可用），内存约 192 GiB available，2 GiB swap 已满。
- 当时仅 GPU 3/7 空闲；先完成 GPU 7 极小 CUDA 运算，再按用户明确批准在 GPU 3/7 分别加载独立 Qwen3-8B vLLM 实例。
- 使用 vLLM 原生 TLS、API Key 和远端 loopback listener；本地通过 SSH local forwarding 映射 `127.0.0.1:8000/8001`，两个 Endpoint 的严格 TLS/API/短生成均通过。
- 双实例验收完成后停止本任务的隧道和两个 vLLM 进程；GPU 3/7 均恢复为 15 MiB、0%，模型、环境、证书和日志保留。

## Work Completed

1. 阅读并核实 `AGENTS.md`、`PROJECT_GUIDE.md`、`README.md`；初始没有日报。
2. 定位 Recovery-Bench：`/home/three/projects/agent-intervention/recovery-bench`，HEAD `c5f83f2b`。
3. 保护外部仓库已有的 13,724 个 tracked-file modifications，未写入、还原或清理。
4. 审计 `pipeline.py`、CLI entry、recovery mixin、trajectory replay、Terminus recovery、usage/result helpers。
5. 确认 `per_task.json` 不存在；实际逐任务产物为 `result.json`、`agent/trajectory.json`、`usage.json`、`trial.log`、verifier/artifacts。
6. 审计实际安装的 Harbor 0.4.0 和 Docker backend/Trial lifecycle/schema。
7. 实证检查历史 90 个 result、89 个 trajectory 的结构；确认工具参数/结果、Token、timestamps 和 trial exceptions 的可用性与缺口。
8. 确认 Recovery-Bench recovery 是 best-effort command replay + message context，不是完整 Checkpoint/Restore，也没有 Branch Runner。
9. 完成本地 Docker images/containers/volumes/mounts 清单。
10. 初始化项目 Git、安全 `.gitignore`、目录和 Day 1 文档。
11. 完成远程 SSH 主机身份、认证、容器边界、GPU/Driver/CUDA、Conda/Python、vLLM、模型候选路径、磁盘/内存和端口的只读审计。
12. 发现远程环境与固定部署假设不一致，先停止部署；用户确认正式资源规则为“8 张共享 RTX 4090，每次只使用两张当时空闲的卡”，据此更新长期指南，不再固定 GPU 0/1。
13. 用户随后明确授权下载 Qwen3-8B、安装 vLLM，并要求优先评估已有 `agent` 环境。审计后决定不改动 `agent`，新建隔离环境 `risk-agent-vllm`。
14. 使用 vLLM 官方推荐的独立环境方案安装 vLLM；`pip check` 通过，CLI/import 可用。
15. Hugging Face 连接超时后，使用 TLS 验证正常的 ModelScope Hub 下载 `Qwen/Qwen3-8B`。下载中一次因进度输出管道反压暂停，仅终止并续传本任务自己的进程，保留全部 `.incomplete` 数据；最终 snapshot 完成。
16. 完成模型静态结构检查和空闲 GPU 7 的最小 CUDA smoke test；未启动 vLLM、未加载模型、未占用其他用户 GPU。
17. 用户批准 SSH 隧道方案后，在仓库外生成项目私有 CA、API Key 和服务器证书；服务器私钥在远端生成，CA 私钥未上传，敏感文件权限为 600。
18. 安装 `flashinfer-cubin 0.7.3`；额外 `flashinfer-jit-cache 0.7.3+cu130` 因远端 GitHub 连接超时未安装。
19. 首次模型加载发现 CUDA toolkit 12.5 无法编译 FlashInfer sampling JIT 的 `--compress-mode=size`；按 vLLM 自带开关设置 `VLLM_USE_FLASHINFER_SAMPLER=0`，保留 FlashAttention v2 并改用 PyTorch sampler。
20. 单实例 Worker 0 在 GPU 3 加载成功，严格 TLS、401/200 鉴权、模型列表和短生成通过。
21. 双实例 Worker 0/GPU 3 与 Worker 1/GPU 7 同时加载成功，本地通过 SSH 隧道分别访问两个 HTTPS Endpoint；两端生成预期文本。
22. 验收后精确终止本任务启动的 PID 和本地隧道，确认 GPU 3/7、端口和本地转发均释放；未触碰其他用户进程。

## Files Changed

- `.gitignore`（新增）
- `AGENTS.md`（更新远程共享 GPU 安全边界）
- `PROJECT_GUIDE.md`（更新动态 Worker/GPU 映射）
- `README.md`（更新实际远程资源架构）
- `docs/environment_audit.md`（新增）
- `docs/recovery_bench_reuse.md`（新增）
- `docs/day1_architecture.md`（新增）
- `docs/daily_reports/day_01_report.md`（新增）
- `.git/`（由空目录初始化为 Git metadata）

仓库外安全配置：本地 `~/.ssh/` 新增专用 SSH key pair 和经控制台确认的 `known_hosts` 条目；私钥未复制到项目、文档或日志。

三份长期指南只按用户明确确认更新远程资源边界；研究问题、动作、数据、指标和本地/远程职责未改变。Recovery-Bench 未改动。

## Artifacts Generated

- 本地与远程环境审计：`docs/environment_audit.md`
- Recovery-Bench 复用分析：`docs/recovery_bench_reuse.md`
- 部署与 Day 2–14 依赖架构：`docs/day1_architecture.md`
- 本日报：`docs/daily_reports/day_01_report.md`

## Commands and Tests

主要只读命令：

```text
pwd
uname -a
cat /proc/version
cat /etc/os-release
python --version
python3 --version
conda --version
git --version
git status --short
docker --version
docker ps -a --no-trunc
docker image ls --no-trunc
docker volume ls
docker network ls
docker inspect (format-only mounts query)
df -h .
free -h
rg --files / rg -n (Recovery-Bench tracked source audit)
find ... -name per_task.json
/home/three/projects/agent-intervention/.venv/bin/harbor --version
/home/three/projects/agent-intervention/.venv/bin/python (schema summary scripts)
ssh -V
ssh-keyscan / ssh-keygen -lf
ssh -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
nvidia-smi
nvcc --version
conda --version / conda env list
python --version
df -h / free -h
bounded find for vLLM metadata and Qwen3 directory candidates
direct socket checks
curl --noproxy '*' with normal TLS verification
openssl s_client -verify_return_error
conda create -n risk-agent-vllm python=3.12 pip
uv pip install vllm --torch-backend=auto
ms-hub download Qwen/Qwen3-8B --local-dir /mnt/risk-aware-smolagents/remote_models/Qwen3-8B --max-workers 4
pip check / vllm --version
safetensors safe_open metadata validation
CUDA_VISIBLE_DEVICES=7 minimal PyTorch CUDA matrix smoke test
vllm download-kernels
openssl private CA / CSR / certificate hostname verification
CUDA_VISIBLE_DEVICES=3/7 vllm serve (8K, BF16, single sequence, eager)
strict TLS + API Key `/v1/models` and `/v1/chat/completions`
SSH local forwarding for local `127.0.0.1:8000/8001`
graceful TERM and GPU/port cleanup verification
git init
```

没有运行：

- Recovery-Bench task 或 Harbor trial：避免改变外部环境，且 Day 1 仅审计。
- 单元/集成/E2E 测试：本次未开发运行时代码。
- Day 2 smolagents/Tool Call/本地工具执行联调：未执行，避免提前开发 Day 2 功能。
- 40,960 上下文、高并发、吞吐和 CUDA Graph 性能测试：未执行；Day 1 仅用保守 smoke 配置。
- 容器启动/停止、数据删除：未执行。

## Test Results

- 指南文件：3/3 存在并已读取。
- WSL 识别：通过（内核和 `/etc/os-release` 实证）。
- Git 初始化：通过；新仓库无 commit。
- 本地 Docker inventory：通过只读查询；3 images、3 stopped containers、0 named volumes。
- Recovery-Bench/Harbor source audit：完成；结论有文件、函数和行号证据。
- 历史产物 schema smoke audit：90 result / 89 trajectory；未把历史数据当正式实验。
- 远程 SSH：Host Key 已确认，专用 key 严格校验登录通过。
- 远程 GPU/CUDA/Conda/资源审计：完成；结果与设计不一致，不能作为部署通过。
- `agent` 环境保护：通过；未改动其 torch/transformers，新建 `risk-agent-vllm`。
- vLLM 环境：0.34.0 CLI/import 通过；Python 3.12.16、torch 2.14.0+cu132；`pip check` 无冲突；`flashinfer-cubin 0.7.3` 已安装。
- Qwen3-8B snapshot：5/5 分片、0 incomplete、399 个 index 映射；分片集合和 safetensors metadata 校验通过，总分片字节 16,381,516,776。
- 最小 GPU smoke：GPU 7 当时空闲；CUDA 可用、RTX 4090 识别正确、64×64 运算结果正确，进程退出后恢复 15 MiB/0%。未加载模型。
- 模型加载：Worker 0/GPU 3 与 Worker 1/GPU 7 同时成功；实际 attention backend 为 FlashAttention v2，FlashInfer sampler 因 toolkit/JIT 不兼容使用明确 fallback；观测显存约 20.3/21.6 GiB。
- 远程 API/TLS：远端 loopback 双 Endpoint 严格 CA 验证、无 Key 401、正确 Key 200、模型 ID 和短生成全部通过。
- 本地 SSH tunnel E2E：默认 trust store 正确拒绝私有 CA；显式 CA 后，本地 8000/8001 的模型列表与短生成分别通过，结果为 `TUNNEL_8000_OK` / `TUNNEL_8001_OK`。
- 资源清理：本地 tunnel PID 与远端两个 Worker 均正常停止；GPU 3/7 回到 15 MiB/0%，未留监听端口。
- 文档结构断言：通过；四份文档非空且必需章节齐全。
- `.gitignore` 命中检查：通过；`.env`、私钥、credentials、本机配置目录、模型权重、runs、checkpoints、raw data 均被忽略。
- 新增文档与 `.gitignore` 敏感值模式扫描：通过，未发现 AWS access key、私钥块、OpenAI/GitHub token 形式的值。

## Acceptance Checklist

- [x] 已确认 Codex 工作区在本地 WSL。
- [x] 已检查本地 Recovery-Bench。
- [x] 已检查本地 Docker 环境。
- [x] 已确认 Harbor 实际位于本地 WSL 父项目 `.venv`，通过本地 Docker backend 运行。
- [x] 已分析 Agent 执行日志。
- [x] 已分析 Checkpoint / Restore 能力。
- [x] 已分析 Branch Execution 能力。
- [x] 已检查远程 GPU；实际为 8 × RTX 4090 共享资源，每次动态选两张空闲卡。
- [x] 已检查远程 Conda、CUDA、磁盘和模型路径；按后续授权完成隔离 vLLM 环境与 Qwen3-8B 下载、静态验证。
- [x] 已实测本地到远程 SSH/HTTPS/TLS；SSH tunnel、严格 CA、API Key 和双 Endpoint 推理通过。
- [x] 已明确 Recovery-Bench 复用范围。
- [x] 已生成三份审计/架构文档。
- [x] 已生成 Day 1 日报。
- [x] 没有修改或破坏现有 Recovery-Bench 环境。

## Known Issues

1. SSH 目标是 Ubuntu 22.04 Docker 容器；宿主机 OS 未审计，但用户已确认这是 8 卡共享实验室资源入口。
2. GPU 空闲状态随其他用户任务变化；每次启动前必须重新检查，不能依赖 Day 1 的 GPU 2/3/7 瞬时状态。
3. `flashinfer-jit-cache` 因远端 GitHub 不可达未安装；正式启动必须保留 `VLLM_USE_FLASHINFER_SAMPLER=0`，除非以后重新验证 kernel。
4. SSH 4035 偶发握手超时；隧道管理需要幂等启动、健康检查和受控重连，避免重复启动远端模型进程。
5. 本次仅验证 8K/单并发/eager；原生 40K、并发和吞吐仍未知。
6. 远程 overlay 使用率 98%、swap 100%，高资源操作有风险。
7. 本地 `conda` 不可用；Day 2 环境方案仍需确认。
8. 普通执行上下文没有本地 Docker socket 权限；本次未修改系统权限。
9. Recovery-Bench 外部仓库已有大量未提交产物修改，后续 Adapter 必须保持只读。
10. Recovery-Bench/Harbor 没有研究所需的完整 Checkpoint/Restore/Branch protocol。

## Remaining Work

Day 1 remote Gate 已通过。Day 2 开始前或开始时需要：

1. 重新检查共享 GPU，动态选择两张空闲卡并记录 Worker/物理 GPU 映射。
2. 用已验证参数启动两个 vLLM 服务和 SSH 隧道；为连接抖动实现幂等检查，不能把 SSH timeout 当作命令未执行。
3. 保留严格 TLS、CA bundle、API Key 和 `VLLM_USE_FLASHINFER_SAMPLER=0`；密钥继续放在仓库外。
4. 确认本地 Python venv/Conda 与 Docker socket 权限方案，再开始 smolagents 客户端和本地 Tool Call。
5. 继续监控远程 98% 磁盘使用率和满 swap。

## Status: CONDITIONAL PASS

本地与远程审计均已执行；隔离 vLLM 环境、Qwen3-8B、GPU 3/7 双实例、本地 SSH 隧道、严格 TLS/API Key 和两个短生成均通过真实测试。服务在 smoke 验收后主动停止并释放共享 GPU。未完成的 JIT cache、隧道持久化、本地 Day 2 Python/Docker 权限属于后续工程项，因此 Day 1 记为 CONDITIONAL PASS，而非 PASS。

## Git Commit

未提交。项目已初始化为 Git 仓库，保留给用户检查后决定首次 commit。
