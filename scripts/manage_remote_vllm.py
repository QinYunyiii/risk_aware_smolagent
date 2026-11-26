#!/usr/bin/env python3
"""Idempotently manage only the Day 2 vLLM workers and local SSH tunnel.

The remote start operation is protected by a directory lock and state files.  A
repeated start first validates recorded PIDs, ports, and health, so a local SSH
timeout cannot create duplicate workers.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


REMOTE_ROOT = "/mnt/risk-aware-smolagents"
REMOTE_STATE = f"{REMOTE_ROOT}/logs/vllm/day2"
LOCAL_STATE = Path.home() / ".config/risk-aware-smolagents/day2"
WORKER_PORTS = (8000, 8001)


def ownership_checks(record: dict) -> bool:
    """Same strict, credential-free ownership predicate on both ends of SSH."""
    import re
    worker = record.get('worker_id')
    if worker not in ('worker-0', 'worker-1'):
        return False
    index = int(worker[-1]); port = 8000 + index
    return bool(
        record.get('remote_container_id') == '36f825bcc5da'
        and record.get('remote_user') == 'root'
        and record.get('ubuntu_version') == '22.04'
        and type(record.get('worker_pid')) is int and record['worker_pid'] > 1
        and record.get('alive') is True
        and record.get('worker_process_identity')
        and record.get('worker_command_identity') == 'vllm-' + worker
        and re.fullmatch('[0-9a-f]{64}', record.get('worker_command_fingerprint', ''))
        and record.get('cuda_visible_devices') in (str(index), record.get('gpu_uuid'))
        and record.get('physical_gpu_id') == index
        and re.fullmatch(r'GPU-[0-9a-fA-F-]{36}', record.get('gpu_uuid', ''))
        and record.get('state_gpu_uuid') == record.get('gpu_uuid')
        and record.get('state_pid') == record.get('worker_pid')
        and record.get('state_gpu_id') == index
        and record.get('endpoint') == f'127.0.0.1:{port}'
        and record.get('listener_ownership_verified') is True
        and record.get('model_id') == 'Qwen/Qwen3-8B'
        and record.get('model_path') == '/mnt/risk-aware-smolagents/remote_models/Qwen3-8B')


def worker_ownership_preflight(system=None) -> dict:
    """Read-only extension of Day-2 state/cmdline/GPU ownership checks.

    Discover current listener owners first, then cross-check deployment receipts.
    No HTTP, TLS, directory creation, process signals or GPU 2--7 queries.
    Raw cmdline/environ are used in memory only and never returned.
    """
    import datetime
    import hashlib
    import os
    from pathlib import Path
    import subprocess

    class System:
        def identity(self):
            release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                           if '=' in line)
            return dict(remote_container_id=os.uname().nodename,
                        remote_user='root' if os.getuid() == 0 else str(os.getuid()),
                        ubuntu_version=release.get('VERSION_ID', '').strip('"')
                        if release.get('ID', '').strip('"') == 'ubuntu' else None)

        def gpu_rows(self):
            result = subprocess.run(['nvidia-smi', '--id=0,1', '--query-gpu=index,uuid',
                '--format=csv,noheader,nounits'], capture_output=True, check=True, timeout=5,
                env={k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL') if k in os.environ})
            return {int(row.split(',')[0]): row.split(',')[1].strip()
                    for row in result.stdout.decode('ascii').splitlines()}

        def worker(self, index):
            port = 8000 + index
            # Current socket inode -> current owning PID, not a historical PID.
            inodes = {line.split()[9] for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                      if line.split()[1] == f'0100007F:{port:04X}' and line.split()[3] == '0A'}
            owners = set()
            for base in Path('/proc').iterdir():
                if not base.name.isdigit(): continue
                try:
                    if any(os.readlink(fd) in {'socket:[' + inode + ']' for inode in inodes}
                           for fd in (base / 'fd').iterdir()): owners.add(int(base.name))
                except (FileNotFoundError, PermissionError, ProcessLookupError): continue
            if len(owners) != 1: raise ValueError('ambiguous listener owner')
            pid = owners.pop(); base = Path('/proc') / str(pid)
            before = (base / 'stat').read_text().rsplit(')', 1)[1].split()
            raw = (base / 'cmdline').read_bytes(); args = raw.rstrip(b'\0').decode().split('\0')
            def option(name):
                for i, arg in enumerate(args):
                    if arg == name and i + 1 < len(args): return args[i+1]
                    if arg.startswith(name + '='): return arg.split('=', 1)[1]
                return None
            env = dict(part.split(b'=', 1) for part in (base / 'environ').read_bytes().split(b'\0')
                       if b'=' in part)
            # Only the single CUDA selector leaves memory; never serialize environ.
            cuda = env.get(b'CUDA_VISIBLE_DEVICES', b'').decode('ascii')
            state = dict(line.split('=', 1) for line in Path(
                f'/mnt/risk-aware-smolagents/logs/vllm/day2/worker-{index}.state').read_text().splitlines()
                         if '=' in line)
            after = (base / 'stat').read_text().rsplit(')', 1)[1].split()
            command_ok = any('vllm' in arg for arg in args) and option('--port') == str(port)
            return dict(worker_pid=pid, alive=after[0] not in ('Z', 'X') and before[19] == after[19],
                worker_process_identity=after[19],
                worker_command_identity=f'vllm-worker-{index}' if command_ok else None,
                worker_command_fingerprint=hashlib.sha256(raw).hexdigest(),
                cuda_visible_devices=cuda, state_pid=int(state['pid']), state_gpu_id=int(state['gpu']),
                state_gpu_uuid=state['gpu_uuid'], listener_ownership_verified=bool(inodes),
                endpoint=f'127.0.0.1:{port}' if int(state['port']) == port else None,
                model_id=option('--served-model-name'), model_path=option('--model') or
                (args[args.index('serve') + 1] if 'serve' in args and args.index('serve') + 1 < len(args) else None))

    system = system if system is not None else System()
    records = []
    try:
        identity = system.identity()
        if (identity.get('remote_container_id') != '36f825bcc5da'
                or identity.get('remote_user') != 'root' or identity.get('ubuntu_version') != '22.04'):
            raise ValueError('identity rejected')
        gpus = system.gpu_rows()
        if set(gpus) != {0, 1} or len(set(gpus.values())) != 2: raise ValueError('GPU mapping rejected')
        for index in (0, 1):
            record = dict(identity, worker_id=f'worker-{index}', physical_gpu_id=index,
                          gpu_uuid=gpus[index], **system.worker(index))
            record['ownership_verified'] = ownership_checks(record)
            records.append(record)
            if not record['ownership_verified']: raise ValueError('worker rejected')
        return dict(passed=True, workers=records, observed_at=datetime.datetime.now(
            datetime.timezone.utc).isoformat(), source='live_proc_and_day2_state_and_scoped_nvidia_smi')
    except Exception:
        return dict(passed=False, workers=records, error='OWNERSHIP_PREFLIGHT_REJECTED')


def ownership_program() -> str:
    """Send this exact existing deployment preflight through remote(), once."""
    return (inspect.getsource(ownership_checks) + '\n' + inspect.getsource(worker_ownership_preflight)
            + '\nimport json\nresult=worker_ownership_preflight()\n'
              'print(json.dumps(result,sort_keys=True))\nraise SystemExit(0 if result["passed"] else 90)\n')


def ssh_base() -> list[str]:
    names = (
        "RISK_AGENT_SSH_HOST",
        "RISK_AGENT_SSH_PORT",
        "RISK_AGENT_SSH_USER",
        "RISK_AGENT_SSH_KEY",
    )
    values = {name: os.environ.get(name) for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Missing SSH environment variables: {', '.join(missing)}")
    host = values["RISK_AGENT_SSH_HOST"]
    port = values["RISK_AGENT_SSH_PORT"]
    user = values["RISK_AGENT_SSH_USER"]
    key = values["RISK_AGENT_SSH_KEY"]
    return [
        "ssh", "-F", "/dev/null", "-i", key, "-p", port,
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "PreferredAuthentications=publickey",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={Path.home() / '.ssh/known_hosts'}",
        "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", f"{user}@{host}",
    ]


REMOTE_SCRIPT = r'''set -eu
action="$1"
requested_gpus="${2:-}"
root=/mnt/risk-aware-smolagents
state="$root/logs/vllm/day2"
model="$root/remote_models/Qwen3-8B"
envbin=/root/miniconda3/envs/risk-agent-vllm/bin
tls=/root/.config/risk-agent/tls
mkdir -p "$state"

port_open() {
  "$envbin/python" - "$1" <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(0.5)
try:
    raise SystemExit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
finally:
    s.close()
PY
}

is_ours() {
  worker="$1"; sf="$state/worker-$worker.state"
  [ -s "$sf" ] || return 1
  pid=$(sed -n 's/^pid=//p' "$sf")
  port=$(sed -n 's/^port=//p' "$sf")
  [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] || return 1
  cmd=$(tr '\000' ' ' < "/proc/$pid/cmdline")
  case "$cmd" in
    *"$model"*"--port $port"*) return 0 ;;
    *) return 1 ;;
  esac
}

healthy() {
  worker="$1"; sf="$state/worker-$worker.state"
  is_ours "$worker" || return 1
  port=$(sed -n 's/^port=//p' "$sf")
  key=$(cat "$tls/vllm_api_key")
  curl --silent --show-error --fail --max-time 5 --cacert "$tls/ca.crt" \
    -H "Authorization: Bearer $key" "https://localhost:$port/v1/models" >/dev/null
}

show_status() {
  hostname
  nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu \
    --format=csv,noheader
  for worker in 0 1; do
    sf="$state/worker-$worker.state"
    if is_ours "$worker"; then
      pid=$(sed -n 's/^pid=//p' "$sf")
      port=$(sed -n 's/^port=//p' "$sf")
      gpu=$(sed -n 's/^gpu=//p' "$sf")
      if healthy "$worker"; then health=healthy; else health=starting_or_unhealthy; fi
      echo "worker-$worker pid=$pid port=$port gpu=$gpu state=$health"
    elif [ -s "$sf" ]; then
      echo "worker-$worker stale_state"
    else
      echo "worker-$worker absent"
    fi
  done
}

start_all() {
  requested="$1"
  lock="$state/start.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    echo "another Day 2 start/stop operation holds the lock" >&2; exit 20
  fi
  trap 'rmdir "$lock" 2>/dev/null || true' EXIT HUP INT TERM

  all_healthy=1
  for worker in 0 1; do healthy "$worker" || all_healthy=0; done
  if [ "$all_healthy" -eq 1 ]; then
    if [ -n "$requested" ]; then
      expected0=${requested%%,*}
      expected1=${requested#*,}
      actual0=$(sed -n 's/^gpu=//p' "$state/worker-0.state")
      actual1=$(sed -n 's/^gpu=//p' "$state/worker-1.state")
      if [ "$actual0,$actual1" != "$expected0,$expected1" ]; then
        echo "healthy workers use GPUs $actual0,$actual1, not requested $expected0,$expected1" >&2
        exit 27
      fi
    fi
    echo "both Day 2 workers already healthy; no launch performed"
    show_status
    return 0
  fi

  for worker in 0 1; do
    if is_ours "$worker"; then
      echo "worker-$worker exists but is not healthy; refusing duplicate launch" >&2
      exit 21
    fi
  done
  for port in 8000 8001; do
    if port_open "$port"; then
      echo "port $port is occupied by an unowned service; refusing launch" >&2
      exit 22
    fi
  done

  snapshot1="$state/gpus-before-1.csv"
  snapshot2="$state/gpus-before-2.csv"
  busy_uuids="$state/compute-apps-before.txt"
  nvidia-smi --query-gpu=index,uuid,memory.used,utilization.gpu --format=csv,noheader,nounits > "$snapshot1"
  sleep 3
  nvidia-smi --query-gpu=index,uuid,memory.used,utilization.gpu --format=csv,noheader,nounits > "$snapshot2"
  nvidia-smi --query-compute-apps=gpu_uuid --format=csv,noheader,nounits 2>/dev/null \
    | sort -u > "$busy_uuids" || true
  candidates=$(awk -F', *' 'NR==FNR {if ($3 <= 100 && $4 == 0) ok[$1]=$2; next}
    ($3 <= 100 && $4 == 0 && ok[$1]==$2) {print $1 ":" $2}' "$snapshot1" "$snapshot2")
  free=$(printf '%s\n' "$candidates" | while IFS=: read -r gpu uuid; do
    [ -n "$uuid" ] || continue
    if ! grep -Fqx "$uuid" "$busy_uuids"; then echo "$gpu:$uuid"; fi
  done)
  count=$(printf '%s\n' "$free" | awk 'NF {n++} END {print n+0}')
  if [ -n "$requested" ]; then
    case "$requested" in
      [0-9]*,[0-9]*) ;;
      *) echo "requested GPU IDs must be two comma-separated integers" >&2; exit 28 ;;
    esac
    requested0=${requested%%,*}
    requested1=${requested#*,}
    case "$requested0$requested1" in *[!0-9]*)
      echo "requested GPU IDs must be two comma-separated integers" >&2; exit 28 ;;
    esac
    if [ "$requested0" = "$requested1" ]; then
      echo "requested GPU IDs must be distinct" >&2; exit 28
    fi
    pair=""
    for gpu in "$requested0" "$requested1"; do
      selected=$(printf '%s\n' "$free" | awk -F: -v gpu="$gpu" '$1 == gpu {print; exit}')
      if [ -z "$selected" ]; then
        echo "requested GPU $gpu is not free in both samples or has a compute process" >&2
        show_status
        exit 23
      fi
      pair=$(printf '%s\n%s' "$pair" "$selected")
    done
    pair=$(printf '%s\n' "$pair" | awk 'NF')
  else
    if [ "$count" -lt 2 ]; then
      echo "need two GPUs with no compute process, <=100 MiB, and 0% utilization in two samples; found $count" >&2
      show_status
      exit 23
    fi
    pair=$(printf '%s\n' "$free" | sed -n '1,2p')
  fi

  worker=0
  launch_id=$(date -u +%Y%m%dT%H%M%SZ)
  printf '%s\n' "$pair" | while IFS=: read -r gpu uuid; do
    port=$((8000 + worker))
    log="$state/worker-$worker-$launch_id.log"
    sf="$state/worker-$worker.state"
    api_key=$(cat "$tls/vllm_api_key")
    nohup env \
      CUDA_VISIBLE_DEVICES="$gpu" \
      HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
      VLLM_API_KEY="$api_key" VLLM_USE_FLASHINFER_SAMPLER=0 \
      PATH="$envbin:/usr/local/cuda/bin:/usr/bin:/bin" \
      "$envbin/vllm" serve "$model" \
      --served-model-name Qwen/Qwen3-8B --host 127.0.0.1 --port "$port" \
      --dtype bfloat16 --max-model-len 8192 --gpu-memory-utilization 0.90 \
      --max-num-seqs 1 --enforce-eager --enable-auto-tool-choice \
      --tool-call-parser hermes --ssl-keyfile "$tls/server.key" \
      --ssl-certfile "$tls/server.crt" --disable-uvicorn-access-log \
      >"$log" 2>&1 </dev/null &
    pid=$!
    umask 077
    { echo "pid=$pid"; echo "port=$port"; echo "gpu=$gpu"; echo "gpu_uuid=$uuid"; echo "log=$log"; } > "$sf"
    echo "launched worker-$worker pid=$pid port=$port gpu=$gpu uuid=$uuid"
    worker=$((worker + 1))
  done
  echo "launch recorded; use wait-health before creating the tunnel"
}

wait_health() {
  attempts=0
  while [ "$attempts" -lt 120 ]; do
    good=0
    for worker in 0 1; do healthy "$worker" && good=$((good + 1)) || true; done
    if [ "$good" -eq 2 ]; then show_status; return 0; fi
    for worker in 0 1; do
      if [ -s "$state/worker-$worker.state" ] && ! is_ours "$worker"; then
        echo "worker-$worker exited during startup" >&2
        log=$(sed -n 's/^log=//p' "$state/worker-$worker.state")
        [ -n "$log" ] && tail -n 40 "$log" >&2 || true
        return 24
      fi
    done
    attempts=$((attempts + 1)); sleep 5
  done
  show_status; return 25
}

stop_all() {
  lock="$state/start.lock"
  if ! mkdir "$lock" 2>/dev/null; then echo "operation lock held" >&2; exit 20; fi
  trap 'rmdir "$lock" 2>/dev/null || true' EXIT HUP INT TERM
  for worker in 0 1; do
    sf="$state/worker-$worker.state"
    if is_ours "$worker"; then
      pid=$(sed -n 's/^pid=//p' "$sf")
      echo "sending TERM to owned worker-$worker pid=$pid"
      kill -TERM "$pid"
    else
      echo "worker-$worker has no validated owned process"
    fi
  done
  attempts=0
  while [ "$attempts" -lt 60 ]; do
    alive=0
    for worker in 0 1; do is_ours "$worker" && alive=$((alive + 1)) || true; done
    [ "$alive" -eq 0 ] && break
    attempts=$((attempts + 1)); sleep 2
  done
  for worker in 0 1; do
    if is_ours "$worker"; then echo "owned worker-$worker did not stop" >&2; exit 26; fi
  done
  release="$state/gpus-after-stop-$(date -u +%Y%m%dT%H%M%SZ).csv"
  release_apps="$state/compute-apps-after-stop-$(date -u +%Y%m%dT%H%M%SZ).csv"
  nvidia-smi --query-gpu=index,uuid,memory.used,utilization.gpu \
    --format=csv,noheader,nounits > "$release"
  nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory \
    --format=csv,noheader,nounits > "$release_apps" 2>/dev/null || true
  for worker in 0 1; do
    sf="$state/worker-$worker.state"
    if [ -s "$sf" ]; then
      uuid=$(sed -n 's/^gpu_uuid=//p' "$sf")
      echo "release-check worker-$worker uuid=$uuid"
      grep -F ",$uuid," "$release" || grep -F "$uuid" "$release" || true
      if grep -Fq "$uuid" "$release_apps"; then
        echo "release-check warning: a compute process is present after stop (may be a new owner)"
        grep -F "$uuid" "$release_apps" || true
      else
        echo "release-check no compute process remains"
      fi
    fi
    rm -f "$sf"
  done
  show_status
}

case "$action" in
  status) show_status ;;
  start) start_all "$requested_gpus" ;;
  wait-health) wait_health ;;
  stop) stop_all ;;
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac
'''


def remote(
    action: str, timeout: float = 60, gpu_ids: str | None = None,
    *, ssh_command: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if action == 'ownership-readonly':
        command = (ssh_command or ssh_base()) + ['python3', '-B', '-']
        return subprocess.run(command, input=ownership_program(), text=True,
                              capture_output=True, timeout=timeout, check=False)
    command = ssh_base() + ["bash", "-s", "--", action, gpu_ids or ""]
    return subprocess.run(
        command, input=REMOTE_SCRIPT, text=True, capture_output=True,
        timeout=timeout, check=False,
    )


def print_result(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _control_command(control_path: Path, operation: str) -> list[str]:
    base = ssh_base()
    target = base[-1]
    return base[:-1] + ["-S", str(control_path), "-O", operation, target]


def _control_check(control_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _control_command(control_path, "check"),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _close_control(control_path: Path) -> None:
    check = _control_check(control_path)
    if check.returncode == 0:
        result = subprocess.run(
            _control_command(control_path, "exit"),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"refusing unverified tunnel stop: {(result.stdout + result.stderr).strip()}"
            )
    control_path.unlink(missing_ok=True)


def _record_control_pid(control_path: Path, pid_path: Path, detail: str) -> str:
    match = re.search(r"pid=(\d+)", detail)
    pid = match.group(1) if match else "unknown"
    pid_path.write_text(f"{pid}\n", encoding="utf-8")
    pid_path.chmod(0o600)
    return pid


def tunnel_start() -> None:
    LOCAL_STATE.mkdir(parents=True, exist_ok=True)
    legacy_control = LOCAL_STATE / "tunnel.sock"
    legacy_pid = LOCAL_STATE / "tunnel.pid"
    if legacy_control.exists():
        _close_control(legacy_control)
        for _ in range(50):
            if not any(port_open(port) for port in WORKER_PORTS):
                break
            time.sleep(0.1)
    legacy_pid.unlink(missing_ok=True)

    base = ssh_base()
    target = base[-1]
    for worker, port in enumerate(WORKER_PORTS):
        control_path = LOCAL_STATE / f"tunnel-{worker}.sock"
        pid_path = LOCAL_STATE / f"tunnel-{worker}.pid"
        if control_path.exists():
            check = _control_check(control_path)
            if check.returncode == 0 and port_open(port):
                detail = (check.stdout + check.stderr).strip()
                print(f"Worker {worker} tunnel already active: {detail}")
                continue
            _close_control(control_path)
            pid_path.unlink(missing_ok=True)
        elif pid_path.exists():
            pid_path.unlink()

        command = base[:-1] + [
            "-o", "ExitOnForwardFailure=yes", "-M", "-S", str(control_path),
            "-f", "-N", "-L", f"{port}:127.0.0.1:{port}", target,
        ]
        try:
            launch = subprocess.run(
                command, text=True, capture_output=True, timeout=120, check=False
            )
        except subprocess.TimeoutExpired as exc:
            check = _control_check(control_path) if control_path.exists() else None
            if check is not None and check.returncode == 0 and port_open(port):
                detail = check.stdout + check.stderr
                pid = _record_control_pid(control_path, pid_path, detail)
                print(
                    f"Worker {worker} tunnel launch timed out locally but is healthy pid={pid}"
                )
                continue
            raise RuntimeError(
                f"Worker {worker} tunnel launch timed out and no healthy ControlMaster was found"
            ) from exc
        if launch.returncode != 0:
            raise RuntimeError(
                f"Worker {worker} SSH tunnel failed: "
                f"{(launch.stdout + launch.stderr).strip()}"
            )
        time.sleep(2)
        check = _control_check(control_path)
        if check.returncode != 0 or not port_open(port):
            raise RuntimeError(
                f"Worker {worker} SSH tunnel health check failed: "
                f"{(check.stdout + check.stderr).strip()}"
            )
        detail = check.stdout + check.stderr
        pid = _record_control_pid(control_path, pid_path, detail)
        print(f"Worker {worker} ControlMaster tunnel started pid={pid} port={port}")


def tunnel_stop() -> None:
    controls = [LOCAL_STATE / "tunnel.sock"] + [
        LOCAL_STATE / f"tunnel-{worker}.sock" for worker in range(len(WORKER_PORTS))
    ]
    existing = [path for path in controls if path.exists()]
    if not existing:
        for pid_path in LOCAL_STATE.glob("tunnel*.pid"):
            pid_path.unlink(missing_ok=True)
        print("Day 2 tunnel absent")
        return
    for control_path in existing:
        _close_control(control_path)
    for _ in range(50):
        if not any(port_open(port) for port in WORKER_PORTS):
            break
        time.sleep(0.1)
    for pid_path in LOCAL_STATE.glob("tunnel*.pid"):
        pid_path.unlink(missing_ok=True)
    print("Day 2 tunnel stopped")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "start", "wait-health", "stop", "tunnel-start", "tunnel-stop"))
    parser.add_argument(
        "--gpu-ids",
        help="Two explicitly reserved physical GPU IDs, for example 0,1 (start only)",
    )
    args = parser.parse_args()
    if args.gpu_ids and args.action != "start":
        parser.error("--gpu-ids is only valid with start")
    if args.action == "tunnel-start":
        try:
            tunnel_start()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 31
        return 0
    if args.action == "tunnel-stop":
        try:
            tunnel_stop()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 31
        return 0
    try:
        timeout = 660 if args.action == "wait-health" else 180 if args.action == "start" else 120
        result = remote(args.action, timeout=timeout, gpu_ids=args.gpu_ids)
    except subprocess.TimeoutExpired:
        print("SSH timed out; remote outcome is unknown. Re-run status before any start retry.", file=sys.stderr)
        return 30
    print_result(result)
    if result.returncode == 255:
        print(
            "SSH transport failed; remote outcome is treated as unknown. "
            "Re-run status before any start retry.",
            file=sys.stderr,
        )
        return 30
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
