#!/usr/bin/env bash

# 删除旧项目对同一域名的路由，并把 Traefik 公网入口切换到新应用。
set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_cluster_access

k rollout status deployment/backend -n "${APP_NAMESPACE}" --timeout=30s
k rollout status deployment/frontend -n "${APP_NAMESPACE}" --timeout=30s
k wait --for=condition=Ready certificate/zhaocaidog-tls -n "${APP_NAMESPACE}" --timeout=30s

echo "移除旧 namespace 中占用 zhaocaidog.space 的 Ingress..."
if k get namespace "${OLD_NAMESPACE}" >/dev/null 2>&1; then
  k delete ingress demo-ingress www-redirect \
    -n "${OLD_NAMESPACE}" \
    --ignore-not-found
fi

# 早期 zhaocai 清单曾在 default namespace 创建同域名入口；保留它会与新入口竞争路由。
k delete ingress nginx-ingress \
  -n default \
  --ignore-not-found

echo "应用 saas-ai-demo 的 HTTPS 和域名入口..."
k apply -f "${REPO_ROOT}/pods/https-redirect.yaml"
k apply -f "${REPO_ROOT}/pods/ingress.yaml"
k apply -f "${REPO_ROOT}/pods/www-redirect.yaml"
k get ingress -n "${APP_NAMESPACE}"

echo
echo "入口已切换。请验证："
echo "  curl -I https://zhaocaidog.space"
echo "  curl -fsS https://zhaocaidog.space/api/v1/health/ready"
echo
echo "确认业务正常后，可停止旧项目：scripts/k3s/remove-zhaocai.sh stop"
echo "确认不再需要旧数据库后，可永久删除：scripts/k3s/remove-zhaocai.sh delete --confirm-delete-demo"
