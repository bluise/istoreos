# iStoreOS 精简版（自动构建）

把**官方 iStoreOS x86_64 镜像**自动变成「精简版」：体积从 ~232 MB 降到 ~63 MB，
去掉用不到的包，装回真正要用的插件，并把几处界面做精简。

上游出新版时：**改一下工作流里的 `upstream_url`，点一下 Run workflow**，就能拿到新的精简镜像，
不需要重新编译，也不用重新刷机（用网页在线升级即可）。

---

## 一、怎么用（上游更新时）

1. 打开本仓库 → **Actions** → 左侧 **构建 iStoreOS 精简版镜像** → 右侧 **Run workflow**
2. 填参数：
   | 参数 | 说明 |
   |---|---|
   | `upstream_url` | 官方新镜像的完整 URL（在 koolcenter 下载页复制；默认是当前 25.12.5 那条） |
   | `openclash_version` | OpenClash 版本，默认 `0.47.156`（上游 vernesong/OpenClash） |
   | `ddns_go_version` | DDNS-GO 版本，默认 `6.17.7`（上游 jeessy2/ddns-go） |
   | `use_oaf_v7` | 是否把 OAF 升到 v7（默认开；内核版本不匹配会自动跳过） |
   | `make_release` | 构建成功后自动发布 Release（默认开） |
3. 等 10~20 分钟 → 到 **Releases** 下载 `iStoreOS-efi.img.gz`
4. **刷机（在线升级，不用 U 盘）**：
   系统 → **备份/刷写固件** → 选这个 `img.gz` → 刷写 → 自动重启
   - 只重写引导分区 + rootfs，**overlay 分区不动** → 配置和你在应用商店装的插件都保留
   - ⚠️ 不要点「系统 → OTA」那个按钮：它下的是**官方完整镜像**，会把这些精简全部冲掉
5. 全新装机（空盘）：`gunzip -c iStoreOS-efi.img.gz | sudo dd of=/dev/sdX bs=4M conv=fsync`
   或直接用 balenaEtcher 写这个 img.gz

---

## 二、精简了什么

- **删掉 330 个包**（Docker 全家桶、GPU 显卡固件与驱动、NAS 服务端 Samba/NFS/WebDAV、
  无关无线/网卡固件、易有云 linkease/ddnsto、Perl 运行库、4G 模组、luci-theme-bootstrap、
  ddns-scripts 系列、mdadm+smartmontools 系列、WireGuard 界面、unet 异地组网、
  网络唤醒 WOL、CIFS 网络共享挂载 等）
  完整清单与逐项原因：`recipe/remove-packages.txt`（本仓库文档里也有详细版）
- **额外装回来**（`recipe/extra-packages.txt`）：
  | 内容 | 用途 |
  |---|---|
  | DDNS-GO v6.17.7（静态二进制 + LuCI 页面 + 9876 网页界面 + 开机自启） | 动态域名 |
  | OpenClash 0.47.156（界面 + 规则数据 GeoSite.dat/Country.mmdb + ruby 依赖） | 透明代理（**不含内核**，首次使用在页面里下） |
  | ruby + ruby-yaml 等 | OpenClash 处理 yaml 用 |
  | `kmod-sdhci` / `kmod-mwifiex-sdio` / `mwifiex-sdio-firmware` | Dell Wyse 3040 的 SDIO 无线 |
  | OAF v7（appfilter / kmod-oaf / luci-app-oaf / i18n，可选） | 应用过滤、家长控制 |
- **界面精简**（`recipe/patches.py`，每处都带断言，上游改版会立刻报错）：
  - 首页去掉 4 张卡片：存储服务、下载服务、远程域名、配置模块
  - 首页「文件管理」旁 ⋮ 菜单去掉 RAID管理 / S.M.A.R.T.
  - 「系统 → Argon主题设置」改名「主题设置」；只保留 Argon 主题
  - 「网络存储」下不再显示 磁盘阵列 / S.M.A.R.T.；服务菜单 UPnP 标题改短为 `UPnP IGD`
  - 补回 `/etc/smartd.conf`（iStoreOS 首页的守护进程要读它，缺了首页"磁盘信息"会整块空白）
  - 磁盘管理去掉对 smartctl 的强制依赖（否则删掉 SMART 工具后整个磁盘管理页都不注册）
  - 首页「家长控制」链接跟随 OAF 版本（v7 用 `/services/oaf`，v6 保持原样）

---

## 三、仓库结构

