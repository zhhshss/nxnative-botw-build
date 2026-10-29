#!/usr/bin/env python3
"""Audit target fingerprints and the distributable Mach-O/signature without running either."""
import hashlib
import pathlib
import re
import struct
import sys

UUID = bytes.fromhex('4c4c441a55553144a1fd82f0291e2f93')
CODE = [(0xa7bc0, 248, '4471b68afde0380e614489bf175bf981556ffb606fc000ec3697006e30415cac'),
        (0xaa200, 512, '0c3620e1335f9b31de4c83f4c773f55316c5e1f77bdccf6b3419092090ce52ad')]

def audit_probe(data, symbols):
    header = (pathlib.Path(__file__).resolve().parents[1] / 'src/NXKRenderProbe.h').read_text()
    entries = re.findall(r'\{(0x[0-9a-f]+)u,(\d+)u,\{([^}]+)\}\}, /\* (\w+) \*/', header)
    assert len(entries) == 6, 'missing renderer probe fingerprints'
    for offset_text, size_text, hash_text, name in entries:
        offset, size = int(offset_text, 16), int(size_text)
        digest = bytes(int(v.strip(), 16) for v in hash_text.split(','))
        assert symbols.get(name) == 0x100000000 + offset, f'{name} address changed'
        assert hashlib.sha256(data[offset:offset + size]).digest() == digest, f'{name} code changed'
    frame = int(re.search(r'#define NXK_RENDER_FRAME_OFFSET (0x[0-9a-f]+)u', header).group(1), 16)
    assert symbols.get('_g_cur_frame') == 0x100000000 + frame, 'renderer frame-counter location changed'
    _, commands = macho(data)
    for cmd, value in commands:
        if cmd != 0x19 or value[8:24].rstrip(b'\0') != b'__DATA':
            continue
        address, length = struct.unpack_from('<QQ', value, 24)
        protection = struct.unpack_from('<I', value, 60)[0]
        assert protection & 3 == 3
        assert address <= 0x100000000 + frame <= address + length - 8
        break
    else:
        raise AssertionError('renderer frame counter is not mapped in writable data')
    return len(entries)

def macho(data):
    assert len(data) >= 32
    magic, cpu, subcpu, kind, count, command_bytes, flags, reserved = struct.unpack_from('<8I', data)
    assert magic == 0xfeedfacf and cpu == 0x100000c
    assert command_bytes <= len(data) - 32
    pos = 32
    commands = []
    for _ in range(count):
        cmd, size = struct.unpack_from('<II', data, pos)
        assert size >= 8 and pos + size <= 32 + command_bytes
        commands.append((cmd, data[pos:pos + size]))
        pos += size
    return kind, commands

def target_matches(data):
    _, commands = macho(data)
    uuid = next(value[8:24] for cmd, value in commands if cmd == 0x1b)
    if uuid[1:] != UUID[1:]:
        return False
    return all(hashlib.sha256(data[offset:offset + size]).hexdigest() == digest
               for offset, size, digest in CODE)

def audit_signature(data, command):
    off, size = struct.unpack_from('<II', command, 8)
    assert off + size <= len(data)
    blob = data[off:off + size]
    magic, length, count = struct.unpack_from('>III', blob)
    assert magic == 0xfade0cc0 and length <= len(blob)
    directories = []
    for i in range(count):
        kind, offset = struct.unpack_from('>II', blob, 12 + 8 * i)
        if kind == 0 or 0x1000 <= kind < 0x1005:
            directories.append(offset)
    assert directories
    for offset in directories:
        cd = blob[offset:]
        magic, length, version, flags, hash_offset, ident_offset, specials, slots, limit = struct.unpack_from('>9I', cd)
        hash_size, hash_type, platform, page_shift = struct.unpack_from('>4B', cd, 36)
        assert magic == 0xfade0c02 and flags & 2, 'missing ad-hoc CodeDirectory'
        assert hash_type == 2 and hash_size == 32 and limit == off
        page = 1 << page_shift
        assert slots == (limit + page - 1) // page
        for slot in range(slots):
            start = slot * page
            expected = cd[hash_offset + slot * hash_size:hash_offset + (slot + 1) * hash_size]
            assert hashlib.sha256(data[start:min(start + page, limit)]).digest() == expected
    return slots

