# Eagle HUD Reference — 飞鹰投后空间参考

当前版本实现了用户确认的**参考级空间 HUD**，游戏内绘制只依赖 **BingusSharedLoader v18 / API 1**。**Mod Options Menu v1.2** 是可选项：没装时每一层都开着；装了之后可以在暂停菜单里单独开关。不依赖 HD2Runtime、Enemy HP 或 HUD+。

这不是精确炸点预测：投影的是当前活动记录里的坐标，型号由原始 type 查固定构建的飞鹰目录；圈是目录 baseline，不是实际主机数值、完整覆盖区、必杀或安全边界。

## 游戏内行为

- 任务内、本机玩家已生成且出现已识别的飞鹰活动记录时，在该记录坐标画空间十字、型号与 `CALL REF` 标签。
- 约每 0.1 秒读取一次原生数据，每个 update 帧重新取得相机并投影。相机用和当前视角位置一致的那一个，不用会把一切投到准心的空相机。深度和屏幕坐标先抄成普通数字，避免临时向量被下一次分配盖掉后所有点叠在准心上。
- 每个爆炸阶段最多三圈：琥珀色内半径是满伤边界，红色外半径是伤害边缘，淡蓝色是更大的冲击波半径。烟雾不造成伤害，改成绿色，并且每枚弹单独画一圈，不再把弹与弹之间涂成一条实心胶囊。HD2Runtime 没有单独的外圈伤害值，径向衰减仍是游戏自己的行为；冲击波圈是同一条爆炸记录里最大的已证实半径，不是必杀范围。
- 文字 `BASELINE I/O/S m` 列出这三项。覆盖审计里 `bombsPerSalvo` 没有标量，弹匣数组也不是落点。所以不能按每枚弹的真实间距排布。
- **500kg**：只有 1 枚，触地 1 / 3 / 6 m，主爆炸 10 / 25 / **35 m**。35 m 是冲击波，不是满伤。仍画在呼叫点上。
- **110mm 火箭巢**：只画呼叫参考十字和 `TARGET UNKNOWN / NO IMPACT CIRCLE`，不在这里画攻击目标圈。
- **扫射**：活动记录里另一个点离呼叫点 1–80 米时，从呼叫点沿「该点 → 呼叫点」再向前画约 50 米的胶囊。宽度仍是单发 2.5 / 5 / 6.5 米。50 米来自 wiki 的大约值。
- **常规空袭、集束、汽油弹、毒气、烟雾**：同一锚点可用时，弹带横在呼叫点两侧，垂直于投掷方向。中心距按相邻外圈相切估算，不是实测间距。常规空袭 6 枚、外圈 10 米，中心线 100 米。集束 8 枚母弹、子弹外圈 6 米，中心线 84 米。汽油弹 5 枚、外圈 10 米，中心线 80 米。毒气 4 枚、外圈 12 米，中心线 72 米。烟雾是呼叫点附近 4 个绿色圈，每枚半径 12 米，相邻圆心相距 24 米，首尾圆心相距 72 米；不再拉成 8 圈。汽油弹、毒气、烟雾的弹数来自实机观察，wiki 和 HD2Runtime 都没有写。没有锚点时退回呼叫点上的单发圆。
- 默认超过当前视角 120 米不画。这个距离可以改，40 到 500 米，步进 10。关掉队友范围后，80 米以上不画；记录里没有已证实的投掷者，所以这只去掉远处信标，身边的队友仍会画。
- 当前记录消失、坐标读取失败、玩家生命周期或任务/世界变化时清除旧标记。字体读取失败不会清除十字和参考环。相机后方、屏幕外及菜单中的标记隐藏。
- 空闲时没有常驻面板或测试方块。标记只存在于当前活动记录的可观察期间，不延长到猜测的爆炸结束时刻。
- 手里拿着战略配备球并且按住鼠标右键时，才预判落点。只画红色伤害边界和落点十字，不画抛物线，也不画内圈、冲击波和文字。松开右键就隐藏。信标记录落到预判点 6 米内后，这份预览撤掉，改由原来的完整范围圈接上，红色边界不会跳到另一套位置。还没对上飞鹰型号时不画边界，避免猜一个半径。
- 投后的范围点和瞄准时的红色边界会从采样点上方往下打地面射线。打中的高度用来画这个点，所以边界可以跟着坡面起伏。每帧最多新打 48 条，打过的半米格子会记住。没打中时这个点仍用呼叫点高度。
- 文字优先读取 Enemy HP 1.1.2 使用的游戏字体。材质拥有者指针不再要求 8 字节对齐；为空或不可读时，标签改用引擎资源 `core/performance_hud/debug`。
- 范围圈、边界样式、圆周点数、队友范围和最远距离都可以改。没改时是方点、每圈 64 个点、队友范围开着、最远 120 米。游戏内菜单和管理器都能配，见下方「选项」。扫射和多弹的覆盖范围始终绘制。

