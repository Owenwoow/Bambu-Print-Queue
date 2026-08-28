# bpq 镜像：多阶段构建。
#
# 阶段一在 Node 里把前端编成静态文件；阶段二只装 Python 依赖 + 那份静态产物，
# 不带 Node/npm，成品镜像小。详细部署说明见 docs/部署-Docker.md。

FROM node:20-alpine AS frontend
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim AS final
WORKDIR /app

# 用 -e 装（editable）是为了保留仓库目录结构：src/bpq/web/static.py 找前端产物是
# 按 __file__ 相对路径算的（parents[3]/web/dist），装进 site-packages 会破坏这个假设。
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

COPY --from=frontend /app/web/dist ./web/dist

RUN mkdir -p var && useradd -m -u 1000 bpq && chown -R bpq:bpq /app
USER bpq

EXPOSE 8710
VOLUME ["/app/var"]

# 默认端口/是否开 WebUI 由 config.toml 决定；改了端口这条健康检查也要跟着改。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8710/api/health', timeout=3)" || exit 1

ENTRYPOINT ["bpq"]
CMD ["daemon"]
