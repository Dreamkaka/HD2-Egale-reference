-- Synthetic native structures, not HD2Runtime mocks. All reads copy through real LuaJIT FFI.
local ffi = require('ffi')
local h = {logs={}, temp=100, created=0, rectangles=0, destroyed=0, reads=0,
    candidate_reads=0, update_calls=0, now=0, modules_available=true}
local blocks, base = {}, 0x10000000
local function block(address, size)
    local b = ffi.new('uint8_t[?]', size)
    blocks[address] = {bytes=b, size=size}
    return b
end
local function u32(b, offset, value) ffi.cast('uint32_t *', b+offset)[0]=value end
local function u64(b, offset, value) ffi.cast('uint64_t *', b+offset)[0]=value end
local function f32(b, offset, value) ffi.cast('float *', b+offset)[0]=value end
local function global(rva, value) local b=block(base+rva,8);u64(b,0,value) end

global(53633856,0x20000000)
local game_state=block(0x20000000+705052,4);u32(game_state,0,4)
global(53634720,0x21000000)
local mode=block(0x21000000,68);u32(mode,8,1);u64(mode,56,0x22000000);u32(mode,64,1)
local descriptor=block(0x22000000,24);u32(descriptor,8,300);u32(descriptor,20,1)
global(55037680,0x23000000)
local local_peer=block(0x23000000+45976,8);u64(local_peer,0,77)
global(53634152,0x24000000)
local players=block(0x24000000,1100)
u32(players,132,1);u32(players,136,1);u64(players,232,0x25000000)
u64(players,712,77);u32(players,736,3);u32(players,936,10)
local player_descriptor=block(0x25000000,24);u32(player_descriptor,8,501)
global(0x33266B0,0x26000000)
local active=block(0x26000000,128);u32(active,0x34,1);u64(active,0x78,0x26100000)
local active_rows=block(0x26100000,512*64)
u32(active_rows,12,30);f32(active_rows,16,1.25);f32(active_rows,20,-2.5);f32(active_rows,24,3.75)
f32(active_rows,48,1.25);f32(active_rows,52,-2.5);f32(active_rows,56,3.75)
local font=block(base+0x3772268,8);u32(font,0,0x11111111);u32(font,4,0x11111111)
local atlas=block(base+0x3772ee8,8);u32(atlas,0,0x33333333);u32(atlas,4,0x33333333)
local owner=block(base+0x37c5478,8);u64(owner,0,0x2a000000)
local material=block(0x2a000000+24,8);u32(material,0,0x22222222);u32(material,4,0x22222222)
function h.set_font_owner(value)u64(owner,0,value)end
function h.use_unaligned_font_owner()
    u64(owner,0,0x2b000004)
    local moved=block(0x2b000004,32)
    u32(moved,24,0x22222222);u32(moved,28,0x22222222)
end

local runtime = {
    module=function(name) if h.modules_available then return name end end,
    address=function() return base end,
    module_hash=function(name)
        if h.bad_hash then return 'unsupported' end
        if name=='game.dll' then
            return '2E2C3B7C2500646DADD5F2B4C6E0504DBB7E7896139F64CDDC0D1813C718F51E'
        end
        return 'F5FEE03DCFDB2E553A4752C283590950AC13316B376D8196AA556FF0400D5F06'
    end,
    read=function() error('Unexpected allocating native read') end,
    read_into=function(address,size,out)
        h.reads=h.reads+1
        if address==base+0x33266B0 then
            h.candidate_reads=h.candidate_reads+1
        end
        if h.throw_read then error('native read failure') end
        if address==h.unreadable then return false end
        for at,data in pairs(blocks) do
            if address>=at and address+size<=at+data.size then
                ffi.copy(out,data.bytes+(address-at),size)
                return true
            end
        end
        return false
    end,
    monotonic_time=function() return h.now end
}
h.factory=function() return runtime end
function update(dt,...)
    h.update_calls=h.update_calls+1
    if h.throw_update then error('preceding update failure') end
    return 'preceding',nil,...
end
h.original_update=update
function shutdown(...) h.shutdown_called=true;return 'shutdown',... end
h.original_shutdown=shutdown
CowboyBingusModLoader={api=1,version=17,open_log=function()
    return {write=function(_,s) assert(not h.log_closed);h.logs[#h.logs+1]=s;return true end,
        flush=function()return true end,close=function()h.log_closed=true end}
end}
h.tick=function(dt)
    dt=dt or 0.11;h.now=h.now+dt
    return update(dt,'payload',nil,42)
end
h.end_mission=function()u32(game_state,0,3);h.tick()end
h.begin_mission=function(entity)u32(descriptor,8,entity or 301);u32(game_state,0,4);h.tick()end
h.set_state=function(value)u32(game_state,0,value)end
h.set_player_count=function(value)u32(players,132,value)end
h.set_lifecycle=function(lifecycle,avatar)
    u32(players,736,lifecycle)
    if avatar then u32(players,936,avatar)end
end
h.set_active_count=function(value)u32(active,0x34,value)end
h.set_x=function(value)f32(active_rows,16,value)end
h.set_z=function(value)f32(active_rows,24,value)end
h.mark_thrower=function(avatar, owner)
    local list=0x27000000
    global(55037488, list)
    local head=block(list, 16+88)
    u32(head, 8, 0); u32(head, 12, 1)
    u32(head, 16, 20)
    f32(head, 20, tonumber(ffi.cast('float *', active_rows+16)[0]))
    f32(head, 24, tonumber(ffi.cast('float *', active_rows+20)[0]))
    f32(head, 32, 9999); f32(head, 36, 0.1); u32(head, 40, owner)
    local entities=0x28000000
    global(54968216, entities)
    local body=block(entities, 15937312+4)
    local slots=0x29000000
    u64(body, 15871688, slots)
    u32(body, 15871696, 16)
    u32(body, 15871700, 0xffffffff)
    u32(body, 15871704, 1)
    local map=block(slots, 16*8)
    for i=0,15 do u32(map, i*8, 0xffffffff) end
    u32(map, 80, 10); u32(map, 84, 0)
    u32(body, 15937312, avatar)
end
h.set_type=function(value)u32(active_rows,12,value)end
h.set_anchor=function(x,y,z)
    f32(active_rows,48,x);f32(active_rows,52,y);f32(active_rows,56,z)
end
h.set_row=function(index,kind,x,y,z)
    local offset=index*64
    u32(active_rows,offset+12,kind)
    f32(active_rows,offset+16,x);f32(active_rows,offset+20,y);f32(active_rows,offset+24,z)
    f32(active_rows,offset+48,x);f32(active_rows,offset+52,y);f32(active_rows,offset+56,z)
end
h.fail_fonts=function(fail)h.unreadable=fail and base+0x3772268 or nil end
h.fail_active=function(fail)h.unreadable=fail and 0x26100000 or nil end
h.fail_state=function(fail)h.unreadable=fail and 0x20000000+705052 or nil end
return h
