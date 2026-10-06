-- HD2-Addon: mods/engle/eagle_hud_probe
-- Current-record spatial references, not predicted bomb impacts or persistent sortie identities.
-- The build embeds the independent reader, renderer, collector and baseline catalog.
local create_readonly_runtime, create_renderer, create_references, catalog, create_aim_preview = ...
local existing = rawget(_G, 'EngleEagleHudProbe')
if existing then return existing end
local state = {status = 'initializing', sequence = 0, frame = 0, mission = false, epoch = 0, game_time = 0}
rawset(_G, 'EngleEagleHudProbe', state)
local EXE = 'F5FEE03DCFDB2E553A4752C283590950AC13316B376D8196AA556FF0400D5F06'
local GAME = '2E2C3B7C2500646DADD5F2B4C6E0504DBB7E7896139F64CDDC0D1813C718F51E'
local log_file
local log_failed = false
local function quote(s)
    return '"' .. tostring(s):gsub('[%z\1-\31\\"]', function(c)
        return string.format('\\u%04x', c:byte())
    end) .. '"'
end
local function json(v)
    local t = type(v)
    if t == 'nil' then return 'null' end
    if t == 'boolean' then return tostring(v) end
    if t == 'number' then
        if v ~= v or v == math.huge or v == -math.huge then return 'null' end
        return string.format('%.17g', v)
    end
    if t ~= 'table' then return quote(v) end
    local keys, out = {}, {}
    for k in pairs(v) do keys[#keys + 1] = k end
    table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
    for _, k in ipairs(keys) do out[#out + 1] = quote(k) .. ':' .. json(v[k]) end
    return '{' .. table.concat(out, ',') .. '}'
end
local function fallback(message)
    if type(print) == 'function' then pcall(print, '[EagleHudProbe] ' .. message) end
end
local function log(kind, data)
    local line = json({event = kind, sample = state.sequence, frame = state.frame, game_time = state.game_time,
        mission = state.epoch, data = data})
    if log_file then
        local ok, why = pcall(function()
            assert(log_file:write(line .. '\n'))
            assert(log_file:flush())
        end)
        if ok then return end
        pcall(log_file.close, log_file)
        log_file = nil
        if not log_failed then fallback('LOG_UNAVAILABLE: ' .. tostring(why)); log_failed = true end
    end
    -- Without a file, report setup/errors only; never flood the shared engine log.
    if kind == 'stopped' or kind == 'initialized' then fallback(line) end
end
local ffi, runtime, game_base, exe_handle, game_handle
local native_stopped, next_module_check = false, 0
local reasons = {}
local references, renderer, renderer_unavailable, aim, armed
local current_models, current_fonts, current_world
local clear_references
local function changed_reason(channel, reason)
    if reasons[channel] ~= reason then
        reasons[channel] = reason
        log(channel .. '_status', {reason = reason})
    end
end
local function stop_native(reason)
    native_stopped = true
    state.status = 'native_stopped'
    if clear_references then clear_references() end
    log('stopped', {branch = 'native', reason = reason})
end
local buffers = {}
local function read_buffer(rt, key, address, size, capacity)
    if type(address) ~= 'number' or address < 65536 or address % 1 ~= 0
        or address + size > 140737488355327 then return nil, 'invalid_address' end
    if size < 0 or size > capacity then return nil, 'invalid_buffer_length' end
    local b = buffers[key]
    if not b then b = ffi.new('uint8_t[?]', capacity); buffers[key] = b end
    if size > ffi.sizeof(b) then return nil, 'buffer_capacity_exceeded' end
    if not rt.read_into(address, size, b) then return nil, 'unreadable_or_short_read' end
    return b
end
local function u32(b, o) return tonumber(ffi.cast('const uint32_t *', b + o)[0]) end
local function u64hex(b, o) return string.format('%08x%08x', u32(b, o + 4), u32(b, o)) end
local function pointer(rt, address, key)
    local b, why = read_buffer(rt, key, address, 8, 8)
    if not b then return nil, why end
    local hi, lo = u32(b, 4), u32(b, 0)
    if hi > 32767 then return nil, 'invalid_pointer' end
    local p = hi * 4294967296 + lo
    if p < 65536 or p % 8 ~= 0 then return nil, 'null_or_unaligned_pointer' end
    return p
end
local function f32(b, o)
    local v = tonumber(ffi.cast('const float *', b + o)[0])
    if v ~= v or v == math.huge or v == -math.huge then return nil end
    return v
end
-- Build-locked data facts from HD2Runtime's event_natives catalog, not its runtime.
local STATE_NAMES = {'None', 'Splash', 'TitleScreen', 'Ship', 'Mission', 'PrepareShip',
    'PrepareMission', 'PrepareTestLevel', 'PrepareDebugMission'}
local function sample_game_state(rt, base)
    local game, why = pointer(rt, base + 53633856, 'state_game_pointer')
    if not game then return nil, why end
    local b; b, why = read_buffer(rt, 'state_value', game + 705052, 4, 4)
    if not b then return nil, why end
    local value = u32(b, 0)
    if value > 16 then return nil, 'game_state_out_of_bounds' end
    local result = {state = value, name = STATE_NAMES[value + 1] or 'Unknown', mission = false}
    if value ~= 4 then return result end
    local mode; mode, why = pointer(rt, base + 53634720, 'state_mode_pointer')
    if not mode then return nil, why end
    local h; h, why = read_buffer(rt, 'state_mode_header', mode, 68, 68)
    if not h then return nil, why end
    if u32(h, 8) ~= 1 then return nil, 'mission_mode_not_ready' end
    local descriptor; descriptor, why = pointer(rt, mode + 56, 'state_descriptor_pointer')
    if not descriptor then return nil, why end
    local d; d, why = read_buffer(rt, 'state_descriptor', descriptor, 24, 24)
    if not d then return nil, why end
    result.mission, result.mode_type = true, u32(h, 64)
    result.mode_entity, result.host = u32(d, 8), u32(d, 20) % 2 == 1
    result.identity = tostring(mode) .. ':' .. tostring(descriptor) .. ':' .. result.mode_entity
    return result
end
local function sample_local_player(rt, base)
    local user, why = pointer(rt, base + 55037680, 'local_user_pointer')
    if not user then return nil, why end
    local peer; peer, why = read_buffer(rt, 'local_peer', user + 45976, 8, 8)
    if not peer then return nil, why end
    local key = u64hex(peer, 0)
    local manager; manager, why = pointer(rt, base + 53634152, 'local_players_pointer')
    if not manager then return nil, why end
    local p; p, why = read_buffer(rt, 'local_players', manager, 936, 936)
    if not p then return nil, why end
    local count = u32(p, 132)
    if count > 4 then return nil, 'local_player_count_out_of_bounds' end
    for seat = 0, count - 1 do
        local at = 712 + seat * 56
        if u64hex(p, at) == key then
            local lifecycle = u32(p, at + 24)
            local descriptor; descriptor, why = pointer(rt, manager + 232 + seat * 8, 'local_descriptor_pointer')
            if not descriptor then return nil, why end
            local entity; entity, why = read_buffer(rt, 'local_entity', descriptor + 8, 4, 4)
            if not entity then return nil, why end
            local entity_id = u32(entity, 0)
            local avatar; avatar, why = read_buffer(rt, 'local_avatar', manager + 936 + seat * 32, 4, 4)
            if not avatar then return nil, why end
            local network_id = u32(avatar, 0)
            return {peer = key, seat = seat, entity = entity_id, lifecycle = lifecycle,
                avatar_network_id = network_id, spawned = lifecycle == 3 and network_id ~= 32767,
                identity = key .. ':' .. entity_id .. ':' .. network_id .. ':' .. lifecycle}
        end
    end
    return nil, 'local_player_not_listed'
end
local function sample_active(rt, base)
    local manager, why = pointer(rt, base + 0x33266B0, 'active_pointer')
    if not manager then return nil, why end
    local header; header, why = read_buffer(rt, 'active_header', manager, 128, 128)
    if not header then return nil, why end
    local count = u32(header, 0x34)
    if count > 512 then return nil, 'active_count_out_of_bounds' end
    if count == 0 then return {}, nil end
    local array; array, why = pointer(rt, manager + 0x78, 'active_array_pointer')
    if not array then return nil, why end
    local b; b, why = read_buffer(rt, 'active_rows', array, count * 64, 512 * 64)
    if not b then return nil, why end
    local rows = {}
    for i = 0, count - 1 do
        local o = i * 64
        local x, y, z = f32(b, o + 16), f32(b, o + 20), f32(b, o + 24)
        if not x or not y or not z then return nil, 'active_nonfinite_position' end
        local ax, ay, az = f32(b, o + 48), f32(b, o + 52), f32(b, o + 56)
        if not ax or not ay or not az then return nil, 'active_nonfinite_anchor' end
        rows[#rows + 1] = {native_type_candidate = u32(b, o + 12),
            x = x, y = y, z = z, ax = ax, ay = ay, az = az}
    end
    return rows, nil
end
local function mul32(a, c)
    local low, high = c % 65536, math.floor(c / 65536)
    return (a * low + ((a * high) % 65536) * 65536) % 4294967296
end
local function sample_avatar(rt, base, network_id)
    -- HD2Runtime playerAvatars: network id -> avatar entity. The player-list entity does not change on death.
    if type(network_id) ~= 'number' or network_id == 32767 or network_id < 0 then return nil end
    local manager = pointer(rt, base + 54968216, 'avatar_entities_pointer')
    if not manager then return nil end
    local header = read_buffer(rt, 'avatar_map_header', manager + 15871688, 20, 20)
    if not header then return nil end
    local hi, lo = u32(header, 4), u32(header, 0)
    if hi > 32767 then return nil end
    local slots = hi * 4294967296 + lo
    if slots < 65536 or slots % 8 ~= 0 then return nil end
    local capacity, empty, multiplier = u32(header, 8), u32(header, 12), u32(header, 16)
    if capacity == 0 or capacity > 1048576 or capacity % 2 ~= 0 then return nil end
    local start = mul32(network_id, multiplier)
    local limit = capacity
    if limit > 4096 then limit = 4096 end
    for probe = 0, limit - 1 do
        local bytes = read_buffer(rt, 'avatar_map_slot', slots + ((start + probe) % capacity) * 8, 8, 8)
        if not bytes then return nil end
        local key = u32(bytes, 0)
        if key == network_id then
            local slot = u32(bytes, 4)
            if slot > 1048576 then return nil end
            local entity = read_buffer(rt, 'avatar_entity', manager + 15937312 + slot * 24, 4, 4)
            if not entity then return nil end
            local id = u32(entity, 0)
            if id == 0 then return nil end
            return id
        end
        if key == empty then return nil end
    end
    return nil
end
local function sample_comms(rt, base)
    -- Same ring the live logs decoded: kind 20, call xy, owner entity. Unreadable means no owner, not a failed sample.
    local list = pointer(rt, base + 55037488, 'comms_pointer')
    if not list then return nil end
    local ends = read_buffer(rt, 'comms_ends', list + 8, 8, 8)
    if not ends then return nil end
    local first, last = u32(ends, 0), u32(ends, 4)
    if first >= 128 or last >= 128 then return nil end
    local calls, index, seen = {}, first, 0
    while index ~= last and seen < 128 do
        local row = read_buffer(rt, 'comms_row', list + 16 + index * 88, 88, 88)
        if not row then return nil end
        local x, y = f32(row, 4), f32(row, 8)
        local lifetime, elapsed = f32(row, 16), f32(row, 20)
        local owner = u32(row, 24)
        if u32(row, 0) == 20 and owner ~= 0 and x and y and lifetime and elapsed and elapsed < lifetime then
            calls[#calls + 1] = {x = x, y = y, owner = owner}
        end
        seen = seen + 1
        index = index + 1
        if index >= 128 then index = 0 end
    end
    return calls
end
local function attach_throwers(rows, calls)
    for i = 1, #rows do
        local row, owner, ambiguous = rows[i], nil, false
        for j = 1, #calls do
            local call = calls[j]
            if math.abs(call.x - row.x) <= 0.05 and math.abs(call.y - row.y) <= 0.05 then
                if owner == nil then owner = call.owner
                elseif owner ~= call.owner then ambiguous = true; break end
            end
        end
        if owner and not ambiguous then row.thrower = owner end
    end
end
local function font_hash(rt, key, address)
    local b, why = read_buffer(rt, key, address, 8, 8)
    if not b then return nil, why end
    local hex = u64hex(b, 0)
    if hex == '0000000000000000' then return nil, 'font_hash_not_ready' end
    return hex
end
local function sample_fonts(rt, base)
    -- Enemy HP 1.1.2 reads the same three values and accepts any canonical pointer.
    -- Live play stores an unaligned material owner; the shared 8-byte pointer check rejects it.
    local function live()
        local font, why = font_hash(rt, 'hud_font', base + 0x3772268)
        if not font then return nil, why end
        local atlas; atlas, why = font_hash(rt, 'hud_atlas', base + 0x3772ee8)
        if not atlas then return nil, why end
        local owner_bytes; owner_bytes, why = read_buffer(rt, 'hud_material_owner', base + 0x37c5478, 8, 8)
        if not owner_bytes then return nil, why end
        local hi, lo = u32(owner_bytes, 4), u32(owner_bytes, 0)
        local raw = u64hex(owner_bytes, 0)
        if hi > 32767 then return nil, 'invalid_pointer:' .. raw end
        local owner = hi * 4294967296 + lo
        if owner < 65536 then return nil, 'null_pointer:' .. raw end
        local material; material, why = font_hash(rt, 'hud_material', owner + 24)
        if not material then return nil, tostring(why) .. ':' .. raw end
        return {font = font, material = material, atlas = atlas, source = 'live'}
    end
    local fonts, why = live()
    if fonts then return fonts end
    -- HUD+ uses this engine pair when the live font cannot be read. Markers do not depend on it.
    return {font = 'core/performance_hud/debug', material = 'core/performance_hud/debug',
        source = 'fallback', detail = why or 'live_font_unavailable'}
end
local function main_world()
    local sr = rawget(_G, 'stingray')
    local app = type(sr) == 'table' and sr.Application
    if type(app) ~= 'table' or type(app.main_world) ~= 'function' then
        return nil, 'main_world_api_unavailable'
    end
    local ok, world = pcall(app.main_world)
    if not ok or world == nil then return nil, 'main_world_unavailable' end
    return world
end
local function ensure_modules(now)
    if native_stopped then return false end
    if not game_base and now < next_module_check then return false end
    local ok, why = pcall(function()
        local exe, game = runtime.module('helldivers2.exe'), runtime.module('game.dll')
        if exe == nil or game == nil then
            game_base, exe_handle, game_handle = nil, nil, nil
            next_module_check = now + 5
            state.status = 'waiting_for_modules'
            clear_references()
            changed_reason('modules', 'waiting_for_modules')
            return
        end
        if exe ~= exe_handle or game ~= game_handle then
            game_base = nil
            local exehash, exe_error = runtime.module_hash(exe)
            assert(exehash, 'EXE_HASH_UNAVAILABLE:' .. tostring(exe_error))
            local gamehash, game_error = runtime.module_hash(game)
            assert(gamehash, 'GAME_HASH_UNAVAILABLE:' .. tostring(game_error))
            log('module_hashes', {exe = exehash, game = gamehash})
            assert(exehash == EXE and gamehash == GAME, 'UNSUPPORTED_BUILD')
            game_base = runtime.address(game)
            assert(type(game_base) == 'number' and game_base >= 65536, 'invalid_game_base')
            exe_handle, game_handle = exe, game
            changed_reason('modules', 'fingerprints_verified')
            state.status = 'observing'
        end
    end)
    if not ok then stop_native(tostring(why)); return false end
    return game_base ~= nil
end
clear_references = function()
    current_models, current_fonts, current_world = nil, nil, nil
    if renderer then
        local ok, cleared, why = pcall(renderer.clear)
        if not ok or not cleared then
            changed_reason('hud', 'clear_unavailable:' .. tostring(ok and why or cleared))
        end
    end
end
local function reset(reason)
    armed = nil
    clear_references()
    renderer_unavailable = nil
    log('references_cleared', {reason = reason})
end
local display = {inner = true, outer = true, shock = true, dots = true, cross = true, labels = true,
    squad = true, aim = true, mark = 1, samples = 64, range = 120}
local option_rows = {
    {key = 'inner', id = 'engle.eagle_hud.inner', kind = 'toggle', default = true, label = 'Full Damage / 满伤内半径',
        description = 'Orange square dots. Full-damage edge. / 橙色范围。代表满伤边界'},
    {key = 'outer', id = 'engle.eagle_hud.outer', kind = 'toggle', default = true, label = 'Damage Edge / 伤害外半径',
        description = 'Red square dots, damage edge. Eagle Smoke coverage uses this too. / 红色范围，代表伤害边缘。注意，飞鹰烟雾的烟雾覆盖范围绘制也由此项控制。'},
    {key = 'shock', id = 'engle.eagle_hud.shock', kind = 'toggle', default = true, label = 'Shockwave / 冲击半径',
        description = 'Pale blue square dots. Possible stagger range. / 淡蓝色范围。代表可能触发硬直的范围'},
    {key = 'dots', id = 'engle.eagle_hud.dots', kind = 'toggle', default = true, gap = true, label = 'Draw Rings / 绘制边界',
        description = 'Draw the ranges turned on above. Off leaves the cross and text. / 是否绘制范围，关掉后将不绘制范围，十字和文字可以单独留下。'},
    {key = 'mark', id = 'engle.eagle_hud.mark', kind = 'choice', default = 1, label = 'Marker Style / 边界样式',
        choices = {'Square / 方点', 'Dash / 短划线', 'Tick / 刻度', 'Cross / 十字'},
        description = 'Drawing style of the range boundary：Squares are solid blocks. Dashes follow the ring. Ticks point at the center. Crosses sit on each sample. / 范围边界的绘制样式，方点是实心小方块。短划顺着边界。刻度指向圈心。十字画在每个采样点上。'},
    {key = 'samples', id = 'engle.eagle_hud.samples', kind = 'slider', default = 64, min = 8, max = 64, step = 8,
        label = 'Point Count / 边界绘制',
        description = 'Points on one ring. Fewer means sparser. / 每个边界上绘制的点数量，值越少点越稀疏'},
    {key = 'squad', id = 'engle.eagle_hud.squad', kind = 'toggle', default = true, label = 'teammate range (experimental) / 是否显示队友信标范围（实验性）',
        description = 'Off hides a call whose comms owner is another avatar. A call with no comms owner still stops at 80 m. / 关掉后，队友丢出的飞鹰信标将不再绘制。如果无法确认是否为队友信标，则回退为离当前视角超过80米的不画。'},
    {key = 'range', id = 'engle.eagle_hud.range', kind = 'slider', default = 120, min = 40, max = 500, step = 10,
        label = 'Max Range / 最远绘制距离',
        description = 'Meters from the current view. Farther calls are not drawn. Default 120. With squad ranges off, only an unowned call still stops at 80 m. / 从当前视角算起，超过这个米数将不再绘制预测范围，默认 120。关闭显示队友绘制范围后，无法确认是否为队友的呼叫标记仍限制在 80 米以内。'},
    {key = 'cross', id = 'engle.eagle_hud.cross', kind = 'toggle', default = true, label = 'Call Cross / 呼叫十字',
        description = 'Yellow cross on the call point. / 呼叫点上的黄色十字。'},
    {key = 'labels', id = 'engle.eagle_hud.labels', kind = 'toggle', default = true, label = 'Type and Range / 型号与距离注释',
        description = 'Whether to display annotations regarding the range size and lethality indicators. / 是否显示范围大小和致死提示的相关注释。'},
    {key = 'aim', id = 'engle.eagle_hud.aim', kind = 'toggle', default = true, gap = true, label = 'Aim Landing / 瞄准落点预判',
        description = 'Hold right mouse with the stratagem ball to show the red damage edge. Releasing it hides the preview. / 拿着战略配备球并按住右键时，只显示红色伤害边界。松开右键后隐藏。'},
}
local options_pending = true
local function apply_profile()
    local profile = rawget(_G, 'EngleEagleHudProfile')
    if type(profile) ~= 'table' then return end
    changed_reason('profile', 'manager')
    for _, key in ipairs({'inner', 'outer', 'shock', 'dots', 'cross', 'labels', 'squad', 'aim'}) do
        if profile[key] == false then display[key] = false end
    end
    local mark = profile.mark
    if type(mark) == 'number' and mark >= 1 and mark <= 4 and mark == math.floor(mark) then
        display.mark = mark
    end
    local samples = profile.samples
    if type(samples) == 'number' and samples == samples and samples > -math.huge and samples < math.huge then
        display.samples = samples
    end
    local range_m = profile.range
    if type(range_m) == 'number' and range_m == range_m and range_m > -math.huge and range_m < math.huge then
        display.range = range_m
    end
end
local function sync_options()
    apply_profile()
    local menu = rawget(_G, 'ModOptionsMenu')
    if type(menu) ~= 'table' or menu.api ~= 1 or type(menu.register_option) ~= 'function'
        or type(menu.get) ~= 'function' then
        return
    end
    if options_pending then
        local refused
        for index = 1, #option_rows do
            local row = option_rows[index]
            local spec = {type = row.kind, label = row.label, mod = 'Eagle HUD Reference / 飞鹰参考', mod_id = 'engle.eagle_hud',
                default = display[row.key], description = row.description, gap = row.gap}
            if row.kind == 'choice' then spec.choices = row.choices end
            if row.kind == 'slider' then spec.min, spec.max, spec.step = row.min, row.max, row.step end
            local ok, why = menu.register_option(row.id, spec)
            if not ok then refused = why; break end
        end
        options_pending = false
        changed_reason('options', refused and ('refused:' .. tostring(refused)) or 'registered')
    end
    for index = 1, #option_rows do
        local row = option_rows[index]
        local value = menu.get(row.id)
        local usable = row.kind == 'toggle' and (value == true or value == false)
            or (type(value) == 'number' and value == value and value > -math.huge and value < math.huge)
        if usable then display[row.key] = value end
    end
end
local draw_options = setmetatable({}, {__index = display})
local function draw_references()
    if not current_models or #current_models == 0 or not current_fonts then return end
    if not renderer then
        if renderer_unavailable then return end
        local ok, created, why = pcall(create_renderer, rawget(_G, 'stingray'), log)
        if not ok or not created then
            renderer_unavailable = tostring(ok and why or created)
            changed_reason('hud', 'renderer_unavailable:' .. renderer_unavailable)
            return
        end
        renderer = created
    end
    local sr = rawget(_G, 'stingray')
    if aim and current_world and type(aim.begin) == 'function' then pcall(aim.begin, sr, current_world) end
    if aim and type(aim.height) == 'function' then
        draw_options.surface = function(x, y, z)
            local sampled, hit = pcall(aim.height, x, y, z)
            if sampled and type(hit) == 'number' and hit == hit and hit > -100000 and hit < 100000 then
                return hit
            end
            return z
        end
    else
        draw_options.surface = nil
    end
    local ok, drawn, result = pcall(renderer.draw, current_models, current_fonts, current_world, draw_options)
    if not ok or not drawn then
        local why = tostring(ok and result or drawn)
        clear_references()
        changed_reason('hud', 'unavailable:' .. why)
    else
        changed_reason('hud', 'visible_references:' .. tostring(result))
    end
end
local function sample(now)
    state.sequence = state.sequence + 1
    local ready = ensure_modules(now)
    local current, reason
    if ready then current, reason = sample_game_state(runtime, game_base) end
    changed_reason('game_state', current and 'available' or ('unavailable:' .. tostring(reason or 'native_not_ready')))
    local mission = current and current.mission == true or false
    local mission_key = mission and current.identity or nil
    if state.mission ~= mission or state.mission_key ~= mission_key then
        reset('mission_transition_or_unavailable')
        if mission then state.epoch = state.epoch + 1 end
        state.mission, state.mission_key, state.local_key = mission, mission_key, nil
    end
    local player
    if mission then
        player, reason = sample_local_player(runtime, game_base)
        changed_reason('local_player', player and 'available' or ('unavailable:' .. tostring(reason)))
    end
    local local_key = player and player.identity or nil
    if state.local_key ~= local_key then
        reset('local_player_lifecycle_or_unavailable')
        state.local_key = local_key
    end
    local sampling = mission and player and player.spawned or false
    display.avatar = nil
    if sampling then
        local world, why = main_world()
        local rows
        if world then rows, why = sample_active(runtime, game_base) end
        local models
        if rows then
            local calls = sample_comms(runtime, game_base)
            if calls then attach_throwers(rows, calls) end
            display.avatar = sample_avatar(runtime, game_base, player.avatar_network_id)
            models, why = references.collect(rows)
            if models then
                local best, rank
                for index = 1, #models do
                    local model = models[index]
                    local known = model.definition
                    if known and known.draw_rings and model.aim ~= true then
                        local mine = display.avatar and model.thrower == display.avatar
                        local next_rank = mine and 0 or 1
                        if not rank or next_rank < rank or next_rank == rank then
                            best, rank = known, next_rank
                        end
                    end
                end
                if best then armed = best end
            end
            if models and world and #models < 16 then
                if display.aim == false then
                    changed_reason('aim', 'off')
                elseif aim then
                    local ok, preview, aim_why = pcall(aim.predict, rawget(_G, 'stingray'), world)
                    if not ok then
                        changed_reason('aim', 'unavailable:' .. tostring(preview))
                    elseif preview then
                        local definition = armed
                        local origin = preview.origin
                        if origin then
                            local best, best_score
                            for index = 1, #models do
                                local model = models[index]
                                if model.definition then
                                    local dx = model.x - origin[1]
                                    local dy = model.y - origin[2]
                                    local dz = model.z - origin[3]
                                    local distance = math.sqrt(dx * dx + dy * dy + dz * dz)
                                    local mine = display.avatar and model.thrower == display.avatar
                                    if mine or distance <= 12 then
                                        local score = distance + (mine and 0 or 50)
                                        if not best_score or score < best_score then
                                            best, best_score = model.definition, score
                                        end
                                    end
                                end
                            end
                            if best then definition, armed = best, best end
                        end
                        local landed = false
                        for index = 1, #models do
                            local dx, dy = models[index].x - preview.x, models[index].y - preview.y
                            if dx * dx + dy * dy <= 36 then landed = true; break end
                        end
                        if landed then
                            changed_reason('aim', 'handoff')
                        else
                            preview.edge_only = true
                            if definition and definition.draw_rings then
                                preview.definition = definition
                                local run = definition.runMeters
                                if preview.heading and type(run) == 'number' and run > 0 and run <= 168 then
                                    preview.axis = preview.heading
                                    preview.runMeters = run
                                    if definition.pattern == 'across' then preview.centered = true end
                                end
                                changed_reason('aim', 'edge:' .. tostring(definition.name))
                            else
                                changed_reason('aim', 'cross:no_type')
                            end
                            models[#models + 1] = preview
                        end
                    elseif aim_why == 'holstered' then
                        changed_reason('aim', 'holstered')
                    else
                        changed_reason('aim', tostring(aim_why or 'idle'))
                    end
                end
            end
        end
        if not models then
            clear_references()
            changed_reason('references', 'unavailable:' .. tostring(why))
        elseif #models == 0 then
            clear_references()
            changed_reason('references', 'no_current_eagle_records')
        else
            local fonts = sample_fonts(runtime, game_base)
            if main_world() ~= world then
                clear_references()
                changed_reason('references', 'world_changed_during_snapshot')
            else
                current_models, current_fonts, current_world = models, fonts, world
                changed_reason('font', fonts.source == 'live' and 'live' or ('fallback:' .. tostring(fonts.detail)))
                changed_reason('references', 'current_records:' .. #models)
            end
        end
    else
        clear_references()
    end
end
local function safe_sample(now)
    local ok, why = pcall(sample, now)
    if not ok then
        clear_references()
        changed_reason('sample', 'unavailable:' .. tostring(why))
    end
end
local ok, why = pcall(function()
    local loader = rawget(_G, 'CowboyBingusModLoader')
    if loader and type(loader.open_log) == 'function' then
        local opened, file = pcall(loader.open_log, 'EagleHudProbe.log')
        if opened then log_file = file end
    end
    if not log_file then fallback('LOG_UNAVAILABLE: EagleHudProbe.log could not be opened'); log_failed = true end
    assert(loader and loader.api == 1 and loader.version == 17, 'Loader v18/API1 required')
    ffi = require('ffi')
    assert(type(create_readonly_runtime) == 'function', 'embedded readonly factory missing; use tools/build_addon.py')
    runtime = create_readonly_runtime()
    for _, key in ipairs({'module', 'address', 'module_hash', 'read', 'read_into', 'monotonic_time'}) do
        assert(type(runtime[key]) == 'function', 'readonly interface missing: ' .. key)
    end
    assert(type(create_renderer) == 'function' and type(create_references) == 'function',
        'embedded HUD modules missing; use tools/build_addon.py')
    references = create_references(catalog)
    assert(type(references) == 'table' and type(references.collect) == 'function', 'reference catalog unavailable')
    if type(create_aim_preview) == 'function' then
        local created_ok, created = pcall(create_aim_preview)
        if created_ok and type(created) == 'table' and type(created.predict) == 'function' then
            aim = created
        end
    end
    local previous_update = rawget(_G, 'update')
    assert(type(previous_update) == 'function', 'game update callback unavailable')
    local next_sample = 0
    state.enabled = true
    local function tick_frame(dt)
        sync_options()
        state.frame = state.frame + 1
        if type(dt) == 'number' and dt == dt and dt >= 0 and dt < 10 then
            state.game_time = state.game_time + dt
        end
        local now = runtime.monotonic_time()
        if now >= next_sample then
            next_sample = now + 0.1
            safe_sample(now)
        end
        draw_references()
    end
    -- Preserve the preceding callback, errors, and every return value without a per-frame table.
    local function after_update(dt, ...)
        if state.enabled then
            local ok, why = pcall(tick_frame, dt)
            if not ok then
                clear_references()
                changed_reason('frame', 'unavailable:' .. tostring(why))
            end
        end
        return ...
    end
    local callback = function(dt, ...)
        return after_update(dt, previous_update(dt, ...))
    end
    rawset(_G, 'update', callback)
    local previous_shutdown = rawget(_G, 'shutdown')
    if type(previous_shutdown) == 'function' then
        rawset(_G, 'shutdown', function(...)
            state.enabled = false
            pcall(clear_references)
            if rawget(_G, 'update') == callback then rawset(_G, 'update', previous_update) end
            if log_file then pcall(log_file.close, log_file); log_file = nil end
            return previous_shutdown(...)
        end)
    end
    state.status = 'waiting_for_modules'
    log('initialized', {edition = 'spatial_reference', reader = 'engle.windows_readonly',
        catalog_source = catalog.source_snapshot})
end)
if not ok then stop_native(tostring(why)) end
return state
