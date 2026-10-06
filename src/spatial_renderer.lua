-- Stateless call-reference presentation. GUI slots are not sortie identities.
-- Inner is full damage, outer is the damage edge, shockwave is the larger stagger radius.
-- Smoke is the exception: one green circle per bomb. A capsule would fill the gaps.
-- Draw distance defaults to 120 m from the viewpoint. With squad marks off, a call
-- owned by another avatar is hidden at any distance. A call with no comms owner
-- still stops at 80 m. An owned call uses the configured distance.
-- Strafing with a 1-80 m anchor draws those radii along a 50 m centerline past the call point.
-- That length is the wiki's approximate run, not a measured bomb line.
-- When options.surface returns a height, each range sample uses that ground
-- height instead of the flat call altitude. A miss keeps the flat height.
local SAMPLES = 64
local CORRIDOR_SIDE = 24
local CORRIDOR_CAP = 16
local circle = {}
for n = 0, SAMPLES - 1 do
    local angle = n * math.pi * 2 / SAMPLES
    circle[n + 1] = {math.cos(angle), math.sin(angle)}
end
local outline = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}, {0, 0}}
local function finite(n)
    return type(n) == "number" and n == n and n > -math.huge and n < math.huge
end
local function hash(s)
    return type(s) == "string" and #s == 16 and s:match("^%x+$") and s ~= "0000000000000000"
end

