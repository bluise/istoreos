#!/bin/sh
# ============================================================================
# 默认网络策略
#   · LAN 固定为静态 10.0.0.1/24
#   · 双网口：第一个网口 = WAN(自动获取)，第二个网口 = LAN
#   · 单网口：该网口 = LAN（不配置 WAN），LAN 自带公共 DNS
#   · dnsmasq 始终带公共上游 DNS（网口没拿到租约/无线中继做上行时也不会解析不了域名）
#
# 这个脚本有两处入口：
#   1) /etc/uci-defaults/98-netpolicy-v2 —— 全新刷机首次开机（排在 config_generate 之后）
#   2) /etc/init.d/netpolicy (S19)      —— 每次开机
# 为什么第二个入口必须存在：OTA 升级保留 overlay，而 uci-defaults 脚本执行一次后会被
# 加白名单覆盖掉（下次升级不会再生效）。于是老配置里的 network.wan6.device='@wan'
# 这类"设备不存在"的残留会一直留着 —— 每次开机自动修复才能根治。
#
# 安全约束：
#   · 绝不动 wwan / 无线中继及其它接口的配置（早期版本删过，直接导致 LuCI 应用回滚死循环）
#   · 只在"配置不符合策略"时才写 uci，且一次性提交、只 reload 一次
#   · 完成一次完整策略后写 /etc/netpolicy.applied 标记；之后只做修复，不再覆盖用户改过的配置
# ============================================================================
LAN_IP='10.0.0.1'
LAN_MASK='255.255.255.0'
FLAG='/etc/netpolicy.applied'
DNS1='223.5.5.5'
DNS2='119.29.29.29'

log() { logger -t netpolicy "$*" 2>/dev/null; echo "netpolicy: $*"; }

nics() {
	for d in /sys/class/net/*; do
		n=${d##*/}
		[ -e "$d/device" ] || continue
		case "$n" in
			lo|br-*|veth*|docker*|@*) continue ;;
			eth*|en*) echo "$n" ;;
		esac
	done | sort
}

[ -f /etc/config/network ] || exit 0

# 等网口就绪（开机早期可能还没枚举完）
i=0
while [ "$i" -lt 15 ]; do
	[ -n "$(nics)" ] && break
	sleep 1
	i=$((i + 1))
done

LIST=$(nics)
N=$(echo "$LIST" | grep -c .)
FIRST=$(echo "$LIST" | sed -n 1p)
SECOND=$(echo "$LIST" | sed -n 2p)

if [ "$N" -ge 2 ]; then
	LAN_NIC="$SECOND"
	WAN_NIC="$FIRST"
else
	LAN_NIC="$FIRST"
	WAN_NIC=""
fi
[ -n "$LAN_NIC" ] || { log "没找到物理网口，保持原样"; exit 0; }

# ---------------------------------------------------------------------------
# 修复：设备名对不上的 wan/wan6 改回真实网口；dnsmasq 补公共上游 DNS
# 只碰 wan/wan6/dhcp，绝不碰 wwan 等其它接口。
# ---------------------------------------------------------------------------
dev_ok() {
	[ -e "/sys/class/net/$1" ] && return 0
	i=0
	while uci -q get network.@device[$i] >/dev/null; do
		[ "$(uci -q get network.@device[$i].name)" = "$1" ] && return 0
		i=$((i + 1))
	done
	return 1
}

fixed=""
if [ -n "$WAN_NIC" ]; then
	for s in wan wan6; do
		uci -q get "network.$s" >/dev/null 2>&1 || continue
		cur=$(uci -q get "network.$s.device")
		case "$cur" in
			"$WAN_NIC") ;;
			@*) uci set "network.$s.device=$WAN_NIC"; fixed="$fixed $s($cur)" ;;
			"") ;;
			*) if ! dev_ok "$cur"; then
				uci set "network.$s.device=$WAN_NIC"; fixed="$fixed $s($cur)"
			   fi ;;
		esac
	done
fi

dns_changed=0
if [ -e /etc/config/dhcp ]; then
	have=$(uci -q get dhcp.@dnsmasq[0].server)
	case "$have" in
		*"$DNS1"*) ;;
		*) uci add_list "dhcp.@dnsmasq[0].server=$DNS1"; dns_changed=1 ;;
	esac
	case "$have" in
		*"$DNS2"*) ;;
		*) uci add_list "dhcp.@dnsmasq[0].server=$DNS2"; dns_changed=1 ;;
	esac
