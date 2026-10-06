-- One ground ray, discovered the same way as the trajectory overlay:
-- match the query body, then check the laser preset before calling it.
-- Discovery never calls the scanned function. A failed check returns nil.
local function create_ground_ray(recipes)
    assert(type(recipes) == 'table' and #recipes == 4, 'ray recipes required')
    local layout, failure, attempted

    local function u32(data, at)
        local a, b, c, d = data:byte(at + 1, at + 4)
        return a + b * 256 + c * 65536 + d * 16777216
    end

    local function read_api()
        local ffi = require('ffi')
        ffi.cdef[[
            void * __stdcall GetModuleHandleA(const char *);
            void * __stdcall GetCurrentProcess(void);
            int __stdcall ReadProcessMemory(void *, const void *, void *, size_t, size_t *);
        ]]
        local process = ffi.C.GetCurrentProcess()
        local buffer, copied = ffi.new('uint8_t[65536]'), ffi.new('size_t[1]')
        return {
            module = function(name)
                return tonumber(ffi.cast('uintptr_t', ffi.C.GetModuleHandleA(name)))
            end,
            address = function(value)
                assert(type(value) == 'userdata', 'unsupported userdata')
                return tonumber(ffi.cast('uintptr_t', value))
            end,
            read = function(at, size)
                assert(type(at) == 'number' and at >= 65536 and at % 1 == 0
                    and size > 0 and size <= 65536 and at + size < 2 ^ 47, 'invalid read range')
                copied[0] = 0
                if ffi.C.ReadProcessMemory(process, ffi.cast('const void *', at), buffer, size, copied) ~= 0
                    and tonumber(copied[0]) == size then
                    return ffi.string(buffer, size)
                end
            end,
            ffi = ffi,
        }
    end

    local function discover(api)
        local function read(at, size)
            local data = api.read(at, size)
            assert(type(data) == 'string' and #data == size, 'contract memory unavailable')
            return data
        end
        local function module(base)
            local dos = read(base, 64)
            assert(dos:sub(1, 2) == 'MZ', 'invalid module header')
            local pe_at = u32(dos, 0x3c)
            assert(pe_at >= 64 and pe_at < 1048576, 'invalid PE offset')
            local pe = read(base + pe_at, 0x58)
            local count = pe:byte(7) + pe:byte(8) * 256
            local optional = pe:byte(21) + pe:byte(22) * 256
            local size = u32(pe, 0x50)
            assert(pe:sub(1, 4) == 'PE\0\0' and pe:byte(5) == 0x64 and pe:byte(6) == 0x86
                and pe:byte(25) == 0x0b and pe:byte(26) == 2 and count > 0 and count <= 96
                and optional >= 0xf0 and optional <= 1024 and size > 4096 and size <= 536870912
                and base + size < 2 ^ 47, 'invalid PE sections')
            local sections, table_bytes = {}, read(base + pe_at + 24 + optional, count * 40)
            for index = 0, count - 1 do
                local rva = u32(table_bytes, index * 40 + 12)
                local length = u32(table_bytes, index * 40 + 8)
                local flags = u32(table_bytes, index * 40 + 36)
                assert(rva >= 4096 and length > 0 and rva + length <= size, 'invalid section range')
                sections[#sections + 1] = {
                    first = base + rva, last = base + rva + length,
                    code = math.floor(flags / 0x20000000) % 2 == 1,
                    writable = math.floor(flags / 0x80000000) % 2 == 1,
                }
            end
            return {base = base, size = size, stamp = u32(pe, 8), sections = sections}
        end
        local function contains(mod, at, size, kind)
            for _, section in ipairs(mod.sections) do
                if at >= section.first and at + size <= section.last then
                    if kind == 'code' then return section.code end
                    return not section.code and (kind ~= 'global' or section.writable)
                end
            end
            return false
        end
        local function matches(recipe, bytes)
            for _, piece in ipairs(recipe.pieces) do
                if bytes:sub(piece[1] + 1, piece[1] + #piece[2]) ~= piece[2] then return false end
            end
            return true
        end
        local function extract(recipe, bytes, at, mod)
            local fields = {address = at}
            for _, field in ipairs(recipe.fields) do
                local value = u32(bytes, field[2])
                if value >= 2147483648 then value = value - 4294967296 end
                value = at + field[3] + value
                assert(contains(mod, value, field[4] == 'global' and 8 or 1, field[4]),
                    recipe.name .. ' target outside ' .. field[4])
                fields[field[1]] = value
            end
            return fields
        end
        local exe = module(api.module('helldivers2.exe'))
        local game = module(api.module('game.dll'))
        local modules = {['helldivers2.exe'] = exe, ['game.dll'] = game}
        local found = {}
        for _, recipe in ipairs(recipes) do
            local mod = modules[recipe.module]
            local hit
            for _, hint in ipairs(recipe.hints or {}) do
                if mod.stamp == hint[1] and mod.size == hint[2]
                    and contains(mod, mod.base + hint[3], recipe.size, 'code') then
                    local bytes = read(mod.base + hint[3], recipe.size)
                    if matches(recipe, bytes) then hit = {address = mod.base + hint[3], bytes = bytes} end
                    break
                end
            end
            if not hit then
                local overlap = recipe.size - 1
                for _, section in ipairs(mod.sections) do
                    if section.code then
                        local tail = ''
                        for cursor = section.first, section.last - 1, 65536 do
                            local bytes = tail .. read(cursor, math.min(65536, section.last - cursor))
                            local start = cursor - #tail
                            local pos = 1
                            while true do
                                pos = bytes:find(recipe.anchor, pos, true)
                                if not pos then break end
                                local at = pos - recipe.anchor_offset
                                if at >= 1 and at + recipe.size - 1 <= #bytes
                                    and start + at + recipe.size - 1 > cursor
                                    and matches(recipe, bytes:sub(at, at + recipe.size - 1)) then
                                    assert(hit == nil, 'ambiguous ' .. recipe.name)
                                    hit = {address = start + at - 1, bytes = bytes:sub(at, at + recipe.size - 1)}
                                end
                                pos = pos + 1
                            end
                            tail = bytes:sub(math.max(1, #bytes - overlap + 1))
                        end
                    end
                end
            end
            assert(hit, 'missing ' .. recipe.name)
            found[recipe.name] = extract(recipe, hit.bytes, hit.address, mod)
        end
        return {
            game = game.base, exe = exe.base,
            owner = found.query_owner.r1b - game.base,
            api = found.collision_0.r6 - game.base,
            presets = found.ray_query.r28 - exe.base,
            worlds = found.ray_world_lookup.r2 - exe.base,
            query = found.ray_query.address - exe.base,
        }
    end
    local native_api
    local function resolve()
        if attempted then return layout, failure end
        if not native_api then native_api = read_api() end
        local api = native_api
        attempted = true
        local exe, game = api.module('helldivers2.exe'), api.module('game.dll')
        if not exe or not game or exe < 65536 or game < 65536 then
            failure = 'native modules unavailable'
            return nil, failure
        end
        local ok, result = pcall(discover, api)
        if ok then layout = result else failure = tostring(result) end
        return layout, failure
    end

    local caster, caster_world, cache, bound_world, budget
    local session_sr, session_world, session_unit

    local function holder(sr, world)
        local units = sr.World.units_by_resource(world, 'core/units/camera')
        if type(units) ~= 'table' then return nil end
        for _, unit in pairs(units) do
            if sr.Unit.alive(unit) and (not sr.Unit.world or sr.Unit.world(unit) == world) then
                return unit
            end
        end
    end

    local function prepare(sr, world, unit)
        local found, why = resolve()
        if not found then return nil, why or 'ray_unavailable' end
        if caster and caster_world == world then return caster end
        local api = native_api
        local ffi = api.ffi
        local function read(at, size)
            local data = api.read(at, size)
            assert(type(data) == 'string' and #data == size, 'ray memory unavailable')
            return data
        end
        local function ptr(at)
            local data = read(at, 8)
            local value = u32(data, 0) + u32(data, 4) * 4294967296
            assert(value >= 65536 and value < 2 ^ 47, 'ray pointer unavailable')
            return value
        end
        local ok, cast = pcall(function()
            assert(sr.Unit.alive(unit) and (not sr.Unit.world or sr.Unit.world(unit) == world),
                'query unit unavailable')
            local world_ptr = ptr(api.address(world))
            local tagged = api.address(unit)
            assert(tagged >= 1 and tagged < 2 ^ 32 and tagged % 4 == 1, 'invalid query unit')
            local query_set = ptr(ptr(found.game + found.owner) + 8)
            assert(ptr(query_set) == world_ptr, 'query world mismatch')
            local handle = u32(read(query_set + 0xA0, 4), 0)
            assert(handle >= 0x80000000 and handle < 0x80000100
                and u32(read(query_set + 0x1C, 4), 0) == handle, 'invalid query handle')
            local preset = read(found.exe + found.presets + (handle - 0x80000000) * 20, 20)
            local world_index = u32(preset, 0)
            assert(world_index < 4 and u32(preset, 4) == 2 and u32(preset, 8) == 5
                and u32(preset, 12) == 0x3E24EC53 and u32(preset, 16) == 0x80000009,
                'query preset mismatch')
            assert(ptr(found.exe + found.worlds + world_index * 8) == world_ptr, 'physics world mismatch')
            local address = ptr(ptr(found.game + found.api) + 0x68)
            assert(address == found.exe + found.query, 'query function mismatch')
            local origin = ffi.new('float[3]')
            local direction = ffi.new('float[3]')
            local hits = ffi.new('float[11]')
            local native = ffi.cast(
                'uint32_t (*)(uint32_t, const float *, const float *, float, uint32_t, void *, uint32_t)',
                address)
            local ignore = (tagged - 1) / 4
            return function(ax, ay, az, dx, dy, dz, length)
                origin[0], origin[1], origin[2] = ax, ay, az
                direction[0], direction[1], direction[2] = dx, dy, dz
                ffi.fill(hits, 44)
                local count = tonumber(native(handle, origin, direction, length, ignore, hits, 1))
                assert(count and count >= 0 and count % 1 == 0, 'invalid hit count')
                if count < 1 then return nil end
                local fraction = tonumber(hits[6]) / length
                assert(fraction >= -1e-4 and fraction <= 1 + 1e-4, 'hit outside segment')
                fraction = math.max(0, math.min(1, fraction))
                return ax + dx * length * fraction, ay + dy * length * fraction, az + dz * length * fraction
            end
        end)
        if not ok then
            caster, caster_world = nil, nil
            return nil, tostring(cast)
        end
        caster, caster_world = cast, world
        return caster
    end

    local function trim(sr, world, unit, points)
        local cast, why = prepare(sr, world, unit)
        if not cast then return nil, why or 'ray_unavailable' end
        local hit, path
        for index = 1, #points - 1 do
            local a, b = points[index], points[index + 1]
            local dx, dy, dz = b[1] - a[1], b[2] - a[2], b[3] - a[3]
            local length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > 1e-6 then
                local ok, hx, hy, hz = pcall(cast, a[1], a[2], a[3], dx / length, dy / length, dz / length, length)
                if not ok then return nil, tostring(hx) end
                if hx then
                    hit = {hx, hy, hz}
                    path = {}
                    for cursor = 1, index do path[cursor] = points[cursor] end
                    path[#path + 1] = hit
                    return hit, path
                end
            end
        end
        return nil, 'clear'
    end

    local function begin(sr, world)
        budget = 48
        session_sr, session_world = sr, world
        if world ~= bound_world then
            cache, bound_world, caster, caster_world = {}, world, nil, nil
        end
        session_unit = holder(sr, world)
    end

    -- Downward sample. Fifty metres above the call, a hundred and twenty down,
    -- so a slope can rise or fall without leaving the flat call height.
    local function height(x, y, z)
        if not session_sr or not session_world or not session_unit then return nil end
        if type(x) ~= 'number' or type(y) ~= 'number' or type(z) ~= 'number' then return nil end
        local key = math.floor(x * 2) .. ':' .. math.floor(y * 2)
        local known = cache[key]
        if known == false then return nil end
        if type(known) == 'number' then return known end
        if not budget or budget <= 0 then return nil end
        budget = budget - 1
        local cast = prepare(session_sr, session_world, session_unit)
        if not cast then
            cache[key] = false
            return nil
        end
        local ok, hx, hy, hz = pcall(cast, x, y, z + 50, 0, 0, -1, 120)
        if not ok or not hz or hz ~= hz or hz <= -1e5 or hz >= 1e5 then
            cache[key] = false
            return nil
        end
        cache[key] = hz
        return hz
    end

    return {trim = trim, begin = begin, height = height}
end

return create_ground_ray