return function(sr, log)
    local required = {
        {"Application", "worlds"}, {"Application", "main_world"},
        {"World", "create_screen_gui"}, {"World", "destroy_gui"},
        {"World", "units_by_resource"}, {"World", "debug_camera_pose"},
        {"Unit", "camera"}, {"Camera", "world_to_screen"}, {"Camera", "world_position"},
        {"Matrix4x4", "translation"}, {"Matrix4x4", "forward"},
        {"Vector3"}, {"Vector3", "x"}, {"Vector3", "y"}, {"Vector3", "z"},
        {"Vector2"}, {"Color"}, {"IdString64", "from_hex"},
        {"Gui", "resolution"}, {"Gui", "material"}, {"Gui", "rect"},
        {"Gui", "update_rect"}, {"Gui", "destroy_rect"}, {"Gui", "text"},
        {"Gui", "update_text"}, {"Gui", "destroy_text"},
        {"Material", "set_scalar"}, {"Material", "set_vector2"},
        {"Material", "set_vector4"}, {"Material", "set_texture"},
        {"Script", "temp_byte_count"}, {"Script", "set_temp_byte_count"},
    }
    for _, path in ipairs(required) do
        local value = sr
        for _, key in ipairs(path) do
            if type(value) ~= "table" and type(value) ~= "userdata" then value = nil break end
            value = value[key]
        end
        if type(value) ~= "function" and type(value) ~= "table" and type(value) ~= "userdata" then
            return nil, "spatial_unavailable:" .. table.concat(path, ".")
        end
    end
    local App, World, Gui, Script = sr.Application, sr.World, sr.Gui, sr.Script
    local V3, V2, Color, I = sr.Vector3, sr.Vector2, sr.Color, sr.IdString64
    local gui, world, font_key
    local rects, texts = {}, {}
    local rect_count, text_count = 0, 0
    local layouts, layout_width = {}, nil
    local pool_failed, last_error = false, nil
    local renderer = {}
    local function resource_name(value)
        return type(value) == "string" and #value > 0 and #value <= 96
            and value:match("^[%w_/%-]+$") ~= nil and value:match("^%x+$") == nil
    end
    local function font_token(value)
        if hash(value) then return I.from_hex(value) end
        if resource_name(value) then return value end
        error("fonts_unavailable")
    end
    local function usable_fonts(fonts)
        return type(fonts) == "table" and (hash(fonts.font) or resource_name(fonts.font))
            and (hash(fonts.material) or resource_name(fonts.material))
            and (fonts.atlas == nil or hash(fonts.atlas))
    end

    local function live(w, worlds)
        if type(worlds) ~= "table" then return false end
        for _, candidate in ipairs(worlds) do if candidate == w then return true end end
        return false
    end
    local function dispose(worlds)
        local old_gui, old_world = gui, world
        gui, world, font_key = nil, nil, nil
        rects, texts, layouts = {}, {}, {}
        rect_count, text_count, layout_width = 0, 0, nil
        if not old_gui then return end
        if live(old_world, worlds or App.worlds()) then
            if type(Gui.set_visible) == "function" then pcall(Gui.set_visible, old_gui, false) end
            World.destroy_gui(old_world, old_gui)
        end
    end
    -- All engine work, including cleanup, is inside the same temporary-pool scope.
    local function protected(operation)
        if pool_failed then return nil, "spatial_unavailable:temporary_pool" end
        local saved_ok, saved = pcall(Script.temp_byte_count)
        if not saved_ok or not finite(saved) or saved < 0 then
            pool_failed = true
            pcall(dispose)
            return nil, "spatial_unavailable:temp_byte_count"
        end
        local ok, result = pcall(operation)
        local cleanup_ok, cleanup_error = true, nil
        if not ok then cleanup_ok, cleanup_error = pcall(dispose) end
        local restored, restore_error = pcall(Script.set_temp_byte_count, saved)
        if not restored then
            pool_failed = true
            cleanup_ok, cleanup_error = pcall(dispose)
        end
        if not ok or not restored or not cleanup_ok then
            local reason = "spatial_unavailable:" .. tostring(not ok and result or restore_error)
            if not cleanup_ok then reason = reason .. ";cleanup:" .. tostring(cleanup_error) end
            if log and reason ~= last_error then pcall(log, "spatial_unavailable", {reason = reason}) end
            last_error = reason
            return nil, reason
        end
        last_error = nil
        return true, result
    end
    function renderer.clear()
        if not gui then return true end
        local ok, reason = protected(dispose)
        if not ok then return nil, reason end
        return true
    end
    local function validate(models)
        assert(type(models) == "table", "models_invalid")
        local count = #models
        assert(count <= 16, "render_limit:16")
        for key in pairs(models) do
            assert(type(key) == "number" and key % 1 == 0 and key >= 1 and key <= count, "models_not_array")
        end
        for n = 1, count do
            local model = models[n]
            assert(type(model) == "table" and finite(model.x) and finite(model.y) and finite(model.z), "position_nonfinite")
            local def = model.definition
            if model.aim == true then
                local path = model.path
                assert(type(path) == "table" and #path >= 2 and #path <= 240, "aim_path_invalid")
                for index = 1, #path do
                    local point = path[index]
                    assert(type(point) == "table" and finite(point[1]) and finite(point[2]) and finite(point[3]),
                        "aim_path_invalid")
                end
                if model.heading ~= nil then
                    assert(type(model.heading) == "table" and finite(model.heading.x) and finite(model.heading.y),
                        "aim_heading_invalid")
                end
            end
            if model.aim ~= true or def ~= nil then
            assert(type(def) == "table" and type(def.name) == "string" and type(def.caption) == "string"
                and finite(def.type) and def.type % 1 == 0 and type(def.draw_rings) == "boolean"
                and type(def.phases) == "table", "definition_invalid")
            if def.bursts ~= nil then
                assert(def.bursts % 1 == 0 and def.bursts >= 2 and def.bursts <= 16, "bursts_invalid")
            end
            if def.tint ~= nil then assert(def.tint == "green", "tint_invalid") end
            for key in pairs(def.phases) do
                assert(type(key) == "number" and key % 1 == 0 and key >= 1 and key <= #def.phases, "phases_not_array")
            end
            for index = 1, #def.phases do
                local phase = def.phases[index]
                assert(type(phase) == "table" and type(phase.label) == "string", "phase_invalid")
                for _, key in ipairs({"innerRadius", "outerRadius", "shockwaveRadius"}) do
                    assert(phase[key] == nil or (finite(phase[key]) and phase[key] >= 0), "radius_invalid")
                end
            end
            if model.axis ~= nil or model.runMeters ~= nil then
                local axis = model.axis
                assert(type(axis) == "table" and finite(axis.x) and finite(axis.y), "axis_invalid")
                local span = math.sqrt(axis.x * axis.x + axis.y * axis.y)
                assert(math.abs(span - 1) <= 1e-4, "axis_invalid")
                assert(finite(model.runMeters) and model.runMeters > 0 and model.runMeters <= 168, "run_invalid")
                if model.centered ~= nil then assert(model.centered == true, "centered_invalid") end
            end
            end
        end
    end
    local function rect(x, y, width, height, layer, r, g, b)
        rect_count = rect_count + 1
        local pos, size, tint = V3(x, y, layer), V2(width, height), Color(255, r, g, b)
        local id = rects[rect_count]
        if id then Gui.update_rect(gui, id, pos, size, tint)
        else rects[rect_count] = assert(Gui.rect(gui, pos, size, tint), "rect_unavailable") end
    end
    local function text(value, x, y, size, fonts)
        for n, offset in ipairs(outline) do
            text_count = text_count + 1
            local front = n == #outline
            local pos = V3(x + offset[1], y + offset[2], front and 954 or 953)
            local tint = front and Color(255, 240, 245, 245) or Color(255, 12, 16, 20)
            local id = texts[text_count]
            if id then
                Gui.update_text(gui, id, value, font_token(fonts.font), size,
                    font_token(fonts.material), pos, tint)
            else
                texts[text_count] = assert(Gui.text(gui, value, font_token(fonts.font), size,
                    font_token(fonts.material), pos, tint), "text_unavailable")
            end
        end
    end
    local function measure(value, size, fonts)
        if Gui.text_extents then
            local ok, width, left = pcall(function()
                local lo, hi = Gui.text_extents(gui, value, font_token(fonts.font), size)
                local readable, l, r = pcall(function() return V3.x(lo), V3.x(hi) end)
                if not readable then
                    -- Some bindings expose the evidenced Vector2 string form only.
                    l = tonumber(tostring(lo):match("^Vector2%(%s*([-%d%.]+)"))
                    r = tonumber(tostring(hi):match("^Vector2%(%s*([-%d%.]+)"))
                end
                assert(finite(l) and finite(r))
                return r - l, l
            end)
            if ok and finite(width) and width >= 0 then return width, left end
        end
        -- Conservative bound when the optional engine metric API is unavailable.
        return #value * size, 0
    end
    local function lines(value, size, fonts, width, output)
        local line = ""
        for word in value:gmatch("%S+") do
            local candidate = line == "" and word or line .. " " .. word
            if measure(candidate, size, fonts) <= width then line = candidate
            else
                if line ~= "" then output[#output + 1] = {line, size} end
                line = ""
                for character in word:gmatch(".") do
                    if line ~= "" and measure(line .. character, size, fonts) > width then
                        output[#output + 1] = {line, size}
                        line = ""
                    end
                    line = line .. character
                end
            end
        end
        if line ~= "" then output[#output + 1] = {line, size} end
    end
    local function label_layout(def, fonts, width)
        if layout_width ~= width then layouts, layout_width = {}, width end
        local cached = layouts[def]
        if cached and cached.name == def.name and cached.caption == def.caption then return cached end
        local output = {}
        lines(def.name .. " / CALL REF", 22, fonts, width - 20, output)
        lines(def.caption, 16, fonts, width - 20, output)
        local height = 0
        for _, line in ipairs(output) do
            line[3], line[4] = measure(line[1], line[2], fonts)
            height = height + line[2] + 5
        end
        cached = {name = def.name, caption = def.caption, lines = output, height = height}
        layouts[def] = cached
        return cached
    end
    local function bind(fonts)
        local key = fonts.font .. "\0" .. fonts.material .. "\0" .. (fonts.atlas or "")
        if font_key == key then return end
        local ink = assert(Gui.material(gui, font_token(fonts.material)), "font_material_unavailable")
        if fonts.atlas then
            for _, parameter in ipairs({"8035c26600000000", "5e8455fe00000000", "309e778300000000", "82b803a800000000"}) do
                sr.Material.set_scalar(ink, I.from_hex(parameter), 0)
            end
            sr.Material.set_vector2(ink, I.from_hex("e13777ce00000000"), V2(1, -1))
            sr.Material.set_vector4(ink, I.from_hex("7701209e00000000"), Color(0, 0, 0, 0))
            sr.Material.set_texture(ink, I.from_hex("88bac99b00000000"), I.from_hex(fonts.atlas))
        end
        font_key = key
        layouts, layout_width = {}, nil
    end

    function renderer.draw(models, fonts, expected_main_world, options)
        return protected(function()
            assert(options == nil or type(options) == "table", "options_invalid")
            local function enabled(key)
                return options == nil or options[key] ~= false
            end
            validate(models)
            if #models == 0 then dispose() return 0 end
            if sr.Window and sr.Window.show_cursor and sr.Window.show_cursor() == true then
                dispose() return 0
            end

            local worlds, main = App.worlds(), App.main_world()
            assert(expected_main_world == nil or main == expected_main_world, "main_world_changed")
            assert(main and live(main, worlds), "main_world_unavailable")
            local overlay
            for _, candidate in ipairs(worlds) do if candidate ~= main then overlay = candidate break end end
            if gui and (world ~= overlay or not live(world, worlds)) then dispose(worlds) end
            assert(overlay, "overlay_world_unavailable")
            -- Copy the pose out of temporaries before any later Vector3 allocation.
            local pose = World.debug_camera_pose(main)
            local translation = sr.Matrix4x4.translation(pose)
            local cx, cy, cz = V3.x(translation), V3.y(translation), V3.z(translation)
            local facing = sr.Matrix4x4.forward(pose)
            local fx, fy, fz = V3.x(facing), V3.y(facing), V3.z(facing)
            local units = World.units_by_resource(main, "core/units/camera")
            local camera, nearest = nil, math.huge
            if type(units) == "table" then
                for _, unit in ipairs(units) do
                    local candidate = sr.Unit.camera(unit, 1)
                    if candidate then
                        local located = sr.Camera.world_position(candidate)
                        local px, py, pz = V3.x(located), V3.y(located), V3.z(located)
                        local dx, dy, dz = px - cx, py - cy, pz - cz
                        local distance = dx * dx + dy * dy + dz * dz
                        if distance < nearest then nearest, camera = distance, candidate end
                    end
                end
            end
            assert(camera, "camera_unavailable")
            local width, height = Gui.resolution()
            assert(finite(width) and finite(height) and width >= 64 and height >= 64, "resolution_unavailable")
            if not gui then
                gui = assert(World.create_screen_gui(overlay, "scale", 1, 1), "gui_unavailable")
                world = overlay
            end

            rect_count, text_count = 0, 0
            local function project(x, y, z, margin)
                assert(finite(x) and finite(y) and finite(z), "geometry_nonfinite")
                local dx, dy, dz = x - cx, y - cy, z - cz
                local depth = dx * fx + dy * fy + dz * fz
                assert(finite(depth), "projection_nonfinite")
                if depth <= 0.5 then return nil end
                -- The next temporary would overwrite this vector. Build it as the call argument.
                local screen = sr.Camera.world_to_screen(camera, V3(x, y, z))
                local sx, sy = V3.x(screen), V3.y(screen)
                assert(finite(sx) and finite(sy), "projection_nonfinite")
                if sx < margin or sy < margin or sx > width - margin or sy > height - margin then return nil end
                return sx, sy
            end
            local function surface(x, y, z)
                local sample = options and options.surface
                if type(sample) ~= "function" then return z end
                local ok, hit = pcall(sample, x, y, z)
                if ok and finite(hit) then return hit end
                return z
            end
            local visible, placed = 0, {}
            local function boundary_style()
                local n = options and options.mark
                if type(n) ~= "number" or n ~= n then return 1 end
                n = math.floor(n + 0.5)
                if n < 1 or n > 4 then return 1 end
                return n
            end
            local function boundary_samples()
                local n = options and options.samples
                if type(n) ~= "number" or n ~= n or n <= -math.huge or n >= math.huge then return SAMPLES end
                n = math.floor(n / 8 + 0.5) * 8
                if n < 8 then return 8 end
                if n > SAMPLES then return SAMPLES end
                return n
            end
            local boundary, marks = boundary_style(), boundary_samples()
            local function units(radius, visit)
                if marks == SAMPLES then
                    for index = 1, SAMPLES do
                        local unit = circle[index]
                        visit(unit[1] * radius, unit[2] * radius)
                    end
                    return
                end
                for index = 0, marks - 1 do
                    local angle = index * math.pi * 2 / marks
                    local c, s = math.cos(angle), math.sin(angle)
                    visit(c * radius, s * radius)
                end
            end
            local function style(kind, tint)
                local size = kind == "inner" and 8 or (kind == "outer" and 6 or 5)
                if tint == "green" then return size, 80, 220, 70 end
                local red = kind == "shock" and 170 or 255
                local green = kind == "inner" and 176 or (kind == "outer" and 48 or 220)
                local blue = kind == "shock" and 255 or 24
                return size, red, green, blue
            end
            local function paint(px, py, size, red, green, blue, rdx, rdy)
                local function bar(horizontal, length)
                    local thick = 2
                    local w = horizontal and length or thick
                    local h = horizontal and thick or length
                    local x = math.max(0, math.min(px - w / 2, width - w))
                    local y = math.max(0, math.min(py - h / 2, height - h))
                    rect(x, y, w, h, 951, red, green, blue)
                end
                if boundary == 4 then
                    local arm = math.max(size, 4) * 2
                    bar(true, arm)
                    bar(false, arm)
                    return
                end
                if (boundary == 2 or boundary == 3) and rdx and (rdx ~= 0 or rdy ~= 0) then
                    local use_x, use_y = rdx, rdy
                    if boundary == 2 then use_x, use_y = -rdy, rdx end
                    bar(math.abs(use_x) >= math.abs(use_y), math.max(size * 3, 8))
                    return
                end
                local half = size / 2
                local x = math.max(0, math.min(px - half, width - size))
                local y = math.max(0, math.min(py - half, height - size))
                rect(x, y, size, size, 951, red, green, blue)
            end
            local function radii(def, draw_one)
                for _, phase in ipairs(def.phases) do
                    local inner, outer, shock = phase.innerRadius, phase.outerRadius, phase.shockwaveRadius
                    if enabled("inner") and inner and outer and math.abs(inner - outer) > 0.05 then
                        draw_one(inner, "inner")
                    end
                    if enabled("outer") then draw_one(outer, "outer") end
                    if enabled("shock") and shock and shock > 0 and (not outer or shock > outer + 0.05) then
                        draw_one(shock, "shock")
                    end
                end
            end
            local reach = 120
            local configured = options and options.range
            if type(configured) == "number" and configured == configured
                and configured > -math.huge and configured < math.huge then
                reach = math.floor(configured / 10 + 0.5) * 10
                if reach < 40 then reach = 40 elseif reach > 500 then reach = 500 end
            end
            local avatar = options and options.avatar
            local known_avatar = type(avatar) == "number" and avatar > 0 and avatar < 4294967296
                and avatar == math.floor(avatar)
            local squad_on = enabled("squad")
            for _, model in ipairs(models) do
                local thrower = model.thrower
                local owned = type(thrower) == "number" and thrower > 0 and thrower < 4294967296
                    and thrower == math.floor(thrower)
                local mine = (owned and known_avatar and thrower == avatar) or model.aim == true
                local theirs = owned and known_avatar and thrower ~= avatar
                local limit = reach
                if not squad_on and not mine and limit > 80 then limit = 80 end
                local pdx, pdy, pdz = model.x - cx, model.y - cy, model.z - cz
                if not (not squad_on and theirs)
                    and pdx * pdx + pdy * pdy + pdz * pdz <= limit * limit then
                if model.aim == true then
                    local shown = false
                    local path = model.path
                    local stride = math.max(1, math.floor(#path / 32))
                    for index = 1, #path, stride do
                        local point = path[index]
                        local px, py = project(point[1], point[2], point[3], 2)
                        if px then
                            rect(px - 1, py - 1, 3, 3, 950, 255, 210, 90)
                            shown = true
                        end
                    end
                    local ax, ay = project(model.x, model.y, model.z, 0)
                    if ax then
                        local arm, thick = 10, 2
                        local left, right = math.max(0, ax - arm), math.min(width, ax + arm)
                        local bottom, top = math.max(0, ay - arm), math.min(height, ay + arm)
                        local band_y, band_x = math.max(0, ay - thick / 2), math.max(0, ax - thick / 2)
                        rect(left, band_y, right - left, math.min(thick, height - band_y), 950, 255, 210, 90)
                        rect(band_x, bottom, math.min(thick, width - band_x), top - bottom, 950, 255, 210, 90)
                        shown = true
                        local radius
                        local cover = model.definition
                        if cover and cover.draw_rings and enabled("dots") then
                            for _, phase in ipairs(cover.phases) do
                                if phase.outerRadius and phase.outerRadius > 0 then radius = phase.outerRadius end
                            end
                        end
                        if radius then
                            for step = 0, 11 do
                                local angle = step * math.pi * 2 / 12
                                local wx = model.x + math.cos(angle) * radius
                                local wy = model.y + math.sin(angle) * radius
                                local qx, qy = project(wx, wy, surface(wx, wy, model.z), 4)
                                if qx then
                                    rect(qx - 1, qy - 1, 3, 3, 950, 255, 196, 64)
                                    shown = true
                                end
                            end
                        end
                    end
                    if shown then visible = visible + 1 end
                else
                local sx, sy = project(model.x, model.y, surface(model.x, model.y, model.z), 0)
                local shown = false
                local def = model.definition
                local ranged = def.draw_rings and enabled("dots")
                local along = ranged and model.axis ~= nil
                if along then
                    local ux, uy = model.axis.x, model.axis.y
                    local px, py = -uy, ux
                    local run = model.runMeters
                    local half = model.centered and run / 2 or 0
                    local x0 = model.x - ux * half
                    local y0 = model.y - uy * half
                    local z0 = model.z
                    local x1 = model.x + ux * (run - half)
                    local y1 = model.y + uy * (run - half)
                    local function outline(radius, kind)
                        if not radius or radius <= 0 then return end
                        local size, red, green, blue = style(kind, def.tint)
                        local function dot(wx, wy, ox, oy)
                            local dx, dy = project(wx, wy, surface(wx, wy, z0), 4)
                            if not dx then return end
                            local radial_x, radial_y
                            if boundary == 2 or boundary == 3 then
                                local cx, cy = project(ox, oy, surface(ox, oy, z0), 4)
                                if cx then radial_x, radial_y = dx - cx, dy - cy end
                            end
                            paint(dx, dy, size, red, green, blue, radial_x, radial_y)
                            shown = true
                        end
                        if def.bursts then
                            local step = run / (def.bursts - 1)
                            for index = 0, def.bursts - 1 do
                                local along_m = -half + index * step
                                local bx, by = model.x + ux * along_m, model.y + uy * along_m
                                units(radius, function(ox, oy)
                                    dot(bx + ox, by + oy, bx, by)
                                end)
                            end
                            return
                        end
                        local side, cap = CORRIDOR_SIDE, CORRIDOR_CAP
                        if marks ~= SAMPLES then
                            side = math.max(2, math.floor(CORRIDOR_SIDE * marks / SAMPLES + 0.5))
                            cap = math.max(2, math.floor(CORRIDOR_CAP * marks / SAMPLES + 0.5))
                        end
                        for i = 0, side - 1 do
                            local t = i / (side - 1)
                            local bx = x0 + (x1 - x0) * t
                            local by = y0 + (y1 - y0) * t
                            dot(bx + px * radius, by + py * radius, bx, by)
                            dot(bx - px * radius, by - py * radius, bx, by)
                        end
                        for i = 1, cap - 1 do
                            local theta = -math.pi / 2 + i * math.pi / cap
                            local c, s = math.cos(theta), math.sin(theta)
                            dot(x1 + (c * ux + s * px) * radius, y1 + (c * uy + s * py) * radius, x1, y1)
                            dot(x0 + (-c * ux + s * px) * radius, y0 + (-c * uy + s * py) * radius, x0, y0)
                        end
                    end
                    radii(def, outline)
                end
                if sx then
                    if ranged and not along then
                        local function ring(radius, kind)
                            if not radius or radius <= 0 then return end
                            local size, red, green, blue = style(kind, def.tint)
                            units(radius, function(ox, oy)
                                local qx, qy = project(model.x + ox, model.y + oy,
                                    surface(model.x + ox, model.y + oy, model.z), 4)
                                if not qx then return end
                                local radial_x, radial_y
                                if boundary == 2 or boundary == 3 then radial_x, radial_y = qx - sx, qy - sy end
                                paint(qx, qy, size, red, green, blue, radial_x, radial_y)
                                shown = true
                            end)
                        end
                        radii(def, ring)
                    end
                    if enabled("cross") then
                        local arm, thick = 16, 4
                        local left, right = math.max(0, sx - arm), math.min(width, sx + arm)
                        local bottom, top = math.max(0, sy - arm), math.min(height, sy + arm)
                        local band_y, band_x = math.max(0, sy - thick / 2), math.max(0, sx - thick / 2)
                        rect(left, band_y, right - left, math.min(thick, height - band_y), 952, 255, 230, 40)
                        rect(band_x, bottom, math.min(thick, width - band_x), top - bottom, 952, 255, 230, 40)
                        shown = true
                    end
                    if enabled("labels") then
                        placed[#placed + 1] = {model, sx, sy}
                        shown = true
                    end
                end
                if shown then visible = visible + 1 end
                end
                end
            end
            local function clear_texts()
                for n = #texts, 1, -1 do Gui.destroy_text(gui, texts[n]) texts[n] = nil end
                text_count, font_key = 0, nil
            end
            if not usable_fonts(fonts) then
                clear_texts()
            else
                local labels_ok = pcall(function()
                    bind(fonts)
                    for _, mark in ipairs(placed) do
                        local model, sx, sy = mark[1], mark[2], mark[3]
                        local layout = label_layout(model.definition, fonts, width)
                        assert(layout.height <= height - 20, "label_render_limit")
                        local y = math.max(10, math.min(sy + 14, height - layout.height - 10)) + layout.height
                        for _, line in ipairs(layout.lines) do
                            y = y - line[2] - 5
                            local line_width, bearing = line[3], line[4]
                            local x = math.max(10, math.min(sx - line_width / 2, width - line_width - 10))
                            text(line[1], x - bearing, y, line[2], fonts)
                        end
                    end
                end)
                if not labels_ok then clear_texts() end
            end
            for n = #rects, rect_count + 1, -1 do Gui.destroy_rect(gui, rects[n]) rects[n] = nil end
            for n = #texts, text_count + 1, -1 do Gui.destroy_text(gui, texts[n]) texts[n] = nil end
            if visible == 0 then dispose(worlds)
            elseif type(Gui.set_visible) == "function" then Gui.set_visible(gui, true) end
            return visible
        end)
    end
    return renderer
end
