FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31-alpine
# Do not associate this derived image with the upstream base-image repository.
LABEL org.opencontainers.image.source="https://github.com/DamiBeGud/deploy-flow" \
      org.opencontainers.image.url="https://github.com/DamiBeGud/deploy-flow" \
      org.opencontainers.image.revision=""
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
