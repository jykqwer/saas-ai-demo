# 生产镜像：Node 构建静态资源，nginx 托管并反向代理 /api 到后端服务。
FROM node:22 AS build

WORKDIR /app

COPY package*.json ./

RUN npm config set registry https://registry.npmmirror.com

RUN npm ci

COPY . .

RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
