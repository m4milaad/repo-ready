FROM node:20.18.1-bookworm-slim

WORKDIR app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .

CMD ["node", "index.js"]
