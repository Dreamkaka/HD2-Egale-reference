-- Live Eagle jets for this pinned game.dll only.
-- Layout: HD2Runtime domains/custom_payloads.lua and domains/event_natives.lua,
-- both stamped game.dll 2E2C3B7C2500646DADD5F2B4C6E0504DBB7E7896139F64CDDC0D1813C718F51E.
-- The manager lists jets. A jet's entity descriptor names its unit. The unit's
-- scene-graph pose is accepted only when the accessor is the reviewed
-- `lea rax, [rcx+0x60]; ret`. A heading may replace one call's anchor axis.
-- It never moves that call's radii onto the aircraft, and it never pairs
-- one jet with more than one call.
local ffi = require('ffi')

local JET_GLOBAL, JET_COUNT, JET_HANDLES, JET_CAPACITY = 53634640, 28, 72, 16
local HANDLE_RESOURCE, HANDLE_ENTITY = 0, 8
local ENTITIES, ENTITY_MAP, DESCRIPTORS = 54968216, 15838896, 15937304
local DESCRIPTOR_STRIDE, DESCRIPTOR_ENTITY, DESCRIPTOR_UNIT = 24, 8, 12
local UNITS, UNIT_COUNT, UNIT_GENERATIONS, UNIT_OBJECTS = 27328752, 152, 160, 136
local UNIT_ID, SCENE_METHOD, SCENE_OFFSET, POSES = 8, 2873456, 96, 40
local SCENE_BYTES = '\072\141\065\096\195'

-- Donor jet resource (lowercase hex) -> catalog native type.
local JET_TYPE = {
    dbb286ad7ed9df96 = 126,
    ['23a60681dd4383ec'] = 30,
    ['2ea01cb1676aca29'] = 18,
    ['9d4f7cb4eb34515d'] = 65,
    ['1b3bcadabc7ef8d6'] = 38,
    ['27bb558c893383cc'] = 133,
    ['397792815583da29'] = 140,
    e44b691dc039a505 = 3,
}

