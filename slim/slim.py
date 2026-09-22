#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
istoreos-slim / slim.py
=======================
把「官方 iStoreOS x86_64 镜像」变成「精简版镜像」。整套流程全部可复现：

  1. 解压官方 img.gz  → raw.img
  2. 从 p2 分区切出 squashfs → unsquashfs 得到 rootfs
  3. 按 remove-packages.txt 精确删包（数据库记录 + 文件 + /etc/apk/world + 依赖行）
  4. 用镜像自带的 apk（musl 加载器）补装需要的包（ruby 等）+ 装 OpenClash 的 apk
  5. 放入 DDNS-GO 二进制与集成文件、OAF 新版 apk
  6. 应用所有界面/配置补丁（patches.py）
  7. mksquashfs 重打包 → 按扇区写回 p2（并清零 p2 剩余空间）→ gzip
  8. 自检：关键文件存在、应删项不存在、p2 区段与 squashfs 逐字节一致、解压长度正确

用法:
  python3 slim/slim.py --upstream-url <官方 img.gz URL> --out <输出 img.gz> \
      [--openclash-apk f.apk] [--ddns-go-tar f.tar.gz] [--oaf-dir DIR] [--work DIR]
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import gzip

HERE = os.path.dirname(os.path.abspath(__file__))
P2_SECTOR = 262656          # p2 起始扇区
P2_OFF = P2_SECTOR * 512    # 134479872
P2_BYTES = 524288 * 512     # 256 MiB

sys.path.insert(0, HERE)
import patches  # noqa: E402


def run(cmd, **kw):
    print("  $", " ".join(cmd) if isinstance(cmd, list) else cmd, flush=True)
    return subprocess.run(cmd, check=True, **kw)


def sh(cmd):
    return subprocess.run(cmd, shell=True, check=True)


