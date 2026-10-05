-- Stateless CALL REF models, not sortie identities or predicted bomb impacts.
-- A strafing anchor 1-80 m away only supplies direction. It is not a proven thrower.
local function create_references(catalog)
    assert(type(catalog) == 'table' and type(catalog.entries) == 'table',
        'reference catalog entries required')
    local entries = catalog.entries
    local references = {}

    local function coordinate(value)
        return type(value) == 'number' and value == value
            and value >= -100000 and value <= 100000
    end

    function references.collect(rows)
        if type(rows) ~= 'table' then
            return nil, 'reference_snapshot_unavailable'
        end
        -- Do not let ipairs truncate a malformed/sparse snapshot unnoticed.
        local count, last = 0, 0
        for key in pairs(rows) do
            if type(key) ~= 'number' or key < 1 or key % 1 ~= 0 then
                return nil, 'reference_snapshot_malformed'
            end
            count = count + 1
            if key > last then last = key end
        end
        if count ~= last then return nil, 'reference_snapshot_malformed' end

        local models = {}
        for i = 1, count do
            local row = rows[i]
            local definition
            if type(row) == 'table' and type(row.native_type_candidate) == 'number' then
                definition = entries[row.native_type_candidate]
            end
            if definition then
                if not coordinate(row.x) or not coordinate(row.y) or not coordinate(row.z) then
                    return nil, 'reference_coordinates_invalid'
                end
                if row.ax ~= nil or row.ay ~= nil or row.az ~= nil then
                    if not coordinate(row.ax) or not coordinate(row.ay) or not coordinate(row.az) then
                        return nil, 'reference_coordinates_invalid'
                    end
                end
                if #models == 16 then return nil, 'reference_render_limit:16' end
                local model = {x = row.x, y = row.y, z = row.z, definition = definition}
                local thrower = row.thrower
                if type(thrower) == 'number' and thrower > 0 and thrower < 4294967296
                    and thrower == math.floor(thrower) then
                    model.thrower = thrower
                end
                local run = definition.runMeters
                if row.ax ~= nil and type(run) == 'number' and run == run and run > 0 and run <= 168 then
                    local dx, dy = row.x - row.ax, row.y - row.ay
                    local dist = math.sqrt(dx * dx + dy * dy)
                    -- Inside 1 m the axis is noise. Past 80 m it is not a plausible throw.
                    if dist >= 1 and dist <= 80 then
                        local ux, uy = dx / dist, dy / dist
                        if definition.pattern == 'across' then
                            model.axis = {x = -uy, y = ux}
                            model.centered = true
                        else
                            model.axis = {x = ux, y = uy}
                        end
                        model.runMeters = run
                    end
                end
                models[#models + 1] = model
            end
        end
        return models, nil
    end

    return references
end

return create_references
