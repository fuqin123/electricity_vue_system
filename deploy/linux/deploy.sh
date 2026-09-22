#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · 一键部署脚本
#
# 用法：
#   ./deploy.sh install <大屏tar包> <后台tar包>   首次安装
#   ./deploy.sh update  <后台tar包>               更新后台（自动备份）
#   ./deploy.sh rollback                          回滚到最近一次备份
#   ./deploy.sh status                            查看当前状态并做 HTTP 探测
#
# 打包方式（在开发机执行）：
#   cd frontend && pnpm build
#   tar -czf screen-dist.tar.gz -C apps/screen/dist .
#   tar -czf admin-dist.tar.gz  -C apps/admin/dist .
#
# ══════════════════════════════════════════════════════════════
set -euo pipefail

APP_ROOT="/opt/eep"
BACKUP_DIR="${APP_ROOT}/_backup"
NGINX_CONF="/etc/nginx/conf.d/eep.conf"
KEEP_BACKUPS=5

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; NC=$'\033[0m'
info()  { echo "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo "${YELLOW}[WARN]${NC}  $*"; }
error() { echo "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo "${CYAN}[STEP]${NC}  $*"; }

need_root() {
  if [[ $EUID -ne 0 ]]; then
    error "请用 root 或 sudo 执行"
    exit 1
  fi
}

# 取 Nginx 实际运行用户（Debian 系是 www-data，RHEL 系是 nginx）
nginx_user() {
  if id nginx >/dev/null 2>&1; then echo "nginx"; else echo "www-data"; fi
}

# ── 环境自检 ────────────────────────────────────────────────
preflight() {
  step "环境自检"
  command -v nginx >/dev/null 2>&1 || { error "未安装 nginx，请先安装后再执行"; exit 1; }
  command -v curl  >/dev/null 2>&1 || warn "未安装 curl，HTTP 探测将跳过"

  if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
    warn "SELinux 为 Enforcing，将自动打标签（如需手工处理见 docs/04 §3.4）"
  fi

  local avail
  avail=$(df -m "${APP_ROOT%/*}" 2>/dev/null | awk 'NR==2{print $4}')
  if [[ -n "${avail:-}" && "${avail}" -lt 1024 ]]; then
    warn "可用空间不足 1GB（当前 ${avail} MB），备份可能失败"
  fi
}

fix_selinux() {
  if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
    step "处理 SELinux 标签"
    semanage fcontext -a -t httpd_sys_content_t "${APP_ROOT}(/.*)?" 2>/dev/null || true
    restorecon -Rv "${APP_ROOT}" >/dev/null 2>&1 || true
    # Nginx 反向代理到后端需要此开关
    setsebool -P httpd_can_network_connect 1 2>/dev/null || true
    info "SELinux 标签已处理"
  fi
}

# ── 首次安装 ────────────────────────────────────────────────
do_install() {
  need_root
  preflight

  local screen_tar="${1:-}" admin_tar="${2:-}"
  [[ -f "${screen_tar:-}" ]] || { error "大屏产物包不存在：${screen_tar:-（未提供）}"; exit 1; }
  [[ -f "${admin_tar:-}"  ]] || { error "后台产物包不存在：${admin_tar:-（未提供）}"; exit 1; }

  step "创建目录"
  mkdir -p "${APP_ROOT}/screen" "${APP_ROOT}/admin" "${BACKUP_DIR}"

  step "解压产物"
  tar -xzf "${screen_tar}" -C "${APP_ROOT}/screen"
  tar -xzf "${admin_tar}"  -C "${APP_ROOT}/admin"

  local u; u=$(nginx_user)
  chown -R "${u}:${u}" "${APP_ROOT}"
  chmod -R 755 "${APP_ROOT}"
  info "属主设为 ${u}"

  if [[ ! -f "${NGINX_CONF}" ]]; then
    step "写入 Nginx 配置"
    local src
    src="$(cd "$(dirname "$0")/../nginx" && pwd)/eep.conf"
    if [[ -f "${src}" ]]; then
      cp "${src}" "${NGINX_CONF}"
      info "已复制 ${src} → ${NGINX_CONF}"
    else
      warn "未找到 ${src}，请手工放置 Nginx 配置"
    fi
  else
    warn "${NGINX_CONF} 已存在，跳过（如需更新请手工替换）"
  fi

  fix_selinux

  step "校验并重载 Nginx"
  nginx -t || { error "Nginx 配置校验失败，请检查 ${NGINX_CONF}"; exit 1; }
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl reload nginx || systemctl restart nginx

  do_verify
  info "安装完成"
}

# ── 更新（带自动备份）────────────────────────────────────────
do_update() {
  need_root

  local admin_tar="${1:-}"
  [[ -f "${admin_tar:-}" ]] || { error "后台产物包不存在：${admin_tar:-（未提供）}"; exit 1; }

  local ts; ts=$(date +%Y%m%d-%H%M%S)
  mkdir -p "${BACKUP_DIR}"

  step "备份当前版本"
  if [[ -d "${APP_ROOT}/admin" && -n "$(ls -A "${APP_ROOT}/admin" 2>/dev/null)" ]]; then
    tar -czf "${BACKUP_DIR}/admin-${ts}.tar.gz" -C "${APP_ROOT}/admin" .
    info "已备份 → ${BACKUP_DIR}/admin-${ts}.tar.gz"
  else
    warn "当前 admin 目录为空，跳过备份"
  fi

  # 只保留最近 N 份
  ls -1t "${BACKUP_DIR}"/admin-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f

  step "替换文件"
  find "${APP_ROOT}/admin" -mindepth 1 -delete
  tar -xzf "${admin_tar}" -C "${APP_ROOT}/admin"

  local u; u=$(nginx_user)
  chown -R "${u}:${u}" "${APP_ROOT}/admin"
  fix_selinux

  step "重载 Nginx"
  nginx -t && systemctl reload nginx

  do_verify
  info "更新完成。如需回滚：./deploy.sh rollback"
}

# ── 回滚 ───────────────────────────────────────────────────
do_rollback() {
  need_root
  local latest
  latest=$(ls -1t "${BACKUP_DIR}"/admin-*.tar.gz 2>/dev/null | head -1 || true)
  [[ -n "${latest}" ]] || { error "没有可用备份"; exit 1; }

  warn "即将回滚到：${latest}"
  read -r -p "确认继续？(yes/N) " reply
  [[ "${reply}" == "yes" ]] || { info "已取消"; exit 0; }

  find "${APP_ROOT}/admin" -mindepth 1 -delete
  tar -xzf "${latest}" -C "${APP_ROOT}/admin"

  local u; u=$(nginx_user)
  chown -R "${u}:${u}" "${APP_ROOT}/admin"

  nginx -t && systemctl reload nginx
  do_verify
  info "回滚完成"
}

# ── 验证 ───────────────────────────────────────────────────
do_verify() {
  command -v curl >/dev/null 2>&1 || { warn "未安装 curl，跳过 HTTP 探测"; return 0; }

  step "HTTP 探测"
  sleep 1

  local host="127.0.0.1" code

  probe() {
    local url="$1" desc="$2" expect="${3:-200}"
    code=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "$url" || echo "000")
    if [[ "${code}" == "${expect}" ]]; then
      info "${desc}  HTTP ${code} ✓"
    elif [[ "${expect}" == "any" || "${code}" != "000" ]]; then
      warn "${desc}  HTTP ${code}（预期 ${expect}）"
    else
      warn "${desc}  HTTP ${code} ✗"
    fi
  }

  probe "http://${host}/healthz"           "健康检查        "
  probe "http://${host}/screen/"           "大屏首页        "
  probe "http://${host}/admin/"            "后台首页        "
  probe "http://${host}/api/health"        "后端 API        " any

  # ★ 最关键的一项：history 回退
  code=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "http://${host}/admin/system/user" || echo "000")
  if [[ "${code}" == "200" ]]; then
    info "后台子路由回退  HTTP ${code} ✓"
  else
    error "后台子路由回退  HTTP ${code} ✗  → 检查 try_files \$uri \$uri/ /admin/index.html"
  fi

  # 视频网关（可选，不通不影响前端部署）
  code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "http://${host}:1984/api/streams" || echo "000")
  if [[ "${code}" == "200" ]]; then
    info "go2rtc 网关    HTTP ${code} ✓"
  else
    warn "go2rtc 网关    HTTP ${code}（视频功能需要它，详见 deploy/go2rtc/README.md）"
  fi
}

