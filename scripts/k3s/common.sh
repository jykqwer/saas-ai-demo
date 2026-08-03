#!/usr/bin/env bash

# k3s 发布脚本共享的路径、命令探测和前置检查。
set -Eeuo pipefail

readonly K3S_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${K3S_SCRIPT_DIR}/../.." && pwd)"
readonly APP_NAMESPACE="${APP_NAMESPACE:-saas-ai-demo}"
readonly OLD_NAMESPACE="${OLD_NAMESPACE:-demo}"

if [[ -n "${KUBECTL:-}" ]]; then
  # KUBECTL 允许填写 "kubectl" 或 "sudo k3s kubectl"；不支持带引号的复杂 shell 表达式。
  read -r -a KUBECTL_COMMAND <<<"${KUBECTL}"
elif command -v k3s >/dev/null 2>&1; then
  if [[ "${EUID}" -eq 0 ]]; then
    KUBECTL_COMMAND=(k3s kubectl)
  else
    KUBECTL_COMMAND=(sudo k3s kubectl)
  fi
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL_COMMAND=(kubectl)
else
  echo "错误：未找到 kubectl 或 k3s；请在远端 k3s 服务器执行此脚本。" >&2
  exit 1
fi

k() {
  "${KUBECTL_COMMAND[@]}" "$@"
}

require_cluster_access() {
  k get nodes >/dev/null
}

require_namespace_secret() {
  local secret_name="$1"
  if ! k get secret "${secret_name}" -n "${APP_NAMESPACE}" >/dev/null 2>&1; then
    echo "错误：${APP_NAMESPACE} namespace 中缺少 Secret ${secret_name}。" >&2
    echo "请先运行 scripts/k3s/create-secrets.sh。" >&2
    exit 1
  fi
}
