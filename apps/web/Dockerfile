# The PatchPilot UI.
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY . .
# The API is reached from the browser, not from this container, so the URL must
# point at the host's published port rather than a compose service name.
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8080
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production
COPY --from=build /web/.next ./.next
COPY --from=build /web/node_modules ./node_modules
COPY --from=build /web/package.json ./package.json
COPY --from=build /web/next.config.js ./next.config.js
EXPOSE 3000
CMD ["npm", "run", "start"]
