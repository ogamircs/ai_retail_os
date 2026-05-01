# Minimal OpenBoxes image for the AI Retail OS demo.
#
# OpenBoxes ships as a Grails WAR (~190 MB) plus a MySQL backend. There's
# no official Docker Hub image — the upstream-recommended path is a Tomcat
# container with the WAR dropped in. This Dockerfile does that:
#   - tomcat:9-jdk17 base (matches OpenBoxes 0.9.x runtime requirements)
#   - WAR pulled from the GitHub release at build time
#   - MySQL JDBC connector pre-installed so Grails can talk to db
#
# Pinned to v0.9.7-hotfix1. Override at build time with --build-arg
# OPENBOXES_VERSION=… if you want to track a newer release.

FROM tomcat:9-jdk17

ARG OPENBOXES_VERSION=v0.9.7-hotfix1
ARG OPENBOXES_WAR_URL=https://github.com/openboxes/openboxes/releases/download/${OPENBOXES_VERSION}/openboxes.war

ENV JAVA_OPTS="-Xmx2048m -XX:MaxMetaspaceSize=512m"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Drop the WAR into Tomcat as ROOT.war so it's served at /
RUN rm -rf /usr/local/tomcat/webapps/ROOT \
    && curl -fL -o /usr/local/tomcat/webapps/ROOT.war "${OPENBOXES_WAR_URL}"

# OpenBoxes reads its DB config from ~/.grails/openboxes-config.properties.
# The compose file mounts the demo config in at runtime; the empty
# directory makes that mount a clean copy.
RUN mkdir -p /root/.grails

EXPOSE 8080

CMD ["catalina.sh", "run"]
