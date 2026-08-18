# anomaly 算子镜像。tag 即模型版本 —— 模型版本管理就是镜像 tag 管理。
#
# GPU 算子：异常检测用自编码器按重构误差判异常，需要 CUDA。
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 算子只依赖 contract + algo_common，不装 Platform/Scheduler 的任何东西
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY src/algo_common /app/algo_common
COPY operators/anomaly /app/operators/anomaly

ENV PYTHONPATH=/app

# 非 root 运行
RUN useradd --create-home --uid 10001 operator
USER operator

ENTRYPOINT ["python3", "-m", "operators.anomaly.main"]
