FROM nginx:1.27-alpine

COPY . /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY config.js.template /etc/nginx/templates/config.js.template

ENV API_BASE=http://localhost:8001
ENV NGINX_ENVSUBST_OUTPUT_DIR=/usr/share/nginx/html

EXPOSE 80
