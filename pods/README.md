# Kubernetes 部署（k3s）

面向 `saas-ai` namespace 的单副本演示部署。前端（nginx）通过
`backend-service:8000` 反向代理 `/api`，因此浏览器不直接暴露后端。
会话与消息持久化在单副本 PostgreSQL，数据可跨 Pod 重建保留。

## 1. 创建 namespace 与 Secret

```bash
kubectl apply -f pods/namespace.yaml

# 数据库凭据（生成随机密码，含十六进制字符无需 URL 编码）：
K8S_DB_PASSWORD="$(openssl rand -hex 24)"
kubectl -n saas-ai create secret generic postgres-credentials \
  --from-literal=POSTGRES_DB=saasdb \
  --from-literal=POSTGRES_USER=saasuser \
  --from-literal=POSTGRES_PASSWORD="${K8S_DB_PASSWORD}" \
  --from-literal=DATABASE_URL="postgresql+psycopg://saasuser:${K8S_DB_PASSWORD}@postgres-service.saas-ai.svc.cluster.local:5432/saasdb" \
  --dry-run=client -o yaml | kubectl apply -f -
unset K8S_DB_PASSWORD

# LLM 凭据（有 Key 时；留空则后端进入演示模式）：
kubectl -n saas-ai create secret generic llm-credentials \
  --from-literal=LLM_API_KEY="sk-xxxx" \
  --from-literal=LLM_BASE_URL="https://api.deepseek.com" \
  --from-literal=LLM_MODEL="deepseek-chat" \
  --from-literal=TAVILY_API_KEY="tvly-xxxx" \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 2. 构建并推送镜像

```bash
TAG=$(date +%Y%m%d-%H%M%S)
docker build -t docker.io/jykqwer/saas-ai-backend:${TAG} ./backend
docker build -t docker.io/jykqwer/saas-ai-frontend:${TAG} ./frontend
docker push docker.io/jykqwer/saas-ai-backend:${TAG}
docker push docker.io/jykqwer/saas-ai-frontend:${TAG}
```

发布新版本时把 `backend.yaml`、`backend-migration.yaml` 与 `frontend.yaml`
中的镜像标签更新为同一 TAG。

## 3. 按顺序部署（先库、再迁移、后应用）

```bash
kubectl apply -f pods/postgres-service.yaml
kubectl apply -f pods/postgres.yaml
kubectl rollout status statefulset/postgres -n saas-ai --timeout=180s

kubectl apply -f pods/backend-migration.yaml
kubectl wait --for=condition=complete job/backend-migration-0001 -n saas-ai --timeout=600s
kubectl logs -n saas-ai job/backend-migration-0001

kubectl apply -f pods/backend-service.yaml
kubectl apply -f pods/backend.yaml
kubectl rollout status deployment/backend -n saas-ai --timeout=180s

kubectl apply -f pods/frontend-service.yaml
kubectl apply -f pods/frontend.yaml
kubectl rollout status deployment/frontend -n saas-ai --timeout=180s
```

应用启动不会自动修改 Schema；迁移 Job 成功后才部署后端，避免
readiness 只证明数据库可连接、却没证明 Schema 已升级。

## 4. 验证

```bash
kubectl get pods,pvc -n saas-ai

kubectl port-forward -n saas-ai service/frontend-service 8080:80
# 打开 http://localhost:8080

kubectl port-forward -n saas-ai service/backend-service 8000:8000
curl http://localhost:8000/api/v1/health/ready
# 期望 checks 包含 database: ok
```

## 5. 生产注意事项

- 真实 LLM Key 与数据库密码必须走 Secret 管理系统，不要提交到 Git。
- 公网暴露建议通过 Ingress + TLS 接入（当前清单只提供 ClusterIP Service）。
- PostgreSQL 单副本方案无高可用/备份演练，不能直接视为生产数据库方案；
  使用云数据库时删除 `postgres.yaml`/`postgres-service.yaml`，让
  `DATABASE_URL` 指向外部实例。
- 不要把 `DATABASE_URL` 中的换行或空格带进 Secret；单行值，特殊字符需 URL 编码。
- 生产建议把 `LLM_BASE_URL` / `LLM_MODEL` 等非敏感配置放到 ConfigMap。