```
.github/workflows/build.yml   工作流：下载官方镜像 + 插件 → 跑配方 → 出镜像 → 发 Release
recipe/build.py                 主流程：解包 → 删包 → 补装 → 打补丁 → 重打包 → 自检
recipe/patches.py              所有界面/配置补丁（带断言，改不上就报错）
recipe/remove-packages.txt     要删的 330 个包（相对官方镜像）
recipe/extra-packages.txt      要补装的包
files/ddns-go/*              DDNS-GO 的 LuCI 集成文件（init.d / config / uci-defaults / 控制器 / 页面 / ACL）
files/oaf/*                  OAF v7 的 4 个 apk（内核不匹配时自动跳过）
```

---

## 四、本地怎么跑（不想用 Actions 时）

```sh
# 依赖：python3 + squashfs-tools（mksquashfs/unsquashfs）+ pigz + curl
python3 recipe/build.py \
  --upstream-file 官方.img.gz \
  --openclash-apk openclash.apk \
  --ddns-go-tar  ddns-go.tar.gz \
  --oaf-dir       files/oaf \
  --out iStoreOS-efi.img.gz
# 也可以直接给 URL：--upstream-url https://.../istoreos-xxx.img.gz
```

脚本会自己完成：解压 → 从 p2 分区切 squashfs → unsquashfs → 删包（数据库记录/文件/world/依赖行一起清）
→ 用镜像自带的 apk 补装（可 `--rewrite-mirror` 把源换成 downloads.openwrt.org，CI 里默认开）
→ 打补丁 → mksquashfs 重打包 → 写回 p2（并清零 p2 剩余空间）→ 自检 → gzip。

自检包括：关键文件齐全、应删项确实不存在、**p2 区段与 squashfs 逐字节一致**、解压后长度正确。

---

## 五、注意点（踩过的坑）

1. **不要用 `apk del` 手删某些包**：`apk del` 会级联删除依赖它的包。例如 `quickstart`
   声明依赖 `mdadm`/`smartd`/`smartmontools`，直接 `apk del` 会把 `quickstart` 一起删掉
   —— 而 **iStoreOS 的首页就是 quickstart 提供的**，一删首页就没了。
   本配方用的是"改数据库依赖记录 + 按文件清单精确删文件"，所以首页完好。
2. **上游镜像升级会把这些改动全部冲掉**（p2 被替换），这就是本仓库存在的意义：
   用工作流重新产出精简镜像，而不是直接刷官方镜像。
3. **应用商店里点"更新"可能把删掉的依赖装回来**（比如更新 quickstart 时会重新拉 mdadm/smartmontools）。
   发现菜单项又冒出来了，多半是这个原因。
4. **OpenClash 不含内核**：第一次用要在页面 →「内核」里自己下（先在「常规设置 → GitHub 地址代理」
   填个可用加速地址）。
5. **OAF 的 kmod 与内核版本绑定**：`files/oaf/kmod-oaf-6.12.94-r1.apk` 只适用于 6.12.94 内核；
   上游换内核后工作流会自动跳过 OAF 升级（首页「家长控制」链接也会自动保持 v6 的写法）。

---

## 六、已验证

本仓库的工作流已实际跑通并验证（2026-09-22）：

- Actions 运行一次约 **4 分钟**（docker 免费额度友好；公开仓库不消耗额度）
- 产出 `iStoreOS-efi.img.gz` 约 **62.2 MB**，并在 Release 里带 sha256
- 把该产物在 KVM 虚拟机里真机启动验证：
  - 能启动；首页 200；**首页"磁盘信息"正常**（守护进程磁盘接口 200 且返回真实磁盘数据）
  - DDNS-GO 首启即在运行（进程 1 个、9876 网页界面可访问）
  - OpenClash 页面 200、规则数据 GeoSite.dat 在（内核未内置，按需在页面里下）
  - 磁盘管理 200、OAF 页面 200（OAF 已升到 v7.0.1）、主题只剩 Argon
  - 菜单里 WireGuard / 异地组网 / 网络唤醒 / CIFS 挂载 / ddns-scripts 均为 0 处

## 七、当前已验证的成品

- `iStoreOS-25.12.5-x86_64-slim-efi.img.gz`（约 63 MB，官方 232 MB）
- 实测（KVM 虚拟机真跑）：能启动、首页磁盘信息正常、DDNS-GO 首启即在运行（9876 可访问）、
  OpenClash 页面与状态接口 200、磁盘管理 200、OAF 页面 200、主题只剩 Argon、
  **网页升级（sysupgrade）后插件与配置全部保留**（实测换整份新 rootfs，nano/配置/hostname 都在）
