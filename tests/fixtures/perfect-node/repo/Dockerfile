FROM node:20.11-slim
WORKDIR /app
COPY . .
RUN npm ci --ignore-scripts
CMD ["npm", "test"]
