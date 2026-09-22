-- Copyright (C) 2026 bluise
-- DDNS-GO 的 LuCI 入口(菜单: 服务 -> DDNS-GO)
--
-- 为什么用 Lua 而不是 ucode: 这个页面只是"状态 + 启停 + 打开配置页",
-- 用 LuCI 的 Lua 接口最省事(luci-compat / luci-lua-runtime 已在固件里)。
-- 页面本体: /usr/lib/lua/luci/view/ddnsgo.htm

module("luci.controller.ddnsgo", package.seeall)

function index()
	entry({"admin", "services", "ddnsgo"}, template("ddnsgo"), "DDNS-GO", 65)
end
