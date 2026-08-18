.DEFAULT_GOAL := help
.PHONY: help check demo contract-sync contract-test contract-cov contract-lint contract-gen \
        platform-sync platform-lint scheduler-sync scheduler-lint agent-sync agent-lint \
        algo-sync algo-lint tool-install tool-check web-install web-check \
        testing-sync testing-lint conformance e2e arch-check clean clean-runtime

PY_MODULES := contract platform scheduler agent algo testing

help:  ## 显示可用命令
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---- contract（唯一的质量门槛）----

contract-sync:  ## 安装 contract 依赖
	cd contract && uv sync

contract-test: contract-sync  ## 跑 contract 测试
	cd contract && uv run pytest -v

contract-cov: contract-sync  ## contract 覆盖率（守 80%）
	cd contract && uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=80

contract-lint: contract-sync  ## contract lint + 类型检查
	cd contract && uv run ruff check . && uv run ruff format --check . && uv run mypy src

contract-gen: contract-sync  ## 生成 events/*.json 与 types/contract.ts
	cd contract && uv run python scripts/export_json_schema.py && uv run python scripts/export_ts_types.py

# ---- 骨架模块：只做类型自洽检查 ----

platform-sync:  ## 安装 platform 依赖
	cd platform && uv sync

platform-lint: platform-sync  ## platform lint + 类型检查
	cd platform && uv run ruff check . && uv run mypy app

scheduler-sync:  ## 安装 scheduler 依赖
	cd scheduler && uv sync

scheduler-lint: scheduler-sync  ## scheduler lint + 类型检查
	cd scheduler && uv run ruff check . && uv run mypy src

agent-sync:  ## 安装 agent 依赖
	cd agent && uv sync

agent-lint: agent-sync  ## agent lint + 类型检查
	cd agent && uv run ruff check . && uv run mypy src

algo-sync:  ## 安装 algo 依赖
	cd algo && uv sync

algo-lint: algo-sync  ## algo lint + 类型检查
	cd algo && uv run ruff check . && uv run mypy src

tool-install:  ## 安装 tool 前端依赖
	cd tool && pnpm install

tool-check: tool-install  ## tool 类型检查
	cd tool && pnpm exec tsc --noEmit

web-install:  ## 安装 platform 前端依赖
	cd platform/web && pnpm install

web-check: web-install  ## platform 前端类型检查
	cd platform/web && pnpm exec tsc --noEmit

# ---- Testing（横向质量保障）----

testing-sync:  ## 安装 testing 依赖
	cd testing && uv sync

conformance: testing-sync  ## 跨模块契约一致性（含依赖铁律，无需起服务）
	cd testing && uv run pytest contract_checks -q

e2e: testing-sync  ## 端到端流程测试
	cd testing && uv run pytest e2e -q

testing-lint: testing-sync  ## testing lint + 类型检查
	cd testing && uv run ruff check . && uv run mypy contract_checks e2e

# ---- Demo ----

demo: testing-sync  ## 跑端到端 MVP demo（8 条交互真跑一遍）
	uv run --project testing python scripts/demo.py

# ---- 聚合 ----

arch-check:  ## 校验依赖铁律：模块间不得直接 import
	@bash scripts/arch_check.sh

check: contract-lint contract-cov platform-lint scheduler-lint agent-lint algo-lint testing-lint tool-check web-check arch-check conformance e2e  ## 全量检查

clean:  ## 清理缓存与虚拟环境
	rm -rf $(addsuffix /.venv,$(PY_MODULES))
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache \) -prune -exec rm -rf {} +
	rm -rf tool/node_modules platform/web/node_modules

clean-runtime:  ## 清理本地运行数据（DB / 队列 / 对象存储）
	rm -rf .runtime
