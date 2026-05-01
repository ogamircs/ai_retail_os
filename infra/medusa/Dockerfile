# Minimal Medusa v2 image for the AI Retail OS demo.
#
# Medusa doesn't ship an official Docker image — recommended path is to
# bake your own off node + the medusa-starter template. This Dockerfile
# clones the starter at build time so the container is reproducible
# without needing a host-side Node toolchain.
#
# Pinned to node:22-alpine (Medusa v2 minimum is Node 20; 22 LTS is the
# upstream-recommended runtime).
FROM node:22-alpine

# python3 + make + g++ — required by some Medusa transitive deps that
# build native node-gyp modules at install time.
# git — used to clone the starter at build time.
RUN apk add --no-cache git python3 make g++ bash curl

WORKDIR /app

# Pin the starter repo + ref so re-builds are reproducible. Override at
# build time with --build-arg if you want to track a newer ref.
ARG MEDUSA_STARTER_REPO=https://github.com/medusajs/medusa-starter-default.git
ARG MEDUSA_STARTER_REF=master

RUN git clone --depth 1 --branch "${MEDUSA_STARTER_REF}" \
        "${MEDUSA_STARTER_REPO}" . \
    && rm -rf .git \
    && yarn install --frozen-lockfile \
    # `medusa start` requires a prior `medusa build` to produce
    # .medusa/server (the compiled server entrypoint and admin bundle).
    # Without it, the container restart-loops on "Could not find
    # index.html — run 'medusa build'" before bootstrap can land.
    # `medusa build` doesn't need a database; it's a pure compile step
    # over project files, so it's safe at image build time.
    && npx medusa build \
    && yarn cache clean

EXPOSE 9000

# Default command runs the prebuilt server. Bootstrap.sh runs
# db:migrate + user create *before* this comes up so the server starts
# cleanly. For dev / demo only — production deployments should use the
# upstream-recommended Dockerfile with --production install.
CMD ["yarn", "start"]
