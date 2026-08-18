# preannotate 算子镜像。tag 即模型版本 —— 模型版本管理就是镜像 tag 管理。
#
# GPU 算子：动作分段用时序分割网络推理，需要 CUDA。
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 算子只依赖 contract + algo_common，不装 Platform/Scheduler 的任何东西
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY src/algo_common /app/algo_common
COPY operators/preannotate /app/operators/preannotate

ENV PYTHONPATH=/app

# 非 root 运行
RUN useradd --create-home --uid 10001 operator
USER operator

# Scheduler 通过环境变量注入 RDH_JOB_ID / RDH_EPISODE_ID / RDH_INPUT_KEY / RDH_OUTPUT_PREFIX
ENTRYPOINT ["python3", "-m", "operators.preannotate.main"]
