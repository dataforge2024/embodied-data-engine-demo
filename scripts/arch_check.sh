#!/usr/bin/env bash
# 校验依赖铁律：唯一允许的跨模块依赖是 contract（包名 rdh_contract）。
#
# 各模块的顶层包名：
#   contract -> rdh_contract   platform -> app        scheduler -> scheduler
#   agent    -> agent          algo     -> algo_common
#
# 注意：工作区根目录下的 platform/ 会遮蔽 Python stdlib 的 platform 模块。
# 各模块始终以自身目录为 cwd 运行，因此不受影响；但不要从工作区根目录跑 Python。

set -uo pipefail
cd "$(dirname "$0")/.."

fail=0

check() {
  local module_dir="$1" forbidden="$2" label="$3"
  [[ -d "$module_dir" ]] || return 0
  local hits
  hits=$(grep -rnE "^[[:space:]]*(from|import)[[:space:]]+${forbidden}(\.|[[:space:]]|$)" \
    --include='*.py' "$module_dir" 2>/dev/null)
  if [[ -n "$hits" ]]; then
    echo "✗ ${label}"
    echo "$hits" | sed 's/^/    /'
    fail=1
  fi
}

# platform 不得 import 其他业务模块
check platform 'scheduler'   'platform 直接 import scheduler'
check platform 'agent'       'platform 直接 import agent'
check platform 'algo_common' 'platform 直接 import algo_common'

# scheduler 不得 import 其他业务模块
check scheduler 'app'         'scheduler 直接 import platform 的 app 包'
check scheduler 'agent'       'scheduler 直接 import agent'
check scheduler 'algo_common' 'scheduler 直接 import algo_common'

# agent 不得 import 其他业务模块
check agent 'app'         'agent 直接 import platform 的 app 包'
check agent 'scheduler'   'agent 直接 import scheduler'
check agent 'algo_common' 'agent 直接 import algo_common'

# algo 不得 import 其他业务模块
check algo 'app'       'algo 直接 import platform 的 app 包'
check algo 'scheduler' 'algo 直接 import scheduler'
check algo 'agent'     'algo 直接 import agent'

# contract 是底座，不得依赖任何业务模块
for pkg in app scheduler agent algo_common; do
  check contract "$pkg" "contract 反向依赖业务模块 ${pkg}"
done

# 跨目录 sys.path 注入
hits=$(grep -rnE "sys\.path.*\.\./" --include='*.py' \
  contract platform scheduler agent algo 2>/dev/null)
if [[ -n "$hits" ]]; then
  echo "✗ 跨目录 sys.path 注入"
  echo "$hits" | sed 's/^/    /'
  fail=1
fi

if [[ $fail -eq 0 ]]; then
  echo "✓ 依赖铁律通过：模块间无直接 import，仅依赖 contract"
fi
exit $fail