# ── 状态 ───────────────────────────────────────────────────
do_status() {
  echo "── 服务 ──"
  for svc in nginx eep-backend; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}\.service"; then
      printf '  %-14s %s\n' "$svc" "$(systemctl is-active "$svc" 2>/dev/null || echo unknown)"
    else
      printf '  %-14s %s\n' "$svc" "not-installed"
    fi
  done

  echo "── 目录 ──"
  du -sh "${APP_ROOT}/screen" "${APP_ROOT}/admin" 2>/dev/null || true

  echo "── 备份（最近 5 份）──"
  if [[ -d "${BACKUP_DIR}" ]]; then
    ls -lht "${BACKUP_DIR}" 2>/dev/null | head -6 || echo "  （无备份）"
  else
    echo "  （备份目录不存在）"
  fi

  echo "── Nginx 配置 ──"
  if [[ -f "${NGINX_CONF}" ]]; then
    if nginx -t >/dev/null 2>&1; then
      echo "  语法 OK：${NGINX_CONF}"
    else
      echo "  语法错误：${NGINX_CONF}（执行 nginx -t 查看详情）"
    fi
  else
    echo "  未安装：${NGINX_CONF}"
  fi

  echo "── 磁盘 ──"
  df -h / "${APP_ROOT}" 2>/dev/null | awk 'NR==1 || /\//'

  do_verify
}

# ── 入口 ───────────────────────────────────────────────────
case "${1:-}" in
  install)  shift; do_install "${@:-}" ;;
  update)   shift; do_update  "${@:-}" ;;
  rollback) do_rollback ;;
  status)   do_status ;;
  *)
    cat <<'USAGE'
能源产效管理系统 · 部署工具

用法：
  ./deploy.sh install <大屏tar包> <后台tar包>   首次安装
  ./deploy.sh update  <后台tar包>               更新后台（自动备份，失败可回滚）
  ./deploy.sh rollback                          回滚到最近一次备份
  ./deploy.sh status                            查看服务/目录/备份状态并做 HTTP 探测

示例：
  ./deploy.sh install screen-dist.tar.gz admin-dist.tar.gz
  ./deploy.sh update  admin-dist.tar.gz
  ./deploy.sh rollback
  ./deploy.sh status
USAGE
    exit 1
    ;;
esac
