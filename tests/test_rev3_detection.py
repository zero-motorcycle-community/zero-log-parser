"""
Regression tests for get_version_and_header()'s REV0/REV1/REV3 routing.

NOTE ON CONVENTIONS: this repo's pyproject.toml configures pytest with
`testpaths = ["tests"]` and `python_files = ["test_*.py", "*_test.py"]`,
and the README references a `log_data/` sample-file directory, but neither
`tests/` nor `log_data/` exist anywhere in this repo's git history as of
HEAD ff7f770 - there is no prior test file to mirror conventions from. This
file follows pytest's own defaults (plain `test_*` functions, no fixtures
framework beyond stdlib `tempfile`) since that is what the existing
configuration implies. It builds every input as a small, synthetic byte
buffer constructed in-process; no corpus files are checked in.

Background: a branch added in commit 796cfdc ("Updated log structure")
routes any exactly-262144-byte file containing `\\xa1\\xa1\\xa1\\xa1`
anywhere into the REV3 ring-buffer branch, discarding Serial number,
Firmware rev., Board rev., and Model. Classic REV0/REV1 files are also
almost always exactly 262144 bytes and carry their own, unrelated
`\\xa1\\xa1\\xa1\\xa1` fencepost near their own header (their "first run
date" field), so this condition misrouted the large majority of legacy
files too (see analysis/issue11_status.md and analysis/fix_rev3_detection.md
in the private analysis corpus this fix was validated against - not part
of this repo). The fix tried here: check a legacy VIN offset match first,
and only fall back to the coarse `\\xa1\\xa1\\xa1\\xa1`-anywhere heuristic
if none of the three legacy offsets produce a valid VIN. `raw()[0] == 0xb2`
remains an unconditional, independent signal for real ring-buffer files.
"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import LogFile, LogData, REV0, REV1, REV3, REV_UNKNOWN


WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
TRANSLIT = {}
for _i, _c in enumerate("ABCDEFGH"):
    TRANSLIT[_c] = _i + 1
for _i, _c in enumerate("JKLMN"):
    TRANSLIT[_c] = _i + 1
TRANSLIT['P'] = 7
TRANSLIT['R'] = 9
for _i, _c in enumerate("STUVWXYZ"):
    TRANSLIT[_c] = _i + 2
for _d in "0123456789":
    TRANSLIT[_d] = int(_d)


def _check_digit(vin17_placeholder_at_8):
    """FMVSS 565 check digit for a 17-char VIN. Position 9 (index 8) -
    where the placeholder sits - has weight 0, so its actual character is
    never read; still must be a key TRANSLIT recognizes, so callers pass
    '0' there rather than a non-VIN sentinel character."""
    total = 0
    for ch, weight in zip(vin17_placeholder_at_8, WEIGHTS):
        total += TRANSLIT[ch] * weight
    remainder = total % 11
    return 'X' if remainder == 10 else str(remainder)


def make_vin(serial_tail="12345"):
    """A syntactically and check-digit valid Zero VIN: 538, S platform,
    a plausible model line/HP/year/plant, correct check digit, 5-digit
    serial tail. Structure doesn't need to be semantically perfect, only
    charset + check-digit valid, matching what `is_vin()` and the
    check-digit validator used elsewhere in this analysis both accept."""
    body_no_check = "538SDJZ6" + "0" + "H" + "C" + "A"  # positions 1-8, 10-13 (9 is the check digit slot)
    # positions: 1-3 WMI, 4 type, 5-6 line, 7-8 hp, 9 check(calc), 10 year, 11 plant, 12 model, 13-17 serial
    prefix = "538SDJZ6"   # 1-8
    year = "H"            # 10 = 2017
    plant = "C"           # 11
    model = "A"           # 12
    serial = serial_tail.rjust(5, "0")[:5]  # 13-17
    template = prefix + "0" + year + plant + model + serial  # 17 chars, placeholder at index 8 (weight 0)
    check = _check_digit(template)
    vin = prefix + check + year + plant + model + serial
    assert len(vin) == 17
    return vin


def build_classic_buffer(vin, vin_offset, total_len=0x40000, magic=b"MBB\x00"):
    """A minimal classic-format (REV0/REV1-shaped) buffer: board-type magic
    at offset 0, the given VIN at the given offset, and an \\xa1\\xa1\\xa1\\xa1
    fencepost near offset 0x26 - the exact combination that commit 796cfdc's
    condition (before this fix) misrouted into the REV3 branch."""
    buf = bytearray(b"\x00" * total_len)
    buf[0:len(magic)] = magic
    buf[0x26:0x2a] = b"\xa1\xa1\xa1\xa1"
    buf[vin_offset:vin_offset + 17] = vin.encode("ascii")
    return bytes(buf)


def build_ring_buffer_buffer(total_len=300):
    """A minimal real ring-buffer-format buffer: starts with 0xb2 at offset
    0, short enough that legacy-offset reads (0x240/0x252) would be out of
    bounds if ever attempted - this is also the regression case this fix
    introduced and then corrected during development: reading the legacy
    VIN offsets unconditionally, even for genuine short ring-buffer files,
    raised struct.error. The fixed routing must never attempt those reads
    when byte 0 is 0xb2."""
    buf = bytearray(b"\xff" * total_len)
    buf[0] = 0xb2
    return bytes(buf)


def _write_temp(data, suffix="_538SDJZ60HCA12345_MBB.bin"):
    f = tempfile.NamedTemporaryFile(prefix="ziptest", suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return f.name


def _parse(path):
    lf = LogFile(path)
    ld = object.__new__(LogData)
    ld.log_file = lf
    return ld.get_version_and_header(lf)


def test_rev1_classic_file_is_not_misrouted_to_rev3():
    vin = make_vin("11686")
    data = build_classic_buffer(vin, vin_offset=0x252)
    path = _write_temp(data, suffix=f"_{vin}_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV1, f"expected REV1, got {log_version}"
    assert header["VIN"] == vin
    assert header["Serial number"] != "Unknown"
    assert header["Firmware rev."] != "Unknown"
    assert header["Model"] != "Unknown"


def test_rev0_classic_file_is_not_misrouted_to_rev3():
    vin = make_vin("08745")
    data = build_classic_buffer(vin, vin_offset=0x240)
    path = _write_temp(data, suffix=f"_{vin}_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV0, f"expected REV0, got {log_version}"
    assert header["VIN"] == vin
    assert header["Serial number"] != "Unknown"
    assert header["Firmware rev."] != "Unknown"
    assert header["Model"] != "Unknown"


def test_genuine_ring_buffer_file_still_routes_to_rev3():
    data = build_ring_buffer_buffer()
    path = _write_temp(data, suffix="_538ZFAZ74LCK12051_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV3, f"expected REV3, got {log_version}"
    assert header["VIN"] == "538ZFAZ74LCK12051"  # recovered via filename fallback


def test_short_ring_buffer_file_does_not_crash():
    """Regression guard: a real ring-buffer file need not be anywhere near
    262144 bytes. The routing fix must never unconditionally read the
    legacy 0x240/0x252/0x029 offsets on a raw()[0] == 0xb2 file, or a short
    one raises struct.error instead of parsing."""
    data = build_ring_buffer_buffer(total_len=128)
    path = _write_temp(data, suffix="_538ZFAZ75LCK13158_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV3
    assert header["VIN"] == "538ZFAZ75LCK13158"


def test_unrecognized_format_gets_distinct_sentinel_not_rev0():
    """Neither a ring-buffer marker nor a valid VIN at any legacy offset:
    must not be silently reported as REV0 (see analysis/issue11_status.md,
    section 0b, and the Task 2 fix in analysis/fix_rev3_detection.md)."""
    data = bytearray(b"\x00" * 4096)
    data[0:4] = b"MBB\x00"
    path = _write_temp(data, suffix="_notarealvin_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV_UNKNOWN, f"expected REV_UNKNOWN, got {log_version}"
    assert log_version != REV0


def test_unrecognized_format_header_has_no_missing_keys():
    """Gap found on review of the REV_UNKNOWN fix: REV3's branch explicitly
    sets Serial number/Firmware rev./Board rev. to 'Unknown' when it can't
    determine them, but the REV_UNKNOWN branch originally left those three
    keys out of sys_info entirely rather than setting them - present in
    REV3's output, silently absent in REV_UNKNOWN's. Downstream code was
    audited and nothing indexes these keys directly without a presence
    check (see analysis/fix_rev3_detection.md), so this was never a
    KeyError risk - but the inconsistency is real on a parser about to run
    against live public uploads, so the REV_UNKNOWN branch now matches
    REV3's pattern: same three keys, same 'Unknown' value, every time."""
    data = bytearray(b"\x00" * 4096)
    data[0:4] = b"MBB\x00"
    path = _write_temp(data, suffix="_notarealvin_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV_UNKNOWN
    for key in ("Serial number", "Firmware rev.", "Board rev."):
        assert key in header, f"{key!r} missing from header entirely (should be present, value 'Unknown')"
        assert header[key] == "Unknown"


def test_rev1_board_rev_is_read_from_0x268():
    """uint16 @ 0x268, confirmed against 1,982 true-REV1 corpus files as a
    closed 8-value set with 93%+ per-VIN stability - see
    analysis/rev1_board_rev.md and analysis/issue11_status.md section 0c.
    2980 is the most common value found there, used here as a realistic
    sample rather than an arbitrary sentinel."""
    vin = make_vin("11686")
    data = bytearray(build_classic_buffer(vin, vin_offset=0x252))
    data[0x268:0x268 + 2] = struct.pack("<H", 2980)
    path = _write_temp(bytes(data), suffix=f"_{vin}_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV1
    assert header["Board rev."] == 2980
