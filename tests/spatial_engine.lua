-- Isolated engine contract fixture: retained primitives copy values, whereas
-- vectors, cameras and IdString64 values expire when the temporary pool rewinds.
local h = {temp = 100, allocations = {}, created = 0, destroyed = 0,
    camera_reads = 0, projection_calls = 0, updates = 0, deleted_rects = 0,
    deleted_texts = 0, camera_x = 0, camera_y = 0, camera_z = 0,
    width = 1920, height = 1080, bound = 0, menu = false, guis = {}}
local sr = {}
h.sr = sr
h.main = {name = "main"}
h.worlds = {h.main, {}, {}, {}, {}, {}, {}}
h.worlds.metadata = {}
local function live(world)
    for _, candidate in ipairs(h.worlds) do if world == candidate then return true end end
    return false
end
local function temporary(value)
    h.temp = h.temp + 16
    value.valid = true
    h.allocations[#h.allocations + 1] = value
    return value
end
local function check(value)
    assert(value and value.valid, "expired native temporary")
    return value
end
local function font_arg(value)
    if type(value) == "string" then
        assert(value:match("^[%w_/%-]+$"), "unexpected font resource")
        return value
    end
    check(value)
    return value.hex
end
local vector_meta = {}
local function vector(...)
    return setmetatable(temporary({...}), vector_meta)
end
vector_meta.__sub = function(a, b)
    check(a) check(b)
    local x, y, z = a[1] - b[1], a[2] - b[2], a[3] - b[3]
    if h.clobber_left_on_sub then
        a[1], a[2], a[3] = x, y, z
        return a
    end
    return vector(x, y, z)
end
local function constructor(fn)
    return setmetatable({}, {__metatable = "native callable", __call = function(_, ...) return fn(...) end})
end
sr.Vector3 = constructor(vector)
sr.Vector2 = constructor(vector)
sr.Color = constructor(vector)
sr.Vector3.x = function(v) return check(v)[1] end
sr.Vector3.y = function(v) return check(v)[2] end
sr.Vector3.z = function(v) return check(v)[3] end
sr.Vector3.dot = function(a, b)
    check(a) check(b)
    return a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
end
sr.Script = {
    temp_byte_count = function() return h.temp end,
    set_temp_byte_count = function(saved)
        assert(saved == 100, "unexpected outer temporary pool boundary")
        for _, value in ipairs(h.allocations) do value.valid = false end
        h.allocations = {}
        h.temp = saved
    end,
}
sr.Application = {worlds = function() return h.worlds end, main_world = function() return h.main end}
sr.Window = {show_cursor = function() return h.menu end}
sr.IdString64 = {from_hex = function(hex)
    assert(type(hex) == "string" and #hex == 16)
    if h.fail_font then error("font conversion failed") end
    return temporary({hex = hex})
end}
local function owned(gui)
    assert(gui and not gui.dead and live(gui.world), "native GUI access after world death")
end
sr.World = {
    create_screen_gui = function(world, mode, x, y)
        assert(live(world) and world ~= h.main and mode == "scale" and x == 1 and y == 1)
        local gui = {world = world, rects = {}, texts = {}, next_id = 0}
        h.created = h.created + 1
        h.gui = gui
        h.guis[#h.guis + 1] = gui
        return gui
    end,
    destroy_gui = function(world, gui)
        owned(gui) assert(world == gui.world)
        gui.dead = true
        gui.rects, gui.texts = {}, {}
        h.destroyed = h.destroyed + 1
    end,
    units_by_resource = function(world, resource)
        assert(world == h.main and live(world) and resource == "core/units/camera")
        h.camera_reads = h.camera_reads + 1
        if h.fail_camera then return {} end
        local primary = {world = world}
        if h.dummy_center_camera then
            primary.stick_center = true
            primary.camera_x, primary.camera_y, primary.camera_z = 9999, 9999, 9999
        end
        return {primary, {world = world, not_the_first = true}}
    end,
    debug_camera_pose = function(world)
        assert(world == h.main)
        return temporary({x = h.camera_x, y = h.camera_y, z = h.camera_z})
    end,
}
sr.Unit = {camera = function(unit, index)
    assert(unit.world == h.main and index == 1)
    return temporary({x = unit.camera_x or h.camera_x, y = unit.camera_y or h.camera_y,
        z = unit.camera_z or h.camera_z, stick_center = unit.stick_center or false})
end}
sr.Matrix4x4 = {
    translation = function(pose) check(pose) return vector(pose.x, pose.y, pose.z) end,
    forward = function(pose) check(pose) return vector(0, 0, 1) end,
}
sr.Camera = {
    world_position = function(camera)
        check(camera)
        return vector(camera.x, camera.y, camera.z)
    end,
    world_to_screen = function(camera, point)
        check(camera) check(point)
        h.projection_calls = h.projection_calls + 1
        if h.bad_projection then return vector(0 / 0, 0, 0) end
        if camera.stick_center then return vector(h.width / 2, h.height / 2, 0) end
        return vector(h.width / 2 + (point[1] - camera.x) * 10,
            h.height / 2 + (point[2] - camera.y) * 10, 0)
    end,
}
local function copied(v)
    check(v)
    return {v[1], v[2], v[3], v[4]}
end
local function rect(gui, id, position, size, color)
    owned(gui)
    if h.fail_update then error("primitive update failed") end
    gui.rects[id] = {position = copied(position), size = copied(size), color = copied(color)}
end
local function text(gui, id, value, font, size, material, position, color)
    owned(gui)
    local font_name, material_name = font_arg(font), font_arg(material)
    if h.fail_update then error("primitive update failed") end
    gui.texts[id] = {value = value, font = font_name, material = material_name, size = size,
        position = copied(position), color = copied(color)}
end
local function new_id(gui)
    gui.next_id = gui.next_id + 1
    return gui.next_id
end
sr.Gui = {
    resolution = function() return h.width, h.height end,
    material = function(gui, material)
        owned(gui)
        return temporary({gui = gui, material = font_arg(material)})
    end,
    rect = function(gui, ...)
        local id = new_id(gui) rect(gui, id, ...) return id
    end,
    update_rect = function(gui, id, ...)
        assert(gui.rects[id], "updating unowned rectangle")
        h.updates = h.updates + 1
        rect(gui, id, ...)
    end,
    destroy_rect = function(gui, id)
        owned(gui) assert(gui.rects[id]) gui.rects[id] = nil
        h.deleted_rects = h.deleted_rects + 1
    end,
    text = function(gui, ...)
        local id = new_id(gui) text(gui, id, ...) return id
    end,
    update_text = function(gui, id, ...)
        assert(gui.texts[id], "updating unowned text")
        h.updates = h.updates + 1
        text(gui, id, ...)
    end,
    destroy_text = function(gui, id)
        owned(gui) assert(gui.texts[id]) gui.texts[id] = nil
        h.deleted_texts = h.deleted_texts + 1
    end,
    text_extents = function(gui, value, font, size)
        owned(gui) font_arg(font)
        return vector(0, 0, 0), vector(#value * size * 0.6, size, 0)
    end,
}
local scalar_slots = {['8035c26600000000'] = true, ['5e8455fe00000000'] = true,
    ['309e778300000000'] = true, ['82b803a800000000'] = true}
sr.Material = {
    set_scalar = function(ink, slot, value)
        check(ink) check(slot) assert(scalar_slots[slot.hex] and value == 0)
    end,
    set_vector2 = function(ink, slot, value)
        check(ink) check(slot) check(value)
        assert(slot.hex == 'e13777ce00000000' and value[1] == 1 and value[2] == -1)
    end,
    set_vector4 = function(ink, slot, value)
        check(ink) check(slot) check(value)
        assert(slot.hex == '7701209e00000000')
        for n = 1, 4 do assert(value[n] == 0) end
    end,
    set_texture = function(ink, slot, atlas)
        check(ink) check(slot) check(atlas)
        assert(slot.hex == '88bac99b00000000')
        h.bound = h.bound + 1
        h.bound_atlas = atlas.hex
    end,
}
function h.count(kind, layer)
    local count = 0
    for _, primitive in pairs(h.gui[kind]) do
        if not layer or primitive.position[3] == layer then count = count + 1 end
    end
    return count
end
function h.cross_x()
    for _, primitive in pairs(h.gui.rects) do
        if primitive.position[3] == 952 and primitive.size[1] > 2 then
            return primitive.position[1] + primitive.size[1] / 2
        end
    end
end
function h.model(x, radius)
    return {x = x or 0, y = 0, z = 10, definition = {name = "Eagle Airstrike", type = 1,
        caption = "BASELINE I/O/S 1/3/6 m - REFERENCE ONLY", draw_rings = true,
        phases = {{label = "impact", innerRadius = 1, outerRadius = radius or 3, shockwaveRadius = 6}}}}
end
h.fonts = {font = '1111111111111111', material = '2222222222222222', atlas = '3333333333333333'}
return h
