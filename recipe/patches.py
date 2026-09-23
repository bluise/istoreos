#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
istoreos / recipe/patches.py
==========================
把"官方 iStoreOS rootfs"改成"精简版"所需的所有文件级改动。
每个改动都带断言：锚点找不到就报错退出，绝不静默跳过（上游改版时能立刻发现）。

用法（由 slim.py 调用）：
    patch_all(rootfs_dir, files_dir)
"""
import os
import re
import shutil
import sys

FAIL = []


def _read(p):
    with open(p, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def _write(p, s):
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(s)


def edit(path, pairs, required=True):
    """把 pairs 里的 (old, new) 逐个替换；old 找不到时报错（或按 required=False 跳过）。"""
    if not os.path.exists(path):
        if required:
            FAIL.append("文件不存在: %s" % path)
        return False
    s = _read(path)
    ok = True
    for old, new in pairs:
        if old not in s:
            if required:
                FAIL.append("锚点未找到: %s <- %r" % (path, old[:70]))
                ok = False
            continue
        s = s.replace(old, new)
    if ok:
        _write(path, s)
    return ok


def rm(path, required=False):
    if os.path.exists(path):
        os.unlink(path)
        return True
    if required:
        FAIL.append("要删的文件不存在: %s" % path)
    return False


def put(path, content, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, bytes):
        with open(path, "wb") as f:
            f.write(content)
    else:
        _write(path, content)
    os.chmod(path, mode)


def copy(src, dst, mode=None):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    if mode is not None:
        os.chmod(dst, mode)


def rm_stanza(root, pkg):
    """从 apk 数据库里摘掉一个包的记录。"""
    db = os.path.join(root, "lib/apk/db/installed")
    blocks = [b for b in _read(db).split("\n\n") if b.strip()]
    keep = [b for b in blocks if ("\nP:%s\n" % pkg) not in "\n" + b + "\n"]
    if len(keep) == len(blocks):
        return False
    _write(db, "\n\n".join(keep) + "\n")
    return True


# ---------------------------------------------------------------------------
# 1) 首页（iStoreOS 首页 = quickstart 那个 SPA，/www/luci-static/quickstart/index.js）
# ---------------------------------------------------------------------------
def patch_quickstart_spa(root, oaf_v7=False):
    js = os.path.join(root, "www/luci-static/quickstart/index.js")
    if not os.path.exists(js):
        FAIL.append("找不到首页 SPA: %s" % js)
        return

    # 1a. 去掉 4 张卡片：存储服务 / 下载服务 / 远程域名 / 配置模块
    cards = [
        '{key:"storage",title:n("\\u5B58\\u50A8\\u670D\\u52A1"),description:n("\\u5171\\u4EAB\\u4E0E\\u5B58\\u50A8\\u670D\\u52A1\\u6982\\u89C8")},',
        '{key:"downloadService",title:n("\\u4E0B\\u8F7D\\u670D\\u52A1"),description:n("\\u4E0B\\u8F7D\\u4EFB\\u52A1\\u4E0E\\u670D\\u52A1\\u72B6\\u6001")},',
        '{key:"remoteDomain",title:n("\\u8FDC\\u7A0B\\u57DF\\u540D"),description:n("\\u8FDC\\u7A0B\\u8BBF\\u95EE\\u57DF\\u540D\\u7BA1\\u7406")},',
        '{key:"configModule",title:n("\\u914D\\u7F6E\\u6A21\\u5757"),description:n("\\u5185\\u7F51\\u914D\\u7F6E\\u3001DNS\\u914D\\u7F6E\\u7B49\\u5DE5\\u5177")},',
    ]
    # 1b. 对应的动态 push 分支
    pushes = [
        'f.value.storage&&R.push({key:"storage",component:jf}),',
        'f.value.downloadService&&R.push({key:"downloadService",component:n1}),',
        'f.value.remoteDomain&&R.push({key:"remoteDomain",component:s5}),',
    ]
    # 1c. 配置模块是单独渲染的分支 + 两个默认开关
    others = [
        ('f.value.configModule?(r(),s("div",sh,[t("div",dh,[Y(Ip)])])):D("",!0),', ''),
        ('configModule:!0,', ''),
        ('configModule:!1,', ''),
    ]
    edit(js, [(c, '') for c in cards] + [(p, '') for p in pushes] + others)

    # 1d. 首页「文件管理」旁 ⋮ 菜单里的 RAID管理 / S.M.A.R.T.
    edit(js, [
        ('t("div",null,[t("a",ag,i(e(n)("RAID\\u7BA1\\u7406")),1)]),og,', ''),
        ('ag={href:"/cgi-bin/luci/admin/nas/raid"},'
         'og=eg(()=>t("div",null,[t("a",{href:"/cgi-bin/luci/admin/nas/smart"},"S.M.A.R.T.")],-1)),', ''),
        # 磁盘项里的 "S.M.A.R.T异常" 分支（没有 SMART 数据时别误报）
        ('o.disk.smartWarning&&o.smartWarning?', '!1?'),
    ], required=False)

    # 1e. 家长控制：点击走通用跳转；只有装了 OAF v7 才把链接改到新路由
    pairs = [('if(R.icon=="speed")return m();if(R.icon=="baby")return p();',
              'if(R.icon=="speed")return m();')]
    if oaf_v7:
        pairs.append(('"/cgi-bin/luci/admin/services/appfilter"',
                      '"/cgi-bin/luci/admin/services/oaf"'))
    edit(js, pairs, required=False)


# ---------------------------------------------------------------------------
# 2) LuCI 菜单/页面标题、协议、SMART 保护等
# ---------------------------------------------------------------------------
def patch_luci(root):
    # 2a. 系统 -> Argon主题设置 改名成 主题设置
    edit(os.path.join(root, "usr/lib/lua/luci/controller/argon-config.lua"), [
        ('_("Argon Config")', '"主题设置"'),
    ], required=False)

    # 2b. 「网络存储」下不再显示 磁盘阵列 / S.M.A.R.T.（quickstart 注册的两条路由）
    qs = os.path.join(root, "usr/lib/lua/luci/controller/quickstart.lua")
    if os.path.exists(qs):
        lines = _read(qs).split("\n")
        keep = [l for l in lines if ('"nas", "raid"' not in l and '"nas", "smart"' not in l)]
        if len(keep) != len(lines):
            _write(qs, "\n".join(keep))

    # 2c. 磁盘管理：smartctl 不再是"必需工具"（否则整个磁盘管理页不注册、菜单消失）
    edit(os.path.join(root, "usr/lib/lua/luci/controller/diskman.lua"), [
        ('local CMD = {"parted", "blkid", "smartctl"}', 'local CMD = {"parted", "blkid"}'),
        ('function smart_attr(dev)\n  local attr = { }\n  local dm = require "luci.model.diskman"\n',
         'function smart_attr(dev)\n  local attr = { }\n  local dm = require "luci.model.diskman"\n'
         '  if not dm.command.smartctl then\n'
         '    luci.http.prepare_content("application/json")\n'
         '    luci.http.write_json(attr)\n'
         '    return\n'
         '  end\n'),
    ], required=False)
    edit(os.path.join(root, "usr/lib/lua/luci/model/diskman.lua"), [
        ('local get_smart_info = function(device)\n  local section\n  local smart_info = {}\n',
         'local get_smart_info = function(device)\n  local section\n  local smart_info = {}\n'
         '  if not d.command.smartctl then return smart_info end\n'),
    ], required=False)

    # 2d. 服务菜单里的 UPnP 标题改短
    edit(os.path.join(root, "usr/share/luci/menu.d/luci-app-upnp.json"), [
        ('"UPnP IGD & PCP"', '"UPnP IGD"'),
    ], required=False)
    edit(os.path.join(root, "www/luci-static/resources/view/upnp/upnp.js"), [
        ("_('UPnP IGD & PCP/NAT-PMP Service')", "_('UPnP IGD')"),
    ], required=False)

    # 2e. WireGuard：删掉 luci-compat 里的协议文件（否则"网络->添加接口->协议"还列 WireGuard）
    wg = os.path.join(root, "usr/lib/lua/luci/model/network/proto_wireguard.lua")
    if os.path.exists(wg):
        rm(wg)
        db = os.path.join(root, "lib/apk/db/installed")
        blocks = [b for b in _read(db).split("\n\n") if b.strip()]
        out = []
        for b in blocks:
            out.append("\n".join(l for l in b.split("\n") if l != "R:proto_wireguard.lua"))
        _write(db, "\n\n".join(out) + "\n")


# ---------------------------------------------------------------------------
# 3) 主题：只留 Argon
# ---------------------------------------------------------------------------
def patch_theme(root):
    luci = os.path.join(root, "etc/config/luci")
    if not os.path.exists(luci):
        FAIL.append("找不到 /etc/config/luci")
        return
    s = _read(luci)
    s = s.replace("option mediaurlbase '/luci-static/bootstrap'",
                  "option mediaurlbase '/luci-static/argon'")
    if "option Argon" not in s:
        s = s.replace("config internal 'themes'\n",
                      "config internal 'themes'\n\toption Argon '/luci-static/argon'\n")
    # 去掉可能存在的 Bootstrap 主题项
    s = "\n".join(l for l in s.split("\n") if not l.strip().startswith("option Bootstrap"))
    _write(luci, s)


# ---------------------------------------------------------------------------
# 4) /etc/smartd.conf —— iStoreOS 首页的守护进程会读它，缺了首页"磁盘信息"会整块不显示
# ---------------------------------------------------------------------------
def patch_smartd_conf(root):
    put(os.path.join(root, "etc/smartd.conf"), "/dev/hdb -H\n", 0o644)


# ---------------------------------------------------------------------------
# 5) DDNS-GO：二进制（由工作流下载）+ 集成文件
# ---------------------------------------------------------------------------
# (目标路径, 仓库里的文件名, 权限)
DDNS_GO_FILES = [
    ("etc/init.d/ddns-go", "init.d-ddns-go", 0o755),
    ("etc/config/ddns-go", "config-ddns-go", 0o644),
    ("etc/uci-defaults/95-ddns-go", "95-ddns-go", 0o755),
    ("usr/lib/lua/luci/controller/ddnsgo.lua", "ddnsgo.lua", 0o644),
    ("usr/lib/lua/luci/view/ddnsgo.htm", "ddnsgo.htm", 0o644),
    ("usr/share/rpcd/acl.d/luci-app-ddns-go.json", "luci-app-ddns-go.json", 0o644),
]


def patch_ddns_go(root, files_dir, binary=None):
    for rel, fname, mode in DDNS_GO_FILES:
        src = os.path.join(files_dir, "ddns-go", fname)
        dst = os.path.join(root, rel)
        if not os.path.exists(src):
            FAIL.append("缺少 DDNS-GO 集成文件: %s" % src)
            continue
        copy(src, dst, mode)
    # 开机自启软链（首启脚本建得太晚，第一次开机不会起）
    rcd = os.path.join(root, "etc/rc.d")
    os.makedirs(rcd, exist_ok=True)
    for link in ("S95ddns-go", "K10ddns-go"):
        p = os.path.join(rcd, link)
        if os.path.lexists(p):
            os.unlink(p)
        os.symlink("../init.d/ddns-go", p)
    if binary:
        copy(binary, os.path.join(root, "usr/bin/ddns-go"), 0o755)


# ---------------------------------------------------------------------------
# 6) OpenClash：规则数据/界面来自 apk（由工作流下载），这里只做收尾
# ---------------------------------------------------------------------------
def patch_openclash(root):
    # 默认关闭（首次使用需自己下内核、加订阅），并确保目录存在
    cfg = os.path.join(root, "etc/config/openclash")
    if os.path.exists(cfg):
        s = _read(cfg)
        s = re.sub(r"option enable '1'", "option enable '0'", s, count=1)
        _write(cfg, s)
    os.makedirs(os.path.join(root, "etc/openclash/core"), exist_ok=True)



# ---------------------------------------------------------------------------
# 7) OTA 在线升级指向本项目仓库
#    /lib/upgrade/ota.sh 里原本写死 fw0.koolcenter.com（官方完整镜像），
#    误点 OTA 会把精简版刷回官方全家桶。改成指向本仓库 Release 后：
#      - 误点也只会刷到本项目自己产出的固件（由工作流同步发布 OTA 清单文件）
#      - 拿不到 GitHub 时只会报错，不会刷到官方镜像
#    这里用 required=True：万一上游改写了这个文件，构建要立刻失败，避免"以为改了其实没改"。
# ---------------------------------------------------------------------------
OTA_URL_BASE = "https://github.com/bluise/istoreos/releases/latest/download"


def patch_ota(root):
    f = os.path.join(root, "lib/upgrade/ota.sh")
    if not os.path.exists(f):
        FAIL.append("找不到 /lib/upgrade/ota.sh")
        return
    s = _read(f)
    n = 0
    out = []
    for line in s.split("\n"):
        if "OTA_URL_BASE=" in line and "koolcenter.com" in line:
            indent = line[:len(line) - len(line.lstrip())]
            var = "export -n " if "export -n" in line else ""
            out.append('%s%sOTA_URL_BASE="%s"' % (indent, var, OTA_URL_BASE))
            n += 1
        else:
            out.append(line)
    if n == 0:
        FAIL.append("/lib/upgrade/ota.sh 里没找到 OTA_URL_BASE=...koolcenter.com 那几行")
        return
    _write(f, "\n".join(out))
    print("  OTA 地址已指向本仓库 Release（改了 %d 行）" % n)



# ---------------------------------------------------------------------------
# 8) 默认网络：**独立**的首启脚本 /etc/uci-defaults/99-slim-network
#    · 官方文件一个字不改（官方 09_istoreos / rescan_nic 原样保留）
#    · uci-defaults 按文件名排序执行，99- 排在官方 09_istoreos 之后，所以能覆盖
#      rescan_nic 生成的结果；跑完系统自己删掉该脚本，之后重启/升级都不再干预
#      （官方 09_istoreos/blocks 里若有写死的 192.168.100.x，这里只替换非脚本类文件）
# ---------------------------------------------------------------------------
def patch_network(root, files_dir):
    src = os.path.join(files_dir, "netpolicy", "99-slim-network")
    if not os.path.exists(src):
        FAIL.append("缺少默认网络脚本: %s" % src)
        return
    copy(src, os.path.join(root, "etc/uci-defaults/99-slim-network"), 0o755)
    print("  已放入默认网络首启脚本 /etc/uci-defaults/99-slim-network（不改官方文件）")

    targets = [
        "etc/board.json",
        "etc/config/network",
        "etc/config/dhcp",
        "etc/config/system",
        "usr/libexec/blockmount.sh",
    ]
    n = 0
    for rel in targets:
        f = os.path.join(root, rel)
        if not os.path.isfile(f):
            continue
        try:
            t = _read(f)
        except Exception:
            continue
        if "192.168.100.1" in t:
            _write(f, t.replace("192.168.100.1", "10.0.0.1"))
            n += 1
        if "192.168.100." in t:
            t = _read(f)
            _write(f, t.replace("192.168.100.", "10.0.0."))
            n += 1
    if n:
        print("  兜底替换了 %d 个文件里的 192.168.100.x" % n)


# ---------------------------------------------------------------------------
def patch_all(root, files_dir, oaf_v7=False):
    patch_smartd_conf(root)
    patch_ota(root)
    patch_network(root, files_dir)
    patch_quickstart_spa(root, oaf_v7=oaf_v7)
    patch_luci(root)
    patch_theme(root)
    patch_ddns_go(root, files_dir)
    patch_openclash(root)
    if FAIL:
        print("✗ 补丁阶段有 %d 处失败:" % len(FAIL))
        for x in FAIL:
            print("   -", x)
        sys.exit(1)
    print("✓ 补丁全部应用成功")
