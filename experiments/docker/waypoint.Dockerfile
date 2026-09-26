FROM golang:1.25-bookworm

RUN printf 'deb http://deb.debian.org/debian sid main\n' \
        > /etc/apt/sources.list.d/sid.list \
    && apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -t sid \
        criu procps \
    && rm -rf /var/lib/apt/lists/*

ENV GOPROXY=https://goproxy.cn,direct
ENV GOFLAGS=-buildvcs=false
