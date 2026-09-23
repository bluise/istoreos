# iStoreOS 精简版（自动构建）

把**官方 iStoreOS x86_64 镜像**自动做成「精简版」：232 MB -> 62 MB，删掉用不到的包，
补回要用的插件，只做少量界面调整。内核、iStoreOS 应用、升级机制都是官方原装（不是源码编译）。

适用范围：x86_64 软路由 / 小主机（含 Dell Wyse 3040 这类瘦客户机，已补 SDIO 无线驱动）。

最新固件：**Releases** 里的 `iStoreOS-efi.img.gz`（带 sha256，构建完自动发布，只保留最新一版）。

---

## 一、包含的插件与功能

| 内容 | 说明 |
|---|---|
| DDNS-GO 6.17.7 | 静态二进制 + LuCI 页面 + 9876 独立网页界面，首启自动运行 |
| OpenClash 0.47.156 | 界面 + 依赖(ruby) + 规则数据(GeoSite.dat / Country.mmdb)；**不含内核**，首次使用在页面里自己下 |
| OAF v7.0.1 | 应用过滤 / 家长控制（appfilter / kmod-oaf / luci-app-oaf / 中文包）；kmod 与内核 6.12.94 绑定，内核不匹配时自动跳过 |
| Wyse 3040 无线 | kmod-sdhci / kmod-mwifiex-sdio / mwifiex-sdio-firmware |
| 主题 | 只保留 Argon（light） |

官方原有能力全部保留：首页(quickstart)、网络向导、防火墙、DHCP/DNS、应用商店、
磁盘管理、UPnP、PassWall2、TTYD、CPU 调频、备份/刷写、在线升级。

## 二、去掉了什么

删掉 **330 个包**（完整清单 `recipe/remove-packages.txt`，逐项说明见发布目录的 `精简清单.txt`），
主要是：Docker 全家桶、NAS 服务端（Samba/NFS/WebDAV）、Perl、4G 模组、GPU 与无关网卡固件、
易有云 linkease/ddnsto、ddns-scripts 系列、mdadm + smartmontools、WireGuard 界面、
异地组网 unet、网络唤醒 WOL、CIFS 挂载、luci-theme-bootstrap 等。

## 三、界面调整

- 首页去掉 4 张卡片：存储服务、下载服务、远程域名、配置模块
- 首页「文件管理」⋮ 菜单去掉 RAID管理 / S.M.A.R.T.；「网络存储」下也不再显示这两项
- 「系统 → Argon主题设置」改名「系统 → 主题设置」；「服务 → UPnP」标题改为 `UPnP IGD`
- 首页「家长控制」链接跟随 OAF 版本自动适配
- 保留最小 `/etc/smartd.conf`：首页磁盘信息要读它（SMART 功能本身已删）

## 四、默认网络

- LAN 静态 `10.0.0.1/24`，DHCP 池 `10.0.0.100-249`
- 双网口：第一口(eth0)=WAN(自动获取)、第二口(eth1)=LAN；单网口：该口=LAN
- dnsmasq 带公共上游 DNS（223.5.5.5 / 119.29.29.29）
- 这些默认值只在**原厂默认配置**的机器上落一次（刚装机的机器）；已经自己配过网络的机器一字不改，
  升级时网口角色、WAN 设置、无线中继原样保留

实现方式：一个独立的 `/etc/uci-defaults/99-default-network`（OpenWrt 标准首启机制，
排在官方 `09_istoreos` 之后执行，跑完即被系统删除）；官方文件与镜像**不做任何运行时改动**，
没有常驻服务、没有开机自检。

## 五、OTA 在线升级指向本仓库

`/lib/upgrade/ota.sh` 的 OTA 地址改为 `https://github.com/bluise/istoreos/releases/latest/download`，
并随每次构建发布 `version.latest.v2` / `version.index.v2` 清单（格式与官方一致）。

每次构建还会给固件打一个独立的版本号尾段（`--build-stamp`，形如 `25.12.5-202609230235`，
官方原版是 `25.12.5-2026091113`），写进固件的 `DISTRIB_REVISION` 并与 OTA 清单一一致，
这样设备上的「系统 → OTA」才能识别出"有新版本"；升级后设备版本等于该尾段，OTA 显示已是最新。

效果：误点「系统 → OTA」也只会刷本项目产出的固件；取不到 GitHub 时只会报错。

## 六、构建

**用 GitHub Actions（推荐）**

Actions →「构建 iStoreOS 精简版镜像」：

- 往 master 推 `recipe/`、`files/` 或本工作流的改动 —— 自动构建
- 每天 04:10（北京时间）检查官方是否出新版，版本没变则跳过
- 手动 Run workflow（参数可留空：自动取官方当前最新镜像，多源重试并校验 sha256）

约 4 分钟出结果，发布 Release 并只保留最新一版。

**本地跑**

```sh
# 依赖：python3 + squashfs-tools（mksquashfs/unsquashfs）+ pigz + curl
python3 recipe/build.py \
  --upstream-file 官方.img.gz \
  --openclash-apk openclash.apk \
  --ddns-go-tar  ddns-go.tar.gz \
  --oaf-dir       files/oaf \
  --out iStoreOS-efi.img.gz
```

## 七、仓库结构

```
.github/workflows/build.yml   工作流：下载官方镜像 + 插件 -> 跑配方 -> 出镜像 -> 发 Release
recipe/build.py               主流程：解包 -> 删包 -> 补装 -> 打补丁 -> 重打包 -> 自检
recipe/patches.py             界面/配置补丁（每处都带断言，上游改版会立刻构建失败）
recipe/remove-packages.txt    要删的 330 个包
recipe/extra-packages.txt     要补装的包
files/netpolicy/99-default-network  默认网络（独立首启脚本）
files/ddns-go/*               DDNS-GO 的 LuCI 集成文件
files/oaf/*                   OAF v7 的 4 个 apk（内核不匹配时自动跳过）
```

## 八、刷写

- 已装 iStoreOS 的机器：系统 →「备份/刷写固件」选 `img.gz`（只换 p1/p2，overlay 不动，配置与插件保留）
- 新装机：`gunzip -c iStoreOS-efi.img.gz | sudo dd of=/dev/sdX bs=4M conv=fsync`，或用 balenaEtcher
