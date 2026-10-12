#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · 每日巡检脚本
#
# 用法：
#   ./daily-check.sh              正常巡检
#   ./daily-check.sh --quiet      只在有异常时输出（适合做成 cron 告警）
#
# 建议 cron（每日 08:30）：
#   30 8 * * * /opt/eep/ops/daily-check.sh --quiet | mail -s "EEP 巡检" ops@plant01.local
# ══════════════════════════════════════════════════════════════
set -uo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; NC=$'\033[0m'
ISSUES=0
WARNS=0

[ "${QUIET}" -eq 1 ] && { RED=""; GREEN=""; YELLOW=""; CYAN=""; NC=""; }

say()  { [ "${QUIET}" -eq 1 ] || echo "$*"; }
ok()   { say "  ${GREEN}✓${NC} $*"; }
bad()  { echo "  ${RED}✗${NC} $*"; ISSUES=$((ISSUES + 1)); }
warn() { echo "  ${YELLOW}!${NC} $*"; WARNS=$((WARNS + 1)); }

HOST="127.0.0.1"
GO2RTC_PORT="1984"

echo "════════ 能源产效管理系统 · 每日巡检 $(date '+%F %T') ════════"

# ── 1. 服务状态 ─────────────────────────────────────────────
say "${CYAN}── 1. 服务状态 ──${NC}"
for svc in nginx eep-backend; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}\.service"; then
    state=$(systemctl is-active "$svc" 2>/dev/null || echo unknown)
    [[ "$state" == "active" ]] && ok "$svc : active" || bad "$svc : $state"
  else
    ok "$svc : not-installed（未部署则忽略）"
  fi
done

# ── 2. HTTP 探测 ────────────────────────────────────────────
say "${CYAN}── 2. HTTP 探测 ──${NC}"
probe() {
  local url="$1" desc="$2" expect="${3:-200}" optional="${4:-}"
  local code
  code=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "$expect" ]]; then
    ok "$desc : HTTP $code"
  elif [[ -n "$optional" ]]; then
    warn "$desc : HTTP $code（$optional）"
  else
    bad "$desc : HTTP $code  ($url)"
  fi
}

probe "http://${HOST}/healthz"            "健康检查        "
probe "http://${HOST}/screen/"            "大屏首页        "
probe "http://${HOST}/admin/"             "后台首页        "
probe "http://${HOST}/admin/system/user"  "后台子路由回退  "    # ★ history 回退是否生效
probe "http://${HOST}/api/energy/overview" "后端 API(经反代) " 200 "后端可能未启动"
probe "http://${HOST}:${GO2RTC_PORT}/api/streams" "视频网关 go2rtc" 200 "未部署视频则忽略"

# ── 3. 磁盘 ─────────────────────────────────────────────────
say "${CYAN}── 3. 磁盘 ──${NC}"
while read -r line; do
  usage=$(echo "$line" | awk '{print $5}' | tr -d '%')
  mount=$(echo "$line" | awk '{print $6}')
  if [[ "${usage:-0}" =~ ^[0-9]+$ ]]; then
    if   [[ "$usage" -ge 90 ]]; then bad "磁盘 $mount 使用率 ${usage}%"
    elif [[ "$usage" -ge 80 ]]; then warn "磁盘 $mount 使用率 ${usage}%"
    else ok "磁盘 $mount 使用率 ${usage}%"
    fi
  fi
done < <(df -h / /opt 2>/dev/null | awk 'NR>1 && /\//')

# inode 也会被日志碎片耗尽，容易被忽略
while read -r line; do
  usage=$(echo "$line" | awk '{print $5}' | tr -d '%')
  mount=$(echo "$line" | awk '{print $6}')
  if [[ "${usage:-0}" =~ ^[0-9]+$ && "$usage" -ge 85 ]]; then
    warn "inode $mount 使用率 ${usage}%（小文件过多，查日志碎片）"
  fi
done < <(df -i / /opt 2>/dev/null | awk 'NR>1 && /\//')

# ── 4. 备份 ─────────────────────────────────────────────────
say "${CYAN}── 4. 备份 ──${NC}"
if [[ -d /opt/eep/_backup ]]; then
  cnt=$(find /opt/eep/_backup -name '*.tar.gz' 2>/dev/null | wc -l)
  latest=$(ls -1t /opt/eep/_backup/*.tar.gz 2>/dev/null | head -1)
  if [[ -n "$latest" ]]; then
    age_days=$(( ( $(date +%s) - $(stat -c %Y "$latest") ) / 86400 ))
    if [[ "$age_days" -gt 14 ]]; then
      warn "最近备份已 ${age_days} 天：$(basename "$latest")"
    else
      ok "备份 ${cnt} 份，最近：$(basename "$latest")（${age_days} 天前）"
    fi
  else
    warn "备份目录为空"
  fi
else
  warn "/opt/eep/_backup 不存在"
fi

# ── 5. 日志异常 ─────────────────────────────────────────────
say "${CYAN}── 5. 日志异常 ──${NC}"
if command -v journalctl >/dev/null 2>&1; then
  n=$(journalctl -u eep-backend --since "24 hours ago" -p err --no-pager 2>/dev/null | grep -c . || echo 0)
  [[ "$n" -eq 0 ]] && ok "后端近 24 小时无 ERROR 级日志" || warn "后端近 24 小时 ERROR 日志 ${n} 行（journalctl -u eep-backend -p err 查看）"
fi

if [[ -f /var/log/nginx/eep-error.log ]]; then
  n=$(grep -c '\[error\]' /var/log/nginx/eep-error.log 2>/dev/null || echo 0)
  [[ "$n" -eq 0 ]] && ok "Nginx 无 error 记录" || warn "Nginx error 日志 ${n} 行"
fi

# 5xx 占比（业务健康度最直观的指标）
if [[ -f /var/log/nginx/eep-access.log ]]; then
  total=$(wc -l < /var/log/nginx/eep-access.log 2>/dev/null || echo 0)
  if [[ "$total" -gt 0 ]]; then
    c5=$(awk '{print $9}' /var/log/nginx/eep-access.log 2>/dev/null | grep -c '^5' || echo 0)
    # 注意：bash 算术不支持三目运算符，先算分母
    denom="$total"
    [[ "$denom" -lt 1 ]] && denom=1
    pct=$(( c5 * 100 / denom ))
    if   [[ "$pct" -ge 5 ]]; then bad "Nginx 5xx 占比 ${pct}%（${c5}/${total}）"
    elif [[ "$pct" -ge 1 ]]; then warn "Nginx 5xx 占比 ${pct}%（${c5}/${total}）"
    else ok "Nginx 5xx 占比 ${pct}%（${c5}/${total}）"
    fi
  fi
fi

# ── 汇总 ────────────────────────────────────────────────────
echo "───────────────────────────────────────────────"
if [[ "$ISSUES" -gt 0 ]]; then
  echo "${RED}巡检完成：${ISSUES} 项异常，${WARNS} 项告警 —— 需立即处理${NC}"
  exit 2
elif [[ "$WARNS" -gt 0 ]]; then
  echo "${YELLOW}巡检完成：0 项异常，${WARNS} 项告警 —— 建议关注${NC}"
  exit 1
else
  echo "${GREEN}巡检完成：全部正常${NC}"
  exit 0
fi
