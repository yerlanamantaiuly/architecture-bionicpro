# Build stage
FROM node:16-alpine as build

WORKDIR /app

ARG REACT_APP_API_URL=http://10.2.67.21:8000
ARG REACT_APP_AUTH_URL=http://10.2.67.21:8001
ENV REACT_APP_API_URL=$REACT_APP_API_URL
ENV REACT_APP_AUTH_URL=$REACT_APP_AUTH_URL

COPY package.json package-lock.json ./
RUN npm install --legacy-peer-deps

COPY . .

RUN npm run build

FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