local function create_jets()
    local jets = {}
    local scene_base

    local function finite(value)
        return type(value) == 'number' and value == value and value > -100000 and value < 100000
    end

    local function f32_at(raw, at)
        local value = tonumber(ffi.cast('const float *', ffi.cast('const char *', raw) + at)[0])
        if not finite(value) then return nil end
        return value
    end

    local function mul32(a, c)
        local low, high = c % 65536, math.floor(c / 65536)
        return (a * low + ((a * high) % 65536) * 65536) % 4294967296
    end

    local function power_of_two(value)
        if type(value) ~= 'number' or value < 1 or value > 1048576 or value % 1 ~= 0 then return false end
        local rest = value
        while rest % 2 == 0 do rest = rest / 2 end
        return rest == 1
    end

    local function hash_value(io, header_address, key)
        if type(key) ~= 'number' or key <= 0 or key >= 4294967296 or key % 1 ~= 0 then return nil end
        local buckets = io.pointer(header_address)
        local capacity, empty, multiplier = io.u32(header_address + 8), io.u32(header_address + 12), io.u32(header_address + 16)
        if not buckets or not capacity or not empty or not multiplier or not power_of_two(capacity) then return nil end
        local start = mul32(key, multiplier)
        for probe = 0, math.min(capacity, 4096) - 1 do
            local slot = buckets + ((start + probe) % capacity) * 8
            local found, value = io.u32(slot), io.u32(slot + 4)
            if not found then return nil end
            if found == key then
                if not value or value == 4294967295 or value >= 1048576 then return nil end
                return value
            end
            if found == empty then return nil end
        end
        return nil
    end

    local function entity_unit(io, entity)
        local manager = io.pointer(io.game + ENTITIES)
        if not manager then return nil end
        local index = hash_value(io, manager + ENTITY_MAP, entity)
        if not index then return nil end
        local descriptor = manager + DESCRIPTORS + index * DESCRIPTOR_STRIDE
        if io.u32(descriptor + DESCRIPTOR_ENTITY) ~= entity then return nil end
        local unit = io.u32(descriptor + DESCRIPTOR_UNIT)
        if not unit or unit == 0 then return nil end
        return unit
    end

    local function scene_proven(io)
        if scene_base == io.exe then return true end
        local raw = io.bytes(io.exe + SCENE_METHOD, 5)
        if raw ~= SCENE_BYTES then return false end
        scene_base = io.exe
        return true
    end

    local function unit_pose(io, unit)
        if type(unit) ~= 'number' or unit <= 0 or unit >= 4294967296 or unit % 1 ~= 0 then return nil end
        local index, generation = unit % 4194304, math.floor(unit / 4194304) % 256
        local registry = io.pointer(io.exe + UNITS)
        if not registry then return nil end
        local count = io.u32(registry + UNIT_COUNT)
        local generations = io.pointer(registry + UNIT_GENERATIONS)
        local objects = io.pointer(registry + UNIT_OBJECTS)
        if not count or not generations or not objects or index >= count then return nil end
        local current = io.bytes(generations + index, 1)
        if not current or current:byte() ~= generation then return nil end
        local object = io.pointer(objects + index * 8)
        if not object or io.u32(object + UNIT_ID) ~= unit then return nil end
        local vtable = io.pointer(object)
        local method = vtable and io.pointer(vtable + 0xE8)
        if method ~= io.exe + SCENE_METHOD then return nil end
        local poses = io.pointer(object + SCENE_OFFSET + POSES)
        local raw = poses and io.bytes(poses, 60)
        if not raw then return nil end
        local rows = {}
        for row = 0, 3 do
            local x, y, z = f32_at(raw, row * 16), f32_at(raw, row * 16 + 4), f32_at(raw, row * 16 + 8)
            if not x or not y or not z then return nil end
            rows[row] = {x = x, y = y, z = z}
        end
        local function dot(p, q) return p.x * q.x + p.y * q.y + p.z * q.z end
        for i = 0, 2 do
            if math.abs(dot(rows[i], rows[i]) - 1) > 0.02 then return nil end
            for j = i + 1, 2 do
                if math.abs(dot(rows[i], rows[j])) > 0.02 then return nil end
            end
        end
        local forward = rows[1]
        local flat = math.sqrt(forward.x * forward.x + forward.y * forward.y)
        local pose = {x = rows[3].x, y = rows[3].y, z = rows[3].z}
        if flat >= 1e-4 then pose.fx, pose.fy = forward.x / flat, forward.y / flat end
        return pose
    end

    function jets.sample(io)
        if type(io) ~= 'table' or type(io.game) ~= 'number' or type(io.exe) ~= 'number'
            or type(io.pointer) ~= 'function' then
            return nil, 'bases_unavailable'
        end
        local manager = io.pointer(io.game + JET_GLOBAL)
        if not manager then return nil, 'manager_unreadable' end
        local count = io.u32(manager + JET_COUNT)
        if not count or count > JET_CAPACITY then return nil, 'count_implausible' end
        if count == 0 then return {}, nil end
        if not scene_proven(io) then return nil, 'scene_graph_unproven' end
        local handles = io.pointer(manager + JET_HANDLES)
        if not handles then return nil, 'handles_unreadable' end
        local out = {}
        for index = 0, count - 1 do
            local handle = io.pointer(handles + index * 8)
            local resource = handle and io.u64hex(handle + HANDLE_RESOURCE)
            local entity = handle and io.u32(handle + HANDLE_ENTITY)
            if not resource or not entity or entity == 0 then return nil, 'handle_unreadable' end
            local native = JET_TYPE[resource]
            local pose = native and unit_pose(io, entity_unit(io, entity))
            if pose then
                pose.type = native
                pose.entity = entity
                out[#out + 1] = pose
            end
        end
        return out, nil
    end

    -- Each jet may turn the one call of its type that sits on its nose.
    -- A second call within 8 m of that line, or a second jet claiming the
    -- same call, is left on its own anchor. Radii stay on the call.
    local LATERAL_GAP = 8
    function jets.apply(models, aircraft)
        if type(models) ~= 'table' or type(aircraft) ~= 'table' then return 0 end
        local calls, grouped = {}, {}
        for index = 1, #models do
            local model = models[index]
            local native = type(model) == 'table' and model.definition and model.definition.type
            if type(native) == 'number' then
                local bucket = calls[native]
                if not bucket then bucket = {}; calls[native] = bucket end
                bucket[#bucket + 1] = model
            end
        end
        for index = 1, #aircraft do
            local jet = aircraft[index]
            if type(jet) == 'table' and type(jet.type) == 'number' and finite(jet.fx) and finite(jet.fy)
                and finite(jet.x) and finite(jet.y) then
                local bucket = grouped[jet.type]
                if not bucket then bucket = {}; grouped[jet.type] = bucket end
                bucket[#bucket + 1] = jet
            end
        end
        local proposal = {}
        for native, jets_of_type in pairs(grouped) do
            local group = calls[native]
            if group then
                for jet_index = 1, #jets_of_type do
                    local jet = jets_of_type[jet_index]
                    local ranked = {}
                    for call_index = 1, #group do
                        local model = group[call_index]
                        local run = model.definition and model.definition.runMeters
                        if type(run) == 'number' and run == run and run > 0 and run <= 168 then
                            local dx, dy = model.x - jet.x, model.y - jet.y
                            local distance = math.sqrt(dx * dx + dy * dy)
                            local along = dx * jet.fx + dy * jet.fy
                            if distance >= 0.5 and distance <= 1000 and along > 0 then
                                ranked[#ranked + 1] = {
                                    model = model,
                                    lateral = math.abs(dx * jet.fy - dy * jet.fx),
                                }
                            end
                        end
                    end
                    table.sort(ranked, function(a, b) return a.lateral < b.lateral end)
                    if #ranked >= 1 and (#ranked == 1 or ranked[2].lateral >= ranked[1].lateral + LATERAL_GAP) then
                        local model = ranked[1].model
                        if proposal[model] then proposal[model] = false
                        else proposal[model] = jet end
                    end
                end
            end
        end
        local matched = 0
        for model, jet in pairs(proposal) do
            if jet then
                local dx, dy = model.x - jet.x, model.y - jet.y
                local along = dx * jet.fx + dy * jet.fy
                local lateral = math.abs(dx * jet.fy - dy * jet.fx)
                model.axis = {x = jet.fx, y = jet.fy}
                model.runMeters = model.definition.runMeters
                model.centered = model.definition.pattern == 'across' and true or nil
                model.jet_heading = true
                if lateral > 1 then
                    model.track_x = jet.x + jet.fx * along
                    model.track_y = jet.y + jet.fy * along
                end
                matched = matched + 1
            end
        end
        return matched
    end

    return jets
end

return create_jets
