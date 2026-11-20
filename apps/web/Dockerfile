FROM registry.access.redhat.com/ubi9/nodejs-22:latest AS build

WORKDIR /opt/app-root/src

COPY --chown=1001:0 package.json package-lock.json ./
RUN npm ci

COPY --chown=1001:0 . .
RUN npm run build


FROM registry.access.redhat.com/ubi9/nginx-124:latest

COPY --chown=1001:0 nginx.conf /opt/app-root/etc/nginx.default.d/app.conf
COPY --chown=1001:0 --from=build /opt/app-root/src/dist/ /opt/app-root/src/

USER 1001
EXPOSE 8080

CMD ["/usr/libexec/s2i/run"]
