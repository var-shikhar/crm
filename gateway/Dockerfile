# Build from the repository root:  docker build -f gateway/Dockerfile .
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY gateway/nginx.conf /etc/nginx/nginx.conf
COPY web/ /usr/share/nginx/html/
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:8080/gateway-healthz || exit 1
