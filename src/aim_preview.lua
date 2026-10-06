-- While the stratagem ball is in hand, integrate its arc and keep the first
-- scanned ground hit. No hit means no marker; the arc is not dropped onto a plane.
local BEACON = '16f397ca5f51f271'
local BODY = 'content/fac_helldivers/cha_avatar/avatar_helldiver'
local HANDS = {'attach_hand_r', 'attach_hand_l'}

local function finite(value)
    return type(value) == 'number' and value == value and value > -math.huge and value < math.huge
end

local function copy3(sr, value)
    local x, y, z = sr.Vector3.x(value), sr.Vector3.y(value), sr.Vector3.z(value)
    if not finite(x) or not finite(y) or not finite(z) then return nil end
    return {x, y, z}
end

local function distance(a, b)
    local dx, dy, dz = a[1] - b[1], a[2] - b[2], a[3] - b[3]
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function each_unit(units)
    if type(units) ~= 'table' then return function() end end
    return pairs(units)
end

local function closest_body(sr, world, camera)
    local bodies = sr.World.units_by_resource(world, BODY)
    local best, best_distance
    for _, unit in each_unit(bodies) do
        if sr.Unit.alive(unit) then
            local root = copy3(sr, sr.Unit.world_position(unit, 1))
            local gap = root and distance(root, camera)
            if gap and gap <= 4 and (not best_distance or gap < best_distance) then
                best, best_distance = unit, gap
            end
        end
    end
    return best
end

local function beacon_in_hand(sr, world, body)
    local token = sr.IdString64.from_hex(BEACON)
    local balls = sr.World.units_by_resource(world, token)
    local hands = {}
    for _, name in ipairs(HANDS) do
        if sr.Unit.has_node(body, name) then
            local at = copy3(sr, sr.Unit.world_position(body, sr.Unit.node(body, name)))
            if at then hands[#hands + 1] = at end
        end
    end
    if #hands == 0 then
        local root = copy3(sr, sr.Unit.world_position(body, 1))
        if root then hands[1] = root end
    end
    local held, held_gap
    for _, unit in each_unit(balls) do
        if sr.Unit.alive(unit) then
            local at = copy3(sr, sr.Unit.world_position(unit, 1))
            if at then
                for _, hand in ipairs(hands) do
                    local gap = distance(at, hand)
                    if gap <= 2 and (not held_gap or gap < held_gap) then
                        held, held_gap = unit, gap
                    end
                end
            end
        end
    end
    return held
end

local user32
local function right_held()
    local ok, down = pcall(function()
        if not user32 then
            local ffi = require('ffi')
            ffi.cdef[[short __stdcall GetAsyncKeyState(int);]]
            user32 = ffi.load('user32')
        end
        return tonumber(user32.GetAsyncKeyState(2)) or 0
    end)
    return ok and down < 0
end

local function create_aim(flight, ray, keys)
    assert(type(flight) == 'table' and type(flight.integrate) == 'function', 'beacon flight required')
    assert(type(ray) == 'table' and type(ray.trim) == 'function', 'ground ray required')

    local function predict(sr, world)
        if type(sr) ~= 'table' or world == nil then return nil, 'world_unavailable' end
        local pose = sr.World.debug_camera_pose(world)
        local camera = copy3(sr, sr.Matrix4x4.translation(pose))
        local forward = copy3(sr, sr.Matrix4x4.forward(pose))
        local right = sr.Matrix4x4.right and copy3(sr, sr.Matrix4x4.right(pose))
        local up = sr.Matrix4x4.up and copy3(sr, sr.Matrix4x4.up(pose))
        if not camera or not forward or not right or not up then return nil, 'basis_unavailable' end
        local body = closest_body(sr, world, camera)
        if not body then return nil, 'body_unavailable' end
        if not beacon_in_hand(sr, world, body) then return nil, 'holstered' end
        if keys and type(keys.right) == 'function' then
            if not keys.right() then return nil, 'not_aiming' end
        elseif not right_held() then return nil, 'not_aiming' end
        if not sr.Unit.has_node(body, 'r_shoulder') then return nil, 'shoulder_unavailable' end
        local shoulder = copy3(sr, sr.Unit.world_position(body, sr.Unit.node(body, 'r_shoulder')))
        if not shoulder then return nil, 'shoulder_unavailable' end
        local points, why = flight.integrate(shoulder, forward, right, up)
        if not points then return nil, why end
        local hit, path = ray.trim(sr, world, body, points)
        if not hit then return nil, path or 'clear' end
        local flat_x, flat_y = forward[1], forward[2]
        local flat = math.sqrt(flat_x * flat_x + flat_y * flat_y)
        local model = {
            x = hit[1], y = hit[2], z = hit[3], aim = true,
            origin = shoulder,
        }
        if flat > 1e-4 then
            model.heading = {x = flat_x / flat, y = flat_y / flat}
        end
        return model, nil
    end

    return {predict = predict, begin = ray.begin, height = ray.height}
end

return create_aim
