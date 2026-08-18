# keyframe 算子镜像。tag 即模型版本 —— 模型版本管理就是镜像 tag 管理。
FROM python:3.12-slim

WORKDIR /app

# 算子只依赖 contract + algo_common，不装 Platform/Scheduler 的任何东西
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/algo_common /app/algo_common
COPY operators/keyframe /app/operators/keyframe

ENV PYTHONPATH=/app

# Scheduler 通过环境变量注入 RDH_JOB_ID / RDH_EPISODE_ID / RDH_INPUT_KEY / RDH_OUTPUT_PREFIX
ENTRYPOINT ["python", "-m", "operators.keyframe.main"]
