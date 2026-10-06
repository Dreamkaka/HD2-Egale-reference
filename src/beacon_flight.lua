-- Stratagem-ball arc. Speed, damping, release pose and pitch are the measured
-- beacon family: 22.4 m/s, 0.15/s linear damping, no quadratic drag, gravity 9.8.
-- A scanned ground ray clips the arc; this function does not invent a landing.
local SPEED = 22.4
local GRAVITY = 9.8
local DAMPING = 0.15
local PITCH = math.rad(4.75)
local RELEASE_RIGHT, RELEASE_FORWARD, RELEASE_UP = 0.24, 0.762, 0.675
local STEP = 1 / 60
local HORIZONTAL_LIMIT = 120
local DROP_LIMIT = 80
local TIME_LIMIT = 5

local function finite(value)
    return type(value) == 'number' and value == value and value > -math.huge and value < math.huge
end

local function axis(value, name)
    if type(value) ~= 'table' or not finite(value[1]) or not finite(value[2]) or not finite(value[3]) then
        return nil, name
    end
    return value
end

local function integrate(origin, forward, right, up)
    local o, why = axis(origin, 'origin')
    if not o then return nil, why end
    local f; f, why = axis(forward, 'forward')
    if not f then return nil, why end
    local r; r, why = axis(right, 'right')
    if not r then return nil, why end
    local u; u, why = axis(up, 'up')
    if not u then return nil, why end
    local ox = o[1] + r[1] * RELEASE_RIGHT + f[1] * RELEASE_FORWARD + u[1] * RELEASE_UP
    local oy = o[2] + r[2] * RELEASE_RIGHT + f[2] * RELEASE_FORWARD + u[2] * RELEASE_UP
    local oz = o[3] + r[3] * RELEASE_RIGHT + f[3] * RELEASE_FORWARD + u[3] * RELEASE_UP
    local c, s = math.cos(PITCH), math.sin(PITCH)
    local dx, dy, dz = f[1] * c + u[1] * s, f[2] * c + u[2] * s, f[3] * c + u[3] * s
    local length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not (length > 1e-6) then return nil, 'forward' end
    local vx, vy, vz = dx / length * SPEED, dy / length * SPEED, dz / length * SPEED
    local points = {{ox, oy, oz}}
    local x, y, z, time = ox, oy, oz, 0
    for _ = 1, 400 do
        local mx = vx * (1 - DAMPING * STEP / 2)
        local my = vy * (1 - DAMPING * STEP / 2)
        local mz = vz * (1 - DAMPING * STEP / 2) - GRAVITY * STEP / 2
        local nx, ny, nz = x + mx * STEP, y + my * STEP, z + mz * STEP
        local fraction, stop = 1, false
        if nz < o[3] - DROP_LIMIT and nz ~= z then
            fraction, stop = (o[3] - DROP_LIMIT - z) / (nz - z), true
        end
        local hx, hy = x - ox, y - oy
        local hnx, hny = nx - ox, ny - oy
        if hx * hx + hy * hy < HORIZONTAL_LIMIT * HORIZONTAL_LIMIT
            and hnx * hnx + hny * hny >= HORIZONTAL_LIMIT * HORIZONTAL_LIMIT then
            local sx, sy = nx - x, ny - y
            local dot = hx * sx + hy * sy
            local square = sx * sx + sy * sy
            if square > 1e-12 then
                local limit = (-dot + math.sqrt(math.max(0, dot * dot
                    + square * (HORIZONTAL_LIMIT * HORIZONTAL_LIMIT - hx * hx - hy * hy)))) / square
                if limit >= 0 and limit < fraction then fraction, stop = limit, true end
            end
        end
        if time + STEP * fraction >= TIME_LIMIT then
            fraction, stop = math.max(0, (TIME_LIMIT - time) / STEP), true
        end
        if fraction < 0 then fraction = 0 end
        if fraction > 1 then fraction = 1 end
        vx = vx - DAMPING * mx * STEP * fraction
        vy = vy - DAMPING * my * STEP * fraction
        vz = vz - (GRAVITY + DAMPING * mz) * STEP * fraction
        x = x + fraction * (nx - x)
        y = y + fraction * (ny - y)
        z = z + fraction * (nz - z)
        time = time + fraction * STEP
        if not finite(x) or not finite(y) or not finite(z) then return nil, 'nonfinite' end
        points[#points + 1] = {x, y, z}
        if stop or #points >= 240 then break end
    end
    return points
end

-- hit_test(ax, ay, az, bx, by, bz) returns a fraction in [0, 1], or nil for a miss.
local function clip(points, hit_test)
    if type(points) ~= 'table' or #points < 2 or type(hit_test) ~= 'function' then
        return nil, 'arc_unavailable'
    end
    for index = 1, #points - 1 do
        local a, b = points[index], points[index + 1]
        if type(a) ~= 'table' or type(b) ~= 'table' then return nil, 'arc_unavailable' end
        local fraction = hit_test(a[1], a[2], a[3], b[1], b[2], b[3])
        if type(fraction) == 'number' and fraction >= 0 and fraction <= 1 then
            local hit = {
                a[1] + (b[1] - a[1]) * fraction,
                a[2] + (b[2] - a[2]) * fraction,
                a[3] + (b[3] - a[3]) * fraction,
            }
            local path = {}
            for cursor = 1, index do path[cursor] = points[cursor] end
            path[#path + 1] = hit
            return hit, path
        end
    end
    return nil, 'clear'
end

return {integrate = integrate, clip = clip}
