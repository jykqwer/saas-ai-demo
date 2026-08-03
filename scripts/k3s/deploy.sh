#!/usr/bin/env bash

# 部署新数据库、执行迁移并发布应用；此阶段不会修改旧 zhaocai 的入口或数据。
set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"

if [[ $# -ne 1 ]]; then
  echo "用法：$0 <image-tag>" >&2
  exit 1
fi

readonly TAG="$1"
readonly IMAGE_OWNER="${IMAGE_OWNER:-jykqwer}"

if [[ ! "${TAG}" =~ ^[a-z0-9][a-z0-9.-]{0,40}$ ]]; then
  echo "错误：镜像标签只能包含小写字母、数字、点和连字符，且不能超过 41 个字符。" >&2
  exit 1
fi

readonly MIGRATION_JOB="backend-migration-${TAG//./-}"
readonly BACKEND_IMAGE="docker.io/${IMAGE_OWNER}/saas-ai-demo-backend:${TAG}"
readonly FRONTEND_IMAGE="docker.io/${IMAGE_OWNER}/saas-ai-demo-frontend:${TAG}"

require_cluster_access
k apply -f "${REPO_ROOT}/pods/namespace.yaml"
require_namespace_secret postgres-credentials
require_namespace_secret llm-credentials

if ! k get clusterissuer letsencrypt-prod >/dev/null 2>&1; then
  echo "错误：集群中不存在 ClusterIssuer letsencrypt-prod，无法预签发 TLS 证书。" >&2
  exit 1
fi

render_dir="$(mktemp -d)"
trap 'rm -rf -- "${render_dir}"' EXIT

sed \
  -e "s#docker.io/jykqwer/saas-ai-demo-backend:latest#${BACKEND_IMAGE}#g" \
  -e "s#backend-migration-0001#${MIGRATION_JOB}#g" \
  "${REPO_ROOT}/pods/backend-migration.yaml" >"${render_dir}/backend-migration.yaml"

sed \
  -e "s#docker.io/jykqwer/saas-ai-demo-backend:latest#${BACKEND_IMAGE}#g" \
  "${REPO_ROOT}/pods/backend.yaml" >"${render_dir}/backend.yaml"

sed \
  -e "s#docker.io/jykqwer/saas-ai-demo-frontend:latest#${FRONTEND_IMAGE}#g" \
  "${REPO_ROOT}/pods/frontend.yaml" >"${render_dir}/frontend.yaml"

echo "部署 PostgreSQL..."
k apply -f "${REPO_ROOT}/pods/postgres-service.yaml"
k apply -f "${REPO_ROOT}/pods/postgres.yaml"
k rollout status statefulset/postgres -n "${APP_NAMESPACE}" --timeout=180s

if k get job "${MIGRATION_JOB}" -n "${APP_NAMESPACE}" >/dev/null 2>&1; then
  echo "迁移 Job ${MIGRATION_JOB} 已存在，将检查其执行结果。"
else
  k apply -f "${render_dir}/backend-migration.yaml"
fi

echo "等待数据库迁移完成..."
if ! k wait --for=condition=complete "job/${MIGRATION_JOB}" -n "${APP_NAMESPACE}" --timeout=600s; then
  k logs "job/${MIGRATION_JOB}" -n "${APP_NAMESPACE}" --all-containers=true || true
  echo "错误：数据库迁移失败，应用未发布。" >&2
  exit 1
fi
k logs "job/${MIGRATION_JOB}" -n "${APP_NAMESPACE}" --all-containers=true

echo "部署后端和前端..."
k apply -f "${REPO_ROOT}/pods/backend-service.yaml"
k apply -f "${render_dir}/backend.yaml"
k apply -f "${REPO_ROOT}/pods/frontend-service.yaml"
k apply -f "${render_dir}/frontend.yaml"
k rollout status deployment/backend -n "${APP_NAMESPACE}" --timeout=180s
k rollout status deployment/frontend -n "${APP_NAMESPACE}" --timeout=180s

echo "使用现有 ClusterIssuer 预签发新 namespace 的 TLS 证书..."
k apply -f "${REPO_ROOT}/pods/certificate.yaml"
if ! k wait --for=condition=Ready certificate/zhaocaidog-tls -n "${APP_NAMESPACE}" --timeout=300s; then
  k describe certificate zhaocaidog-tls -n "${APP_NAMESPACE}" || true
  echo "错误：证书尚未签发，暂不切换公网入口。请检查 DNS、cert-manager 和 ACME Challenge。" >&2
  exit 1
fi

k get pods,pvc -n "${APP_NAMESPACE}"
echo
echo "新应用和证书已就绪，旧 zhaocai 尚未变更。"
echo "可先执行以下命令验证集群内服务："
echo "  ${KUBECTL_COMMAND[*]} port-forward -n ${APP_NAMESPACE} service/frontend-service 8080:80"
echo "确认后执行：scripts/k3s/cutover.sh"