# ---------------------------------------------------------------- 解包
def download(url, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        print("  已存在，跳过下载:", dst)
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    print("  下载:", url)
    if shutil.which("aria2c"):
        run(["aria2c", "-x", "8", "-s", "8", "-d", os.path.dirname(dst), "-o", os.path.basename(dst), url])
    else:
        run(["curl", "-fSL", "--retry", "3", "-o", dst, url])
    return dst


def gunzip_to(src, dst):
    print("  解压到:", dst)
    with gzip.open(src, "rb") as i, open(dst, "wb") as o:
        shutil.copyfileobj(i, o, 1 << 22)


def slice_p2(img, dst):
    with open(img, "rb") as i, open(dst, "wb") as o:
        i.seek(P2_OFF)
        left = P2_BYTES
        while left > 0:
            b = i.read(min(1 << 22, left))
            if not b:
                break
            o.write(b)
            left -= len(b)
    return dst


def unsquash(sqfs, dst):
    tool = shutil.which("unsquashfs") or shutil.which("unsquashfs4")
    if not tool:
        sys.exit("✗ 需要 unsquashfs（apt install squashfs-tools）")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    # 非 root 下 unsquashfs 无法创建 /dev/console 等设备节点，会以退出码 2 结束（内容仍然完整），
    # 所以这里容忍 0/2，再用关键文件确认解包成功。
    r = subprocess.run([tool, "-d", dst, "-f", sqfs],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode not in (0, 2) or not os.path.exists(os.path.join(dst, "sbin/init")):
        sys.exit("✗ 解包失败（unsquashfs 退出码 %d）" % r.returncode)


# ---------------------------------------------------------------- 删包
def parse_db(path):
    recs, cur = [], []
    for line in open(path, encoding="utf-8", errors="surrogateescape"):
        line = line.rstrip("\n")
        if line == "":
            if cur:
                recs.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        recs.append(cur)
    pkgs = {}
    for r in recs:
        p = {"raw": r, "deps": [], "files": [], "dirs": [], "name": None, "size": 0}
        lastdir = None
        for l in r:
            if len(l) < 2 or l[1] != ":":
                continue
            k, v = l[0], l[2:]
            if k == "P":
                p["name"] = v
            elif k == "I":
                p["size"] = int(v or 0) if v.isdigit() else 0
            elif k == "D":
                p["deps"] = [d.split(">")[0].split("=")[0].split("<")[0] for d in v.split() if d]
            elif k == "F":
                lastdir = v
                p["dirs"].append(v)
            elif k == "R" and lastdir is not None:
                p["files"].append(lastdir + "/" + v)
        if p["name"]:
            pkgs[p["name"]] = p
    return pkgs


def remove_one(root, pkg):
    """只摘掉一个包的数据库记录（文件由随后的安装覆盖）。"""
    db = os.path.join(root, "lib/apk/db/installed")
    blocks = [b for b in open(db, encoding="utf-8", errors="surrogateescape").read().split("\n\n") if b.strip()]
    keep = [b for b in blocks if ("\nP:%s\n" % pkg) not in "\n" + b + "\n"]
    if len(keep) == len(blocks):
        return False
    with open(db, "w", encoding="utf-8", errors="surrogateescape") as fh:
        fh.write("\n\n".join(keep) + "\n")
    return True


def remove_packages(root, listfile):
    db = os.path.join(root, "lib/apk/db/installed")
    pkgs = parse_db(db)
    targets = set()
    for line in open(listfile, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line:
            targets.add(line)
    drop = targets & set(pkgs)
    missing = targets - set(pkgs)
    if missing:
        print("  清单里这些包官方镜像里没有（忽略）: %s" % " ".join(sorted(missing)[:12]))
    nf = nd = nrc = nsz = 0
    for n in sorted(drop):
        p = pkgs[n]
        nsz += p["size"]
        for f in p["files"]:
            fp = os.path.join(root, f)
            try:
                if os.path.islink(fp) or os.path.isfile(fp):
                    os.unlink(fp)
                    nf += 1
            except FileNotFoundError:
                pass
        for d in sorted(set(p["dirs"]), key=len, reverse=True):
            dp = os.path.join(root, d)
            try:
                if os.path.isdir(dp) and not os.listdir(dp):
                    os.rmdir(dp)
                    nd += 1
            except OSError:
                pass
        extra = os.path.join(root, "lib/apk/packages", n + ".list")
        if os.path.exists(extra):
            os.unlink(extra)
            nf += 1
    rcd = os.path.join(root, "etc/rc.d")
    if os.path.isdir(rcd):
        for f in os.listdir(rcd):
            fp = os.path.join(rcd, f)
            if os.path.islink(fp) and not os.path.exists(fp):
                os.unlink(fp)
                nrc += 1
    # 重写数据库：保留包的依赖行里去掉被删的包
    kept, patched = [], 0
    for n, p in pkgs.items():
        if n in drop:
            continue
        raw = p["raw"]
        if any(d in drop for d in p["deps"]):
            new = []
            for l in raw:
                if l.startswith("D:"):
                    toks = [t for t in l[2:].split() if re.split(r"[<>=]", t)[0] not in drop]
                    new.append("D:" + " ".join(toks))
                    patched += 1
                else:
                    new.append(l)
            raw = new
        kept.append(raw)
    with open(db, "w", encoding="utf-8", errors="surrogateescape") as fh:
        for r in kept:
            fh.write("\n".join(r) + "\n\n")
    # world 同步
    wp = os.path.join(root, "etc/apk/world")
    wl = open(wp, encoding="utf-8", errors="replace").read().split("\n")
    out, removed_w = [], []
    for l in wl:
        name = re.split(r"[<>=]", l.strip())[0] if l.strip() else ""
        if name and name in drop:
            removed_w.append(name)
        else:
            out.append(l)
    open(wp, "w", encoding="utf-8").write("\n".join(out))
    print("  删除 %d 个包（%d 个文件, %.1f MiB）; 改依赖行 %d 处; world 移除 %d 项"
          % (len(drop), nf, nsz / 1048576, patched, len(removed_w)))


# ---------------------------------------------------------------- 装包（用镜像自带的 apk）
def apk(root, args, extra_repos=()):
    """用镜像自带的 apk（musl 加载器）往 rootfs 里装包。

    注意：这里一律加 --no-scripts。包里的 post-install 脚本在非 root、非目标系统的
    环境里跑不起来（CI 上会 exit 127），而且它们的实际效果（uci 默认值、模块加载等）
    在设备首次开机时会由 uci-defaults 正常完成，所以离线构建阶段跳过它们最稳。
    """
    ld = os.path.join(root, "lib/ld-musl-x86_64.so.1")
    apkbin = os.path.join(root, "usr/bin/apk")
    cmd = [ld, "--library-path", "%s/lib:%s/usr/lib" % (root, root), apkbin,
           "--root", root, "--allow-untrusted", "--no-scripts"]
    for r in extra_repos:
        cmd += ["--repository", r]
    cmd += list(args)
    run(cmd)


def read_list(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def rewrite_mirrors(root):
    """CI 里把中文镜像换成 downloads.openwrt.org（其余非官方源注释掉），避免拉不到源。"""
    d = os.path.join(root, "etc/apk/repositories.d")
    if not os.path.isdir(d):
        print("  (没有 repositories.d，跳过镜像改写)")
        return
    for name in sorted(os.listdir(d)):
        fp = os.path.join(d, name)
        if not os.path.isfile(fp):
            continue
        out = []
        for line in open(fp, encoding="utf-8", errors="replace").read().split("\n"):
            st = line.strip()
            if st.startswith("https://mirrors.cernet.edu.cn/openwrt"):
                out.append(line.replace("https://mirrors.cernet.edu.cn/openwrt",
                                        "https://downloads.openwrt.org"))
            elif st.startswith("http") and "downloads.openwrt.org" not in st:
                out.append("# [slim] 暂时停用: " + line)
            else:
                out.append(line)
        open(fp, "w", encoding="utf-8").write("\n".join(out))
    print("  软件源已改写为 downloads.openwrt.org（第三方源已注释）")


def norm_apk(src, work, name=None):
    """把本地 apk 复制到工作目录：统一 .apk 后缀 + 644 权限（否则 apk 会把它当成包名）。"""
    d = os.path.join(work, "apks")
    os.makedirs(d, exist_ok=True)
    base = name or os.path.basename(src)
    if not base.endswith(".apk"):
        base += ".apk"
    dst = os.path.join(d, base)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    return dst


def stage_extras(root, args, work):
    repos = list(args.repo or [])
    extras_file = os.path.join(os.path.dirname(HERE), "slim", "extra-packages.txt")
    extras = [p for p in read_list(extras_file) if p != "luci-app-openclash"]
    # ruby / ruby-yaml + Wyse 3040 的 SDIO 无线链（清单见 slim/extra-packages.txt）
    apk(root, ["add"] + extras, extra_repos=repos)
    # OpenClash（官方 release 的真 apk 包）
    if args.openclash_apk:
        apk(root, ["add", norm_apk(args.openclash_apk, work)], extra_repos=repos)
    # OAF 新版（appfilter / kmod-oaf / luci-app-oaf / i18n）
    if args.oaf_dir:
        files = sorted(
            norm_apk(os.path.join(args.oaf_dir, f), work)
            for f in os.listdir(args.oaf_dir) if f.endswith(".apk"))
        kmods = [f for f in files if "kmod-oaf" in os.path.basename(f)]
        kdir = os.path.join(root, "lib/modules")
        ks = [d for d in sorted(os.listdir(kdir)) if re.match(r"^\d+(\.\d+)+", d)] if os.path.isdir(kdir) else []
        kern = ks[0] if ks else ""
        if kmods and kern and kern not in os.path.basename(kmods[0]):
            print("  ! OAF 内核模块(%s)与镜像内核(%s)不匹配，跳过 OAF 升级"
                  % (os.path.basename(kmods[0]), kern))
        else:
            # 必须一次性整组装：分开装会因为文件归属冲突失败
            # （appfilter 7.x 要覆盖 luci-app-oaf 6.x 的 usr/share/rpcd/acl.d/luci-app-oaf.json）
            try:
                apk(root, ["add"] + files, extra_repos=repos)
            except subprocess.CalledProcessError:
                # apk3 没有 --force-overwrite：先摘掉旧 OAF 的 4 个包记录再装新版本
                print("  ! 整组装失败，改为先移除旧 OAF 记录再安装")
                for old in ("luci-i18n-oaf-zh-cn", "luci-app-oaf", "kmod-oaf", "appfilter"):
                    if not remove_one(root, old):
                        pass
                apk(root, ["add"] + files, extra_repos=repos)
            args._oaf_v7 = True
    # DDNS-GO 二进制
    if args.ddns_go_tar:
        with tarfile.open(args.ddns_go_tar) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("ddns-go") and m.isfile())
            with t.extractfile(member) as src:
                dst = os.path.join(root, "usr/bin/ddns-go")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as o:
                    shutil.copyfileobj(src, o)
        os.chmod(os.path.join(root, "usr/bin/ddns-go"), 0o755)


# ---------------------------------------------------------------- 打包
def mksquash(root, out):
    tool = shutil.which("mksquashfs") or shutil.which("mksquashfs4")
    if not tool:
        sys.exit("✗ 需要 mksquashfs（apt install squashfs-tools）")
    if os.path.exists(out):
        os.unlink(out)
    nproc = str(os.cpu_count() or 4)
    run([tool, root, out, "-noappend", "-all-root", "-comp", "xz", "-b", "256K",
         "-Xbcj", "x86", "-no-progress", "-processors", nproc],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_p2(raw_img, sqfs):
    size = os.path.getsize(sqfs)
    if size >= P2_BYTES:
        sys.exit("✗ squashfs（%d）超过 p2 分区（%d）" % (size, P2_BYTES))
    with open(raw_img, "r+b") as f:
        f.seek(P2_OFF)
        with open(sqfs, "rb") as s:
            shutil.copyfileobj(s, f, 1 << 22)
        # 清零 p2 剩余空间，避免残留上一版数据（否则成品 gz 白白变大）
        left = P2_BYTES - size
        z = b"\0" * (1 << 20)
        while left > 0:
            n = min(left, len(z))
            f.write(z[:n])
            left -= n
    return size


def gzip_out(raw_img, out):
    if shutil.which("pigz"):
        run(["pigz", "-9", "-p", str(os.cpu_count() or 4), "-k", "-c", raw_img],
            stdout=open(out, "wb"))
    else:
        with open(raw_img, "rb") as i, open(out, "wb") as o:
            with gzip.GzipFile(fileobj=o, mode="wb", compresslevel=9) as g:
                shutil.copyfileobj(i, g, 1 << 22)


# ---------------------------------------------------------------- 自检
REQUIRED = [
    "sbin/init", "usr/bin/ddns-go", "etc/init.d/ddns-go", "etc/init.d/openclash",
    "usr/lib/lua/luci/controller/ddnsgo.lua", "usr/lib/lua/luci/controller/openclash.lua",
    "www/luci-static/argon/css/cascade.css", "etc/config/luci", "etc/smartd.conf",
]
FORBIDDEN = [
    "www/luci-static/bootstrap", "usr/share/ucode/luci/template/themes/bootstrap",
    "etc/uci-defaults/30_luci-theme-bootstrap", "etc/openclash/core/clash_meta",
    "etc/config/ddns", "usr/share/luci/menu.d/luci-app-ddns.json",
    "usr/bin/wg", "etc/init.d/unetd", "usr/bin/etherwake",
]


def verify(raw_img, sqfs, root):
    bad = []
    for f in REQUIRED:
        if not os.path.exists(os.path.join(root, f)):
            bad.append("缺少: " + f)
    for f in FORBIDDEN:
        if os.path.lexists(os.path.join(root, f)):
            bad.append("应删未删: " + f)
    # p2 区段与 squashfs 逐字节一致
    h1, h2 = hashlib.sha256(), hashlib.sha256()
    sz = os.path.getsize(sqfs)
    with open(raw_img, "rb") as f:
        f.seek(P2_OFF)
        left = sz
        while left > 0:
            b = f.read(min(1 << 22, left))
            if not b:
                break
            h1.update(b)
            left -= len(b)
    with open(sqfs, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h2.update(b)
    if h1.digest() != h2.digest():
        bad.append("p2 区段与 squashfs 不一致")
    if bad:
        print("✗ 自检失败:")
        for x in bad:
            print("   -", x)
        sys.exit(1)
    print("✓ 自检通过（p2 与 squashfs 逐字节一致，关键文件齐全、应删项已删）")


# ----------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream-url", default="")
    ap.add_argument("--upstream-file", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default="/tmp/istoreos-slim")
    ap.add_argument("--openclash-apk", default="")
    ap.add_argument("--ddns-go-tar", default="")
    ap.add_argument("--oaf-dir", default="")
    ap.add_argument("--repo", action="append", default=[],
                    help="额外的 apk 软件源（可多次）")
    ap.add_argument("--ensure-luci-compat", action="store_true", default=True)
    ap.add_argument("--rewrite-mirror", action="store_true", default=True,
                    help="把镜像内的 apk 源改写为 downloads.openwrt.org（CI 用）")
    ap.add_argument("--keep-raw", action="store_true")
    args = ap.parse_args()

    work = args.work
    os.makedirs(work, exist_ok=True)
    raw = os.path.join(work, "work.img")
    sqfs = os.path.join(work, "p2.sqfs")
    new_sqfs = os.path.join(work, "new.sqfs")
    root = os.path.join(work, "root")

    print("[1/6] 取得官方镜像")
    if args.upstream_file:
        src = args.upstream_file
    else:
        src = download(args.upstream_url, os.path.join(work, os.path.basename(args.upstream_url)))
    gunzip_to(src, raw)

    print("[2/6] 解包 rootfs")
    slice_p2(raw, sqfs)
    unsquash(sqfs, root)
    if args.rewrite_mirror:
        rewrite_mirrors(root)

    print("[3/6] 删包")
    remove_packages(root, os.path.join(os.path.dirname(HERE), "slim", "remove-packages.txt"))

    print("[4/6] 补装 / 放入 extras")
    stage_extras(root, args, work)

    print("[5/6] 应用补丁")
    patches.patch_all(root, os.path.join(os.path.dirname(HERE), "files"),
                      oaf_v7=getattr(args, "_oaf_v7", False))

    print("[6/6] 重打包")
    mksquash(root, new_sqfs)
    size = write_p2(raw, new_sqfs)
    print("  squashfs: %.1f MiB" % (size / 1048576))
    verify(raw, new_sqfs, root)
    gzip_out(raw, args.out)
    sha = hashlib.sha256(open(args.out, "rb").read()).hexdigest()
    with open(args.out + ".sha256", "w") as f:
        f.write("%s  %s\n" % (sha, os.path.basename(args.out)))
    print("✓ 完成: %s (%.1f MB)" % (args.out, os.path.getsize(args.out) / 1048576))
    print("  sha256:", sha)
    if not args.keep_raw:
        os.unlink(raw)


if __name__ == "__main__":
    main()