fi

if [ -n "$fixed" ]; then
	uci commit network
	log "已修复设备不存在的接口:$fixed"
fi
if [ "$dns_changed" = 1 ]; then
	uci commit dhcp
	log "已为 dnsmasq 补公共上游 DNS: $DNS1 $DNS2"
fi
if [ -n "$fixed" ] || [ "$dns_changed" = 1 ]; then
	/etc/init.d/network reload >/dev/null 2>&1
	/etc/init.d/dnsmasq restart >/dev/null 2>&1
fi

# ---------------------------------------------------------------------------
# 已完整应用过一次 -> 之后只修复，不再覆盖用户自己改过的配置
# ---------------------------------------------------------------------------
if [ -f "$FLAG" ]; then
	[ -n "$fixed$dns_changed" ] || log "网络配置正常，跳过"
	exit 0
fi

# ---- 现阶段配置是否符合策略 ----
CUR_IP=$(uci -q get network.lan.ipaddr)
CUR_PORTS=$(uci -q get network.@device[0].ports)
if [ "$CUR_IP" = "$LAN_IP" ] && echo "$CUR_PORTS" | grep -qw "$LAN_NIC"; then
	if [ -z "$WAN_NIC" ]; then
		[ -z "$(uci -q get network.wan)" ] && { : >"$FLAG"; log "网络已符合策略($LAN_NIC=LAN)，标记完成"; exit 0; }
	elif [ "$(uci -q get network.wan.device)" = "$WAN_NIC" ] \
	     && [ "$(uci -q get network.wan6.device)" = "$WAN_NIC" ] \
	     && [ "$(uci -q get network.wan.peerdns)" = "0" ]; then
		: >"$FLAG"
		log "网络已符合策略($WAN_NIC=WAN,$LAN_NIC=LAN)，标记完成"
		exit 0
	fi
fi

log "检测到 $N 个网口: $(echo $LIST | tr '\n' ' ') -> LAN=$LAN_NIC WAN=${WAN_NIC:-无}"

# ---- 只重建 lan/wan/wan6 与名为 br-lan 的桥 ----
uci -q delete network.lan
uci -q delete network.wan
uci -q delete network.wan6
i=0
while uci -q get network.@device[$i] >/dev/null; do
	if [ "$(uci -q get network.@device[$i].name)" = "br-lan" ]; then
		uci -q delete network.@device[$i]
		continue
	fi
	i=$((i + 1))
done

uci set network.loopback=interface
uci set network.loopback.device='lo'
uci set network.loopback.proto='static'
uci set network.loopback.ipaddr='127.0.0.1'
uci set network.loopback.netmask='255.0.0.0'

uci add network device >/dev/null
uci set network.@device[-1].name='br-lan'
uci set network.@device[-1].type='bridge'
uci add_list network.@device[-1].ports="$LAN_NIC"

uci set network.lan=interface
uci set network.lan.device='br-lan'
uci set network.lan.proto='static'
uci set network.lan.ipaddr="$LAN_IP"
uci set network.lan.netmask="$LAN_MASK"
uci set network.lan.ip6assign='60'
uci add_list network.lan.dns="$DNS1"
uci add_list network.lan.dns="$DNS2"
if [ -n "$WAN_NIC" ]; then
	uci set network.wan=interface
	uci set network.wan.device="$WAN_NIC"
	uci set network.wan.proto='dhcp'
	uci set network.wan.peerdns='0'
	uci add_list network.wan.dns="$DNS1"
	uci add_list network.wan.dns="$DNS2"
	uci set network.wan6=interface
	uci set network.wan6.device="$WAN_NIC"
	uci set network.wan6.proto='dhcpv6'
fi
uci commit network

# ---- DHCP 地址池 ----
if [ -e /etc/config/dhcp ]; then
	uci set dhcp.lan=dhcp
	uci set dhcp.lan.interface='lan'
	uci set dhcp.lan.start='100'
	uci set dhcp.lan.limit='150'
	uci set dhcp.lan.leasetime='12h'
	uci set dhcp.lan.ignore='0'
	uci commit dhcp
fi

: >"$FLAG"

# ---- 让配置立即生效（只做这一次）----
/etc/init.d/network reload >/dev/null 2>&1
sleep 2
log "已应用: LAN=$LAN_IP (br-lan -> $LAN_NIC)${WAN_NIC:+, WAN=$WAN_NIC(dhcp)}"
exit 0
