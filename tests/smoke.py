"""Exercise upstream DSPTool using original, redistributable test instructions."""

import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="dsptool-test-") as directory:
        root = Path(directory)

        def run(*args, success=True):
            result = subprocess.run(
                [str(executable), *map(str, args)], cwd=root,
                capture_output=True, text=True,
            )
            if (result.returncode == 0) != success:
                raise AssertionError(f"{args}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
            return result.stdout

        assert "USAGE" in run("--help")
        source = root / "test.ds"
        binary = root / "test.bin"
        listing = root / "test.txt"
        # NOP, a two-word immediate load, a label reference, and HALT.
        source.write_text("start:\nNOP\nLRI $AR0, #0x1234\nJMP start\nHALT\n", encoding="utf-8")
        run("-o", binary, source)
        expected = bytes.fromhex("0000 0080 1234 029f 0000 0021")
        assert binary.read_bytes() == expected, binary.read_bytes().hex()
        run("-d", "-o", listing, binary)
        text = listing.read_text().lower()
        for mnemonic in ("nop", "lri", "jmp", "halt"):
            assert mnemonic in text, text
        run("-c", "-o", binary, binary)
        different = root / "different.bin"
        different.write_bytes(bytes.fromhex("0021"))
        run("-c", "-o", different, binary, success=False)
        run("-h", root / "code", source)
        assert "dsp_code" in (root / "code.h").read_text()
        (root / "included.ds").write_text("NOP\nHALT\n")
        source.write_text('INCLUDE "included.ds"\n')
        run("-o", binary, source)
        assert binary.read_bytes() == bytes.fromhex("0000 0021")
        source.write_text("THIS_IS_NOT_AN_OPCODE\n")
        run("-o", binary, source, success=False)
        run(root / "missing.ds", success=False)
    print("DSPTool CLI smoke tests passed")


if __name__ == "__main__":
    main()
