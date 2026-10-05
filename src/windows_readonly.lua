-- Current-process, read-only Windows adapter. No game offsets or runtime dependency.
-- Ordinary operation failures return nil/false plus a reason; factory failures raise.
local function create_runtime()
    local loaded, ffi = pcall(require, 'ffi')
    if not loaded or ffi.os ~= 'Windows' or ffi.arch ~= 'x64' then
        error('windows_readonly requires Windows x64 LuaJIT FFI', 0)
    end

    -- Keep both libraries alive with the function pointers. Only the two bootstrap
    -- symbols need declarations; reuse any equivalent declarations another mod made.
    local native = {kernel_library = ffi.load('kernel32'), bcrypt_library = ffi.load('bcrypt')}
    local function bootstrap(name, declaration, signature)
        local ok, symbol = pcall(function() return native.kernel_library[name] end)
        if not ok then
            ffi.cdef(declaration)
            symbol = native.kernel_library[name]
        end
        return ffi.cast(signature, symbol)
    end
    local get_module = bootstrap('GetModuleHandleA',
        'void * __stdcall GetModuleHandleA(const char *);',
        'void * (__stdcall *)(const char *)')
    local get_proc = bootstrap('GetProcAddress',
        'void * __stdcall GetProcAddress(void *, const char *);',
        'void * (__stdcall *)(void *, const char *)')
    local kernel_handle, bcrypt_handle = get_module('kernel32.dll'), get_module('bcrypt.dll')
    assert(kernel_handle ~= nil and bcrypt_handle ~= nil, 'readonly Windows libraries unavailable')
    local function bind(handle, name, signature)
        local address = get_proc(handle, name)
        assert(address ~= nil, 'readonly Windows API unavailable: ' .. name)
        native[name] = ffi.cast(signature, address)
    end
    native.GetModuleHandleA = get_module
    bind(kernel_handle, 'GetCurrentProcess', 'void * (__stdcall *)(void)')
    bind(kernel_handle, 'GetLastError', 'uint32_t (__stdcall *)(void)')
    bind(kernel_handle, 'GetTickCount64', 'uint64_t (__stdcall *)(void)')
    bind(kernel_handle, 'ReadProcessMemory',
        'int (__stdcall *)(void *, const void *, void *, size_t, size_t *)')
    bind(kernel_handle, 'GetModuleFileNameW',
        'uint32_t (__stdcall *)(void *, uint16_t *, uint32_t)')
    bind(kernel_handle, 'CreateFileW',
        'void * (__stdcall *)(const uint16_t *, uint32_t, uint32_t, void *, uint32_t, uint32_t, void *)')
    bind(kernel_handle, 'GetFileSizeEx', 'int (__stdcall *)(void *, int64_t *)')
    bind(kernel_handle, 'ReadFile',
        'int (__stdcall *)(void *, void *, uint32_t, uint32_t *, void *)')
    bind(kernel_handle, 'CloseHandle', 'int (__stdcall *)(void *)')
    bind(bcrypt_handle, 'BCryptOpenAlgorithmProvider',
        'int32_t (__stdcall *)(void **, const uint16_t *, const uint16_t *, uint32_t)')
    bind(bcrypt_handle, 'BCryptCreateHash',
        'int32_t (__stdcall *)(void *, void **, void *, uint32_t, void *, uint32_t, uint32_t)')
    bind(bcrypt_handle, 'BCryptHashData',
        'int32_t (__stdcall *)(void *, void *, uint32_t, uint32_t)')
    bind(bcrypt_handle, 'BCryptFinishHash',
        'int32_t (__stdcall *)(void *, void *, uint32_t, uint32_t)')
    bind(bcrypt_handle, 'BCryptDestroyHash', 'int32_t (__stdcall *)(void *)')
    bind(bcrypt_handle, 'BCryptCloseAlgorithmProvider', 'int32_t (__stdcall *)(void *, uint32_t)')

    local MAX_EXACT = 9007199254740991
    local MAX_READ, HASH_CHUNK, MAX_FILE = 1048576, 65536, 8589934592
    local PATH_CAPACITY = 32768
    local process = native.GetCurrentProcess() -- Pseudo-handle: do not CloseHandle.
    local invalid_handle = ffi.cast('void *', -1)
    local memory_count = ffi.new('size_t[1]')
    local memory_buffer
    local file_count, file_size = ffi.new('uint32_t[1]'), ffi.new('int64_t[1]')
    local file_buffer = ffi.new('uint8_t[?]', HASH_CHUNK)
    local path_buffer = ffi.new('uint16_t[?]', PATH_CAPACITY)
    local algorithm, hash = ffi.new('void *[1]'), ffi.new('void *[1]')
    local digest, digest_hex = ffi.new('uint8_t[32]'), ffi.new('uint8_t[64]')
    local sha256_name = ffi.new('uint16_t[7]', {83, 72, 65, 50, 53, 54, 0})
    local hex = '0123456789ABCDEF'
    local buffer_capacities = setmetatable({}, {__mode = 'k'})
    local runtime = {}

    local function win_error(operation)
        return operation .. ': Win32 error ' .. tostring(tonumber(native.GetLastError()))
    end
    local function check_status(operation, status)
        -- NT_SUCCESS uses the sign bit, not equality with STATUS_SUCCESS.
        if status < 0 then
            error(operation .. ': NTSTATUS ' .. tostring(tonumber(status)), 0)
        end
    end
    local function valid_range(address, size)
        return type(address) == 'number' and address >= 65536 and address <= MAX_EXACT
            and address % 1 == 0 and type(size) == 'number' and size >= 0
            and size <= MAX_READ and size % 1 == 0 and size <= MAX_EXACT - address
    end

    function runtime.module(name)
        if name ~= nil and (type(name) ~= 'string' or #name == 0
            or #name >= PATH_CAPACITY or name:find('\0', 1, true)) then
            return nil, 'invalid_module_name'
        end
        local handle = native.GetModuleHandleA(name)
        if handle == nil then return nil, win_error('GetModuleHandleA') end
        return handle -- Borrowed handle: do not FreeLibrary.
    end

    function runtime.address(handle)
        if type(handle) ~= 'cdata' or handle == nil then return nil, 'invalid_module_handle' end
        local ok, value = pcall(ffi.cast, 'uintptr_t', handle)
        if not ok or value == 0 or value > MAX_EXACT then return nil, 'inexact_or_invalid_address' end
        return tonumber(value)
    end

    local function read_memory(address, size, buffer)
        memory_count[0] = 0
        if native.ReadProcessMemory(process, ffi.cast('const void *', address),
            buffer, size, memory_count) == 0 then
            return false, win_error('ReadProcessMemory')
        end
        if memory_count[0] ~= size then return false, 'ReadProcessMemory: short_read' end
        return true
    end

    function runtime.read_into(address, size, buffer)
        if not valid_range(address, size) then return false, 'invalid_read_range' end
        if type(buffer) ~= 'cdata' then return false, 'expected_writable_ffi_array' end
        local capacity = buffer_capacities[buffer]
        if not capacity then
            -- Pointer capacity cannot be established with sizeof. Accept owned,
            -- non-const arrays only, and cache the check for the hot sampling path.
            local description = tostring(ffi.typeof(buffer))
            if not description:find('[', 1, true) or description:find('*', 1, true)
                or description:find('const', 1, true) then
                return false, 'expected_writable_ffi_array'
            end
            capacity = ffi.sizeof(buffer)
            if not capacity then return false, 'unknown_buffer_capacity' end
            buffer_capacities[buffer] = capacity
        end
        if size > capacity then return false, 'buffer_capacity_exceeded' end
        if size == 0 then return true end
        return read_memory(address, size, buffer)
    end

    function runtime.read(address, size)
        if not valid_range(address, size) then return nil, 'invalid_read_range' end
        if size == 0 then return '' end
        if not memory_buffer then memory_buffer = ffi.new('uint8_t[?]', MAX_READ) end
        local ok, reason = read_memory(address, size, memory_buffer)
        if not ok then return nil, reason end
        return ffi.string(memory_buffer, size)
    end

    function runtime.module_hash(handle)
        local address, reason = runtime.address(handle)
        if not address then return nil, reason end
        local file
        algorithm[0], hash[0] = nil, nil
        local ok, result = pcall(function()
            local length = native.GetModuleFileNameW(ffi.cast('void *', address), path_buffer, PATH_CAPACITY)
            if length == 0 then error(win_error('GetModuleFileNameW'), 0) end
            if length >= PATH_CAPACITY then error('GetModuleFileNameW: truncated_path', 0) end
            -- GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING. Deny new writers and
            -- renames while hashing; this is the disk file, not mapped image bytes.
            file = native.CreateFileW(path_buffer, 0x80000000, 1, nil, 3, 0x08000000, nil)
            if file == nil or file == invalid_handle then
                file = nil
                error(win_error('CreateFileW'), 0)
            end
            if native.GetFileSizeEx(file, file_size) == 0 then error(win_error('GetFileSizeEx'), 0) end
            if file_size[0] < 0 or file_size[0] > MAX_FILE then error('module_file_size_out_of_bounds', 0) end
            local remaining = tonumber(file_size[0])
            check_status('BCryptOpenAlgorithmProvider',
                native.BCryptOpenAlgorithmProvider(algorithm, sha256_name, nil, 0))
            -- Windows 7+ owns and releases the object buffer with BCryptDestroyHash.
            check_status('BCryptCreateHash', native.BCryptCreateHash(algorithm[0], hash, nil, 0, nil, 0, 0))
            while remaining > 0 do
                local count = math.min(remaining, HASH_CHUNK)
                file_count[0] = 0
                if native.ReadFile(file, file_buffer, count, file_count, nil) == 0 then
                    error(win_error('ReadFile'), 0)
                end
                if file_count[0] ~= count then error('ReadFile: short_read', 0) end
                check_status('BCryptHashData', native.BCryptHashData(hash[0], file_buffer, count, 0))
                remaining = remaining - count
            end
            -- A growing file must not produce a successful digest of a prefix.
            file_count[0] = 0
            if native.ReadFile(file, file_buffer, 1, file_count, nil) == 0 then
                error(win_error('ReadFile'), 0)
            end
            if file_count[0] ~= 0 then error('module_file_changed_during_hash', 0) end
            check_status('BCryptFinishHash', native.BCryptFinishHash(hash[0], digest, 32, 0))
            for index = 0, 31 do
                local byte = digest[index]
                local high, low = math.floor(byte / 16), byte % 16
                digest_hex[index * 2] = hex:byte(high + 1)
                digest_hex[index * 2 + 1] = hex:byte(low + 1)
            end
            return ffi.string(digest_hex, 64)
        end)

        -- Cleanup is outside the protected operation, so all acquired resources
        -- are released even when a read, hash step, or Lua operation fails.
        local cleanup_reason
        if hash[0] ~= nil then
            local status = native.BCryptDestroyHash(hash[0])
            hash[0] = nil
            if status < 0 then cleanup_reason = 'BCryptDestroyHash: NTSTATUS ' .. tostring(tonumber(status)) end
        end
        if algorithm[0] ~= nil then
            local status = native.BCryptCloseAlgorithmProvider(algorithm[0], 0)
            algorithm[0] = nil
            if status < 0 then
                cleanup_reason = (cleanup_reason and cleanup_reason .. '; ' or '')
                    .. 'BCryptCloseAlgorithmProvider: NTSTATUS ' .. tostring(tonumber(status))
            end
        end
        if file ~= nil and native.CloseHandle(file) == 0 then
            cleanup_reason = (cleanup_reason and cleanup_reason .. '; ' or '') .. win_error('CloseHandle')
        end
        if not ok then
            return nil, tostring(result) .. (cleanup_reason and '; ' .. cleanup_reason or '')
        end
        if cleanup_reason then return nil, cleanup_reason end
        return result
    end

    function runtime.monotonic_time()
        return tonumber(native.GetTickCount64()) / 1000
    end

    return runtime
end

return create_runtime
