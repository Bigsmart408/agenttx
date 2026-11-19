FROM agenttx-tb-base:hello-world-a678ece7805b

USER root
RUN sed -i \
      -e 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
      -e 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       bash bubblewrap ca-certificates coreutils curl findutils git procps strace util-linux \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT []
CMD ["/bin/bash"]