def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: verify.py ORIGINAL_DmIOS NXKeyboard.dylib')
    target = pathlib.Path(sys.argv[1]).read_bytes()
    plugin = pathlib.Path(sys.argv[2]).read_bytes()
    assert target_matches(target), 'target binary is not supported'
    kind, commands = macho(target)
    assert kind == 2
    symbols = next(value for cmd, value in commands if cmd == 2)
    symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', symbols, 8)
    sampling = []
    named_symbols = {}
    for i in range(nsyms):
        index, ntype, section, desc, value = struct.unpack_from('<IBBHQ', target, symoff + i * 16)
        name = target[stroff + index:stroff + strsize].split(b'\0', 1)[0]
        if ntype in (14, 15):
            named_symbols[name.decode()] = value
        if ntype == 14 and name == b'_g_sampling':
            sampling.append(value)
    assert sampling == [0x1022481a0], 'sampling-counter location changed'
    probes = audit_probe(target, named_symbols)
    # LiveContainer converts the executable to MH_DYLIB, shrinks PAGEZERO and increments UUID.
    patched = bytearray(target)
    struct.pack_into('<I', patched, 12, 6)
    pos = 32
    for cmd, value in commands:
        if cmd == 0x1b:
            patched[pos + 8] = (patched[pos + 8] + 7) % 256
        if cmd == 0x19 and value[8:24].rstrip(b'\0') == b'__PAGEZERO':
            struct.pack_into('<QQ', patched, pos + 24, 0x100000000 - 0x4000, 0x4000)
        pos += len(value)
    assert target_matches(patched), 'LiveContainer transformation breaks identity matching'
    patched[CODE[0][0]] ^= 1
    assert not target_matches(patched), 'altered input code was not rejected'
    kind, commands = macho(plugin)
    assert kind == 6
    symtab = next(value for cmd, value in commands if cmd == 2)
    symoff, count, stroff, strsize = struct.unpack_from('<4I', symtab, 8)
    imports = set()
    for i in range(count):
        index, ntype, section, desc, value = struct.unpack_from('<IBBHQ', plugin, symoff + i * 16)
        if ntype & 0x0e == 0:
            imports.add(plugin[stroff + index:stroff + strsize].split(b'\0', 1)[0].decode())
    assert '_thread_get_state' in imports, 'register sampler is missing'
    assert not {'_thread_suspend', '_thread_resume'} & imports, 'sampler must not own thread suspend counts'
    dependencies = []
    initializer = False
    code_signature = None
    for cmd, value in commands:
        if cmd in (0xc, 0x80000018):
            name_offset = struct.unpack_from('<I', value, 8)[0]
            name = value[name_offset:].split(b'\0')[0].decode()
            assert name.startswith(('/usr/lib/', '/System/Library/Frameworks/')), name
            dependencies.append(name)
        if cmd == 0x1d:
            code_signature = value
        if cmd == 0x19:
            sections = struct.unpack_from('<I', value, 64)[0]
            for i in range(sections):
                section = value[72 + i * 80:72 + (i + 1) * 80]
                if section[:16].rstrip(b'\0') == b'__mod_init_func':
                    initializer = struct.unpack_from('<Q', section, 40)[0] > 0
    assert initializer and code_signature
    pages = audit_signature(plugin, code_signature)
    print(f'PASS: original target; {probes} renderer function fingerprints and mapped frame counter; LiveContainer UUID/MH_DYLIB/PAGEZERO transformations; changed code rejected; arm64 dylib; constructor; register sampler without suspend/resume imports; {len(dependencies)} system dependencies; {pages} signature pages.')
    print(f'Plugin SHA-256: {hashlib.sha256(plugin).hexdigest()}')

if __name__ == '__main__':
    main()
