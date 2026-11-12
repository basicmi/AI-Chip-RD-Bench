FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip verilator yosys ca-certificates make g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY . /work
CMD ["python3", "-m", "chiprd.cli", "all", "--output", "artifacts/docker"]
