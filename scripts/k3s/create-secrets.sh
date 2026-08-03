#!/usr/bin/env bash

# 首次部署时创建数据库和外部服务凭据；不会自动轮换已有数据库密码。
set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_cluster_access
k apply -f "${REPO_ROOT}/pods/namespace.yaml"

if k get secret postgres-credentials -n "${APP_NAMESPACE}" >/dev/null 2>&1; then
  echo "postgres-credentials 已存在；为避免现有数据库密码失配，本次不覆盖。"
else
  db_password="$(openssl rand -hex 24)"
  database_url="postgresql+psycopg://saasuser:${db_password}@postgres-service.${APP_NAMESPACE}.svc.cluster.local:5432/saasdb"

  k create secret generic postgres-credentials \
    -n "${APP_NAMESPACE}" \
    --from-literal=POSTGRES_DB=saasdb \
    --from-literal=POSTGRES_USER=saasuser \
    --from-literal=POSTGRES_PASSWORD="${db_password}" \
    --from-literal=DATABASE_URL="${database_url}" \
    --dry-run=client -o yaml | k apply -f -

  unset db_password database_url
  echo "已创建 postgres-credentials。"
fi

if k get secret llm-credentials -n "${APP_NAMESPACE}" >/dev/null 2>&1; then
  read -r -p "llm-credentials 已存在，是否更新？[y/N] " update_llm
  if [[ ! "${update_llm}" =~ ^[Yy]$ ]]; then
    echo "保留现有 llm-credentials。"
    exit 0
  fi
fi

read -r -s -p "LLM API Key（留空进入演示模式）: " llm_api_key
echo
read -r -s -p "Tavily API Key（留空则联网搜索不可用）: " tavily_api_key
echo
read -r -s -p "初始管理员密码（至少 8 位）: " admin_password
echo

if (( ${#admin_password} < 8 )); then
  echo "错误：初始管理员密码至少需要 8 位。" >&2
  exit 1
fi

k create secret generic llm-credentials \
  -n "${APP_NAMESPACE}" \
  --from-literal=LLM_API_KEY="${llm_api_key}" \
  --from-literal=LLM_BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com}" \
  --from-literal=LLM_MODEL="${LLM_MODEL:-deepseek-chat}" \
  --from-literal=TAVILY_API_KEY="${tavily_api_key}" \
  --from-literal=BOOTSTRAP_SUPERUSER_PASSWORD="${admin_password}" \
  --dry-run=client -o yaml | k apply -f -

unset llm_api_key tavily_api_key admin_password
echo "已创建或更新 llm-credentials。请使用新镜像标签运行 deploy.sh，使后端 Pod 读取新 Secret。"
