FROM archlinux

COPY prepare.sh /tmp
RUN ./tmp/prepare.sh

USER e2e
WORKDIR /repo