### 身份与容量边界

不持久跟踪数组下标，不用“最近一次”或 picture 冒充出击 ID。每次采样重新生成当前记录视图，GUI ID 只是可复用的绘图句柄。同型号记录不会合并，但同坐标的图形可能重叠；不提供单次出击战果归因或命中 ETA。

原生数组上限为 512，界面最多处理 16 条同时存在的飞鹰参考记录。超过 16 条时明确记录 `reference_render_limit:16` 并清除参考图形，不悄悄只显示前十六条。未识别的类型不显示；未知半径保留为 `?`，不会补零。

## 安装与升级

游戏关闭时，通过现用管理器导入并启用：

1. `dist/dependencies/BingusSharedLoader/Bingus-Shared-Loader-v18.zip`
2. `dist/Eagle-HUD-Probe.zip`（管理器显示名 **Eagle HUD Reference / 飞鹰参考**）
3. 可选：[Mod Options Menu v1.2](https://github.com/CowboyBingus/ModOptionsMenu/tree/fd0160807b6cc75c2eeb74dbf9793a3c5d30deb6)。不要和 Vanilla Plus Megapack 里的同一菜单装两份。

沿用 `Probe` 文件名、资源入口和 GUID 是为了更新现有安装；内容已切换到空间参考 HUD。不要同时安装旧版和新版。Loader 保持 winning Wwise，既有 HUD boot 不变。助手没有自动部署、Purge、覆盖游戏文件或启动游戏。

## 选项

两处都能配，管的是同一套画面。

**Arsenal 配置档**：模组行上的 Options。第一项 **Eagle HUD Reference / 飞鹰参考** 必须开着，那是本体。其余每一组是单选，第一项是默认，不选也一样。改完关掉游戏再 **Deploy**。选项补丁占用 `patch_80` 到 `patch_103`，不要让别的模组部署同名文件。只拷三件套裸 patch 时没有这些选项，画面保持默认。

**游戏内**：暂停菜单 MODS 页的 **Eagle HUD Reference / 飞鹰参考**。改完按 **APPLY**（键盘 Tab）。已经 APPLY 过的值优先于管理器；没在游戏里改过的项用管理器部署的结果。没装 Mod Options Menu 时只有管理器的结果。

范围：

- **Full Damage / 满伤内半径**：橙色范围，代表满伤边界。
- **Damage Edge / 伤害外半径**：红色范围，代表伤害边缘。烟雾的覆盖范围也走这项。
- **Shockwave / 冲击半径**：淡蓝色范围，代表可能触发硬直的范围。
- **teammate range (experimental) / 是否显示队友信标范围（实验性）**：关掉后，队友丢出的飞鹰信标不再绘制。无法确认时，离当前视角超过 80 米的不画。
- **Max Range / 最远绘制距离**：40 到 500 米，默认 120。关闭队友范围后，无法确认是否为队友的呼叫仍限制在 80 米以内。管理器里只有 40、80、120、200、300、500 这几档；游戏内滑条可以按 10 米调整。

样式：

- **Draw Rings / 绘制边界**：关掉后不绘制范围，十字和文字可以单独留下。
- **Marker Style / 边界样式**：Square / 方点、Dash / 短划线、Tick / 刻度、Cross / 十字。短划顺着边界，刻度指向圈心。
- **Point Count / 边界绘制**：8 到 64，步进 8，默认 64。每个边界上的点数越少越稀。
- **Call Cross / 呼叫十字**：呼叫点上的黄色十字。
- **Type and Range / 型号与距离注释**：是否显示范围大小和致死提示的相关注释。
- **Aim Landing / 瞄准落点预判**：拿着战略配备球并按住右键时，只显示红色伤害边界。松开右键后隐藏。默认开着。

110mm 本来就没有圈，只受十字和文字影响。

HD2Runtime 已不是本模组的运行时依赖；若其他模组需要它，请保留。原始 Enemy HP 仅提供接口研究依据，不需要安装它。

### 裸 patch

```text
E:\hd2-mod\engle\dist\Eagle-HUD-Probe-patch\
  9ba626afa44a3aa3.patch_0
  9ba626afa44a3aa3.patch_0.stream
  9ba626afa44a3aa3.patch_0.gpu_resources
```

主 patch **70688 字节**；两个 sidecar 为空是正常的。与 ZIP 二选一安装时没有管理器选项。手工更新时沿用原探针占用的 `patch_0`，三件套同步命名；不要覆盖其他模组的同名文件。

- ZIP SHA-256：`B20A648D236F821EFCEAD3C50DBDE86FCBADB7D3EED438955D616BE1255FC8D9`
- 主 patch SHA-256：`17EAEA1572A011FDA5A140CA40F2EC82DAE562ACAE1B3ADFBD9A5DB2A77C36B6`
- 入口：`mods/engle/eagle_hud_probe`
- GUID：`a4601e99-90ac-41f8-89a7-6dcdce648678`

## 验证结果与限制

**64 项测试通过**，覆盖目录/未知值、重复记录与压缩重排、容量边界、真实字节布局读取、每帧相机更新、临时向量被覆盖后标记仍钉在世界坐标、空相机不再把圈收成准心上的一个点、500kg 分阶段、110mm 无圈、扫射向前 50 米、常规空袭、汽油弹、毒气、烟雾与集束横带且不沿投掷方向、烟雾为分圈绿色而非实心胶囊、超过默认 120 米的信标不画、改最远距离和关掉队友范围后的截止距离、越界/失读清理、未对齐字体指针、空字体指针时保留标记、任务和世界切换、菜单、字体临时对象及更新链保留。另覆盖每一层范围开关、四种边界样式、圆周点数变稀后扫射尽头仍在、管理器部署的标记，以及游戏内已应用的值盖过管理器。

重建后的 ZIP 已通过既有包检查，Lua 正文与裸 patch 字节一致。测试在隔离 LuaJIT 中使用合成内存和引擎接口，没有在游戏进程中注入或替换任何内容。

- `research/spatial_reference_smoke.json`：当前产物、测试范围和校验值。
- `research/spatial_reference_preview.png`：实际 Lua 绘图命令的离线预览，使用合成相机和系统字体，**不是游戏截图**。
- `research/findings.json`：逐项能力和证据边界。

基础 GUI 绘制和呼叫点圆此前已在游戏里看到。**扫射走廊还没有实机确认**；离线测试不证明那个额外点就是投掷者，也不证明 50 米就是弹着尽头。

## 日志与版本保护

沿用日志位置：

```text
%LOCALAPPDATA%/CowboyBingus/Helldivers2/Logs/EagleHudProbe.log
```

`initialized` 中 edition 为 `spatial_reference`、reader 为 `engle.windows_readonly`。只在状态变化、启动、模块哈希和停止时写日志，没有每秒心跳。

- `references_status`：当前匹配记录数、无记录、读取或容量错误。
- `font_status`：`live`，或 `fallback:` 加材质指针失败原因（含读到的 8 字节）。
- `hud_status`：可见参考数量，或相机/投影/临时池不可用原因。
- `references_cleared`：任务、本机玩家生命周期等清理原因。
- `options_status`：选项菜单注册成功，或注册被拒绝的原因。没装菜单时没有这条。
- `sample` 是原生采样序号，`frame` 是更新帧序号，`game_time` 是本模组累计的有效 update dt；都不是命中 ETA。

只支持已固定的 Steam build **25480438**。首次读取前校验 EXE 和 game.dll SHA-256；不同则停止，不扫描新偏移、不绕过保护。任何失读都会丢弃当前坐标，不沿用上一帧位置。

## 代码与构建

- `src/windows_readonly.lua`：独立 Win32/BCrypt 只读适配器；真实 Windows 当前工具进程 smoke 见 `research/standalone_adapter_smoke.json`。
- `src/eagle_hud_probe.lua`：版本保护、任务/玩家状态、活动记录与字体读取、0.1 秒采样、每帧渲染调度，以及可选的 Mod Options Menu 注册。
- `src/eagle_references.lua`：无历史关联的当前记录模型与容量/坐标校验。
- `src/spatial_renderer.lua`：相机投影、参考环、文字、GUI 生命周期和临时池恢复。边界样式和采样密度由绘制参数决定，缺省是方点、每圈 64 点。
- `tools/eagle_catalog.py`：把八种飞鹰、十个爆炸阶段的数据转换成 Lua 常量，保留未知值。
- `tools/build_addon.py`：合成一个 Lua 入口，再调用官方 Loader 打包器；不另造资源归档格式。

```text
python -B -m unittest discover -s tests -v
python -B tools/build_addon.py
```

离线测试需要 `lupa.luajit21`；游戏不需要 Python。生成入口在 `build/eagle_hud_probe.lua`，构建报告在 `build/build_report.json`。不要用旧命令直接打包未合成的入口源码，它需要构建注入的模块与数据。

## 来源

- [BingusSharedLoader](https://github.com/CowboyBingus/BingusSharedLoader/tree/3d7e3a120828178573ef1ee0a5c7eeae4a951865)：官方单资源归档打包器与启动入口。
- [HD2Runtime](https://github.com/SkyeShade/HD2Runtime/tree/96ab2d258d867a5df4f22bb7b3321d84d28de21d)：0.28.1 的目录与布局事实，**不是运行时依赖，也没有捆绑其运行时代码**。
- [指定 Enemy HP HUD 项目](https://github.com/life2015/HellDiver-Enemy-HP-HUD/blob/1f450f0b409f265ac99021dce1110de8c2bd1d07/src/presentation.lua)：独立 screen GUI、材质绑定、保留式绘制和清理的接口依据。
- 用户提供的 Enemy HP 1.1.2：`research/original_enemy_hp_local.lua:745-816`，给出了字体字段、相机获取及 `Camera.world_to_screen(camera, point)` 的实际调用。来源与哈希见 `research/original_enemy_hp_provenance.json`。
- [Mod Options Menu v1.2](https://github.com/CowboyBingus/ModOptionsMenu/tree/fd0160807b6cc75c2eeb74dbf9793a3c5d30deb6)：暂停菜单里的开关。不随本 ZIP 捆绑；`api` 必须是 1。

相机从 main_world 的 `core/units/camera` 单元取得，GUI 使用第一个非 main_world 世界；两者不混用。每帧重新取得相机，临时向量和 IdString64 不跨帧缓存。

原始模组源码、提取的 HUD boot 与第三方资源仅留在 research 供本地研究，未包含在 ZIP。不要把整个 research/vendor 目录当成可再分发模组。历史输入基线保留，不删除或改写来掩盖用户部署变化。
