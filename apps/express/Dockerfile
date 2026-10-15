FROM node:22.14.0-bookworm-slim
WORKDIR /app
COPY apps/express/package*.json ./
RUN npm ci --omit=dev
COPY apps/node-common.mjs ./server.mjs
ENV NODE_ENV=production FRAMEWORK=express
EXPOSE 8080
CMD ["node", "server.mjs"]
