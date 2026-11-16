"""Extract embedded 7z data without executing the Qt installer."""
from pathlib import Path
import struct
import shutil
import subprocess
import zlib

data = Path("svp4-linux.run").read_bytes()
output = Path("extracted")
if output.exists():
    shutil.rmtree(output)
output.mkdir()
position = 0
count = 0
while (position := data.find(b"7z\xbc\xaf\x27\x1c", position)) >= 0:
    start = position
    position += 6
    if start + 32 > len(data):
        continue
    if zlib.crc32(data[start + 12:start + 32]) != struct.unpack_from("<I", data, start + 8)[0]:
        continue
    offset, size = struct.unpack_from("<QQ", data, start + 12)
    end = start + 32 + offset + size
    if end > len(data):
        raise ValueError("Truncated embedded archive")
    archive = Path(f"embedded-{start}.7z")
    archive.write_bytes(data[start:end])
    subprocess.run(["7z", "x", "-y", f"-o{output}", str(archive)], check=True)
    archive.unlink()
    count += 1
if not count or not (output / "SVPManager").is_file():
    raise ValueError("SVP payload not found")
