# docker inspect --format='{{index .RepoDigests 0}}' archlinux
FROM archlinux@sha256:36e14d971d587c5cc7e2c832bd8789b27cabfd75e0be8e4f79bc162468c5043b

COPY prepare.sh /prepare.sh
RUN /prepare.sh

USER e2e
WORKDIR /repo
