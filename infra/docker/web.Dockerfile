FROM node:22-alpine AS builder

WORKDIR /app
ENV NEXT_OUTPUT=standalone \
    NEXT_TELEMETRY_DISABLED=1
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY .npmrc ./
COPY apps/web/package.json ./apps/web/package.json
COPY apps/web ./apps/web
RUN pnpm --filter @commercevision/web... install --frozen-lockfile --ignore-scripts
RUN pnpm --filter @commercevision/web exec node -e "console.log(require.resolve('next/package.json')); console.log(require.resolve('next/dist/pages/_app'))"
RUN pnpm --filter @commercevision/web build

FROM node:22-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 --ingroup nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/public ./apps/web/public
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static

WORKDIR /app/apps/web
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
