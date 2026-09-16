#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_unpack.py —— 命令行压缩包解压工具（编码由用户手动指定，不做任何自动猜测）

依赖安装命令
------------
    # zip / tar / tar.gz / tar.bz2 / tar.xz 由 Python 标准库支持，无需额外依赖

    # 可选：解压 .7z 需要
    pip install py7zr

    # 可选：解压 .rar 需要（rarfile 还需要外部 unrar/bsdtar 可执行文件）
    pip install rarfile

一行装齐（按需）：
    pip install py7zr rarfile

编码策略（重点）
----------------
本工具**不检测、不猜测**任何编码，全部由用户显式指定：

    --name-encoding   压缩包内“文件名”的原始编码，默认 utf-8
    --text-encoding   文本文件“内容”的编码，默认 utf-8

    可选值：utf-8、gbk、gb18030、shift_jis、cp932、euc-jp、euc-kr、big5、latin-1

处理规则：
    * 文件名：还原出原始字节后，直接用 --name-encoding 解码，不做任何猜测；
    * 文本内容：直接用 --text-encoding 解码，然后统一以 UTF-8 写出；
    * 二进制文件：不解码，原样逐字节拷贝；
    * 指定的编码无法解码某个文件名或文本时，跳过该文件并输出【警告】，不中断整体解压。

常见用法：
    日文压缩包（如测试样本 ワイト Ver1.00.zip）通常用 CP932 / Shift-JIS：
        --name-encoding shift_jis --text-encoding shift_jis
    简体中文老压缩包通常用 GBK：
        --name-encoding gbk --text-encoding gbk

功能一览
--------
    -o, --output DIR   指定解压目录（默认：压缩包同目录下的同名文件夹）
    --overwrite        覆盖已有文件（默认跳过并提示，不中断）
    --list             仅列出内容，不解压
    --dry-run          模拟运行，只预览会解压哪些文件
    --verbose          详细日志（含每个文件的编码处理结果）
    -q, --quiet        静默模式，只输出最终汇总
    --batch            批量解压一个目录下的所有压缩包（各自解压到同名文件夹）
    --recursive        配合 --batch，递归扫描子目录
    --password PWD     加密压缩包的密码（zip/7z/rar）
    --unsafe-paths     允许写出目标目录之外的路径（默认拦截 ../ 越界）

Windows / Linux / macOS 通用：路径统一用 os.path.join，Windows 控制台自动切到 UTF-8，
中文、日文、韩文文件名不会乱码。

使用示例
--------
    # 1) 日文压缩包：文件名与文本内容都按 Shift-JIS 处理（最常用）
    python auto_unpack.py "ワイト Ver1.00.zip" --name-encoding shift_jis --text-encoding shift_jis --verbose

    # 2) 中文老压缩包：按 GBK 处理
    python auto_unpack.py 资料打包.zip --name-encoding gbk --text-encoding gbk

    # 3) 现代压缩包（UTF-8）：直接用默认值即可
    python auto_unpack.py 项目备份.zip

    # 4) 批量解压：目录下所有日文压缩包，各自解压到同名文件夹
    python auto_unpack.py /data/jp --batch --name-encoding cp932 --text-encoding cp932

    # 5) 递归处理子目录 + 覆盖已有文件 + 静默输出
    python auto_unpack.py /data/archives --batch --recursive --overwrite -q \
        --name-encoding gbk --text-encoding gbk

    # 6) 辅助用法：只看内容 / 先模拟运行确认无误 / 指定输出目录与密码
    python auto_unpack.py "ワイト Ver1.00.zip" --list --name-encoding shift_jis
    python auto_unpack.py "ワイト Ver1.00.zip" --dry-run --name-encoding shift_jis
    python auto_unpack.py 加密包.zip --password 123456 -o /data/out
"""

from __future__ import annotations

import argparse
import codecs
import os
import stat
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


# ---------------------------------------------------------------------------
# 0. 常量
# ---------------------------------------------------------------------------

# 允许用户指定的编码（同时用于 --name-encoding 与 --text-encoding）
ENCODING_CHOICES = (
    "utf-8", "gbk", "gb18030", "shift_jis", "cp932",
    "euc-jp", "euc-kr", "big5", "latin-1",
)

# 压缩包格式 -> 后缀列表（用于按文件名分发；zip/7z/rar 还会用魔数嗅探兜底）
FORMAT_SUFFIXES = {
    "zip": (".zip",),
    "tar": (".tar",),
    "tar.gz": (".tar.gz", ".tgz"),
    "tar.bz2": (".tar.bz2", ".tbz2", ".tbz"),
    "tar.xz": (".tar.xz", ".txz"),
    "7z": (".7z",),
    "rar": (".rar",),
}

# 7z / rar 的魔数，用于扩展名缺失或写错时兜底识别
MAGIC_7Z = b"7z\xbc\xaf\x27\x1c"
MAGIC_RAR = (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")

# 文本 / 二进制判定阈值（纯字节特征判断，不做任何编码解码）
_TEXT_SNIFF_BYTES = 1 << 20        # 最多嗅探 1 MiB
_TEXT_SNIFF_MIN_SIZE = 16           # 小于该字节数直接当文本处理
_BINARY_MIN_SAMPLE = 512            # 至少看 512 字节再统计控制字符
_MAX_CONTROL_RATIO = 0.30           # 控制字符占比超过该值视为二进制

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130


# ---------------------------------------------------------------------------
# 1. Windows 终端编码：确保中文/日文文件名在控制台不乱码
# ---------------------------------------------------------------------------

def setup_console_encoding() -> None:
    """让标准输出在 Windows 上也能正确显示中日韩文字。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass  # 非交互流或无 reconfigure 时忽略
    if os.name == "nt":  # pragma: no cover - 仅在 Windows 上执行
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # 65001 = UTF-8
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 2. 文本 / 二进制判定（只看字节特征，不涉及任何编码解码）
# ---------------------------------------------------------------------------

def looks_like_text(data: bytes) -> bool:
    """启发式判断字节流是不是文本：含 NUL 或控制字符过多则视为二进制。

    这里只统计字节特征，完全不尝试任何编码解码——编码由用户通过
    --text-encoding 指定。
    """
    if len(data) < _TEXT_SNIFF_MIN_SIZE:
        return True
    sample = data[:_TEXT_SNIFF_BYTES]
    if b"\x00" in sample:
        return False  # NUL 字节基本可断定是二进制
    head = sample[:_BINARY_MIN_SAMPLE]
    controls = sum(
        1 for b in head
        if b < 0x20 and b not in (0x09, 0x0A, 0x0D, 0x0C, 0x0B, 0x1B)  # 制表/换行/回车等
    )
    return controls / max(len(head), 1) <= _MAX_CONTROL_RATIO


def decode_text(data: bytes, encoding: str) -> bytes:
    """按用户指定编码把文本解码，再统一以 UTF-8 写出。

    解码失败时不再立刻报错，而是先回退用 UTF-8 严格解码一次——真实压缩包常常是
    混合编码（例如整体 Shift-JIS，个别文件却是 UTF-8 带 BOM 或无 BOM），
    这一步能让那些文件也被正确解出来，而不是被整包跳过。
    只有在 UTF-8 也解码失败时，才按原逻辑抛出 UnicodeDecodeError，
    由上层跳过该文件并输出【警告】。
    """
    try:
        return data.decode(encoding, errors="strict").encode("utf-8")
    except (UnicodeDecodeError, LookupError) as first_error:
        try:
            text = data.decode("utf-8", errors="strict")
        except (UnicodeDecodeError, LookupError):
            raise first_error         # UTF-8 也失败 -> 交给上层跳过并告警
        if "--verbose" in sys.argv:   # 仅在 verbose 模式下提示回退
            print(f"提示：用 {encoding} 解码失败，已回退 UTF-8 解码")
        return text.encode("utf-8")


# ---------------------------------------------------------------------------
# 3. 文件名解码（直接用用户指定编码，不猜）
# ---------------------------------------------------------------------------

def zip_entry_bytes(info: zipfile.ZipInfo) -> bytes:
    """还原 ZIP 条目文件名的原始字节。

    ZIP 规范：flag bit 0x800 表示文件名是 UTF-8；否则按 cp437 解释。
    zipfile 已按该规则把名字解码成了 str，这里只是机械地反向还原字节；
    真正的“用什么编码解读”仍由 --name-encoding 决定，不做任何猜测。
    """
    if info.flag_bits & 0x800:                      # 归档自己声明是 UTF-8
        return info.orig_filename.encode("utf-8", errors="surrogateescape")
    try:
        raw = info.orig_filename.encode("cp437")    # cp437 可逆还原原始字节
        if raw.decode("cp437") == info.orig_filename:
            return raw
    except UnicodeEncodeError:
        pass
    # 少数写入工具不设 0x800 却写入 UTF-8 字节，此时按 UTF-8 还原字节
    return info.orig_filename.encode("utf-8", errors="surrogateescape")


# ---------------------------------------------------------------------------
# 4. 路径安全与写出
# ---------------------------------------------------------------------------

def sanitize_member_path(name: str) -> str | None:
    """把压缩包内的成员名转成安全的相对路径。

    去掉绝对路径与盘符，拒绝 ".." 越界（Zip Slip），返回 None 表示该成员不安全。
    """
    if not name:
        return None
    normalized = name.replace("\\", "/")
    if len(normalized) > 1 and normalized[1] == ":":     # 去掉盘符 C:\...
        normalized = normalized[2:]
    parts = [p for p in PurePosixPath(normalized.lstrip("/")).parts
             if p not in ("", ".", "/")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) if parts else None


def safe_join(dest_root: Path, rel_path: str, check_escape: bool = True) -> Path | None:
    """把相对路径拼到目标目录下（统一用 os.path.join），并确认未越出目标目录。"""
    target = Path(os.path.join(str(dest_root), *PurePosixPath(rel_path).parts))
    if check_escape:
        try:
            target.resolve().relative_to(dest_root.resolve())
        except ValueError:
            return None  # 越界，拒绝写出
    return target


def existing_names(dest_root: Path) -> set[str]:
    """收集目标目录下已存在的相对文件名（小写，便于 Windows 大小写不敏感比较）。"""
    names: set[str] = set()
    if not dest_root.exists():
        return names
    for dirpath, _dirnames, filenames in os.walk(dest_root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                names.add(os.path.relpath(full, dest_root).replace("\\", "/").lower())
            except ValueError:
                continue
    return names


def unique_path(path: Path) -> Path:
    """给已存在的文件生成 xxx(1).txt 形式的备用名。"""
    stem, suffix, parent = path.stem, path.suffix, path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def write_file(target: Path, payload: bytes, out_mode: int | None, dry_run: bool) -> None:
    """写出文件；dry_run 时不做任何写入。"""
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(payload)
    # 仅在归档里确实带了权限位时才 chmod；mode 为 0 会写出不可读的文件
    if out_mode:
        perms = stat.S_IMODE(out_mode) & 0o7777
        if perms:
            try:
                os.chmod(target, perms)
            except OSError:
                pass  # 某些文件系统不支持 chmod，忽略即可


# ---------------------------------------------------------------------------
# 5. 统一异常
# ---------------------------------------------------------------------------

class UnpackError(Exception):
    """本工具所有可预期错误的基类，message 直接面向用户（中文）。"""


class ArchiveError(UnpackError):
    """压缩包损坏 / 无法读取。"""


class MissingDependency(UnpackError):
    """缺少第三方库或外部依赖。"""


class PasswordError(UnpackError):
    """加密压缩包相关问题。"""


# ---------------------------------------------------------------------------
# 6. 解压后端（格式分发）
# ---------------------------------------------------------------------------

class Member:
    """统一的成员描述。

    name 为 None 表示文件名无法用 --name-encoding 解码（会被跳过并告警），
    具体原因见 name_error。
    """

    __slots__ = ("name", "is_dir", "size", "mode", "raw", "name_error")

    def __init__(self, name, is_dir=False, size=None, mode=None, raw=None,
                 name_error=None):
        self.name = name            # 解码后的相对路径；None 表示解码失败
        self.is_dir = is_dir
        self.size = size
        self.mode = mode
        self.raw = raw              # 后端私有数据（tarinfo 等）
        self.name_error = name_error


class BaseExtractor:
    format_name = "unknown"

    def __init__(self, path: Path, options: "Options", log):
        self.path = path
        self.options = options
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self) -> None:
        pass

    def iter_members(self):
        raise NotImplementedError

    def read_member(self, member: Member) -> bytes:
        raise NotImplementedError


class ZipExtractor(BaseExtractor):
    format_name = "ZIP"

    def __init__(self, path, options, log):
        super().__init__(path, options, log)
        self.zf = zipfile.ZipFile(path)  # 损坏时抛 BadZipFile，由上层统一处理

    def close(self):
        self.zf.close()

    def iter_members(self):
        encoding = self.options.name_encoding
        for info in self.zf.infolist():
            raw = zip_entry_bytes(info)
            try:
                name = raw.decode(encoding, errors="strict")   # 用户指定编码，严格解码
                name_error = None
            except (UnicodeDecodeError, LookupError) as exc:
                # 解不出来就跳过该成员，并给出原始字节便于排查
                name = None
                name_error = (f"文件名无法用 {encoding} 解码"
                              f"（原始字节 {raw!r}）：{exc}")
            is_dir = info.is_dir() or info.orig_filename.endswith(("/", "\\"))
            yield Member(name, is_dir=is_dir, size=info.file_size,
                         mode=info.external_attr >> 16, raw=info,
                         name_error=name_error)

    def read_member(self, member: Member) -> bytes:
        pwd = self.options.password
        return self.zf.read(member.raw, pwd=pwd.encode() if pwd else None)


class TarExtractor(BaseExtractor):
    """tar / tar.gz / tar.bz2 / tar.xz 通用（tarfile 自动识别压缩方式）。"""

    def __init__(self, path, options, log, format_name="TAR"):
        super().__init__(path, options, log)
        self.format_name = format_name
        try:
            self.tf = tarfile.open(path, mode="r:*")  # r:* 自动处理 gz/bz2/xz
        except tarfile.ReadError as exc:
            raise ArchiveError(f"无法读取 tar 归档：{exc}") from exc
        # tar 头里的成员名是原始字节，直接按用户编码解释
        self.tf.encoding = options.name_encoding

    def close(self):
        self.tf.close()

    def _decode_name(self, info):
        """把 tar 成员名还原成原始字节后，按用户编码严格解码。

        tarfile 内部用 surrogateescape 容错、不会报错；这里显式还原字节再严格解码，
        这样才能准确发现“编码不对”的情况并跳过该成员。
        """
        try:
            raw = info.name.encode(self.tf.encoding, errors="surrogateescape")
            return raw.decode(self.options.name_encoding, errors="strict"), None
        except (UnicodeDecodeError, UnicodeEncodeError, LookupError) as exc:
            return None, f"文件名无法用 {self.options.name_encoding} 解码：{exc}"

    def iter_members(self):
        for info in self.tf:
            name, name_error = self._decode_name(info)
            if info.isdir():
                yield Member(name, is_dir=True, size=None, mode=info.mode,
                             raw=info, name_error=name_error)
            elif info.isfile():
                yield Member(name, is_dir=False, size=info.size, mode=info.mode,
                             raw=info, name_error=name_error)
            elif info.issym() or info.islnk():
                yield Member(name, is_dir=False, size=None, mode=info.mode,
                             raw=info, name_error=name_error)
            # 其他类型（设备节点、FIFO 等）忽略，避免安全隐患

    def read_member(self, member: Member) -> bytes:
        info = member.raw
        if info.issym() or info.islnk():
            return b""
        handle = self.tf.extractfile(info)
        if handle is None:
            return b""
        with handle:
            return handle.read()


class SevenZipExtractor(BaseExtractor):
    """7z 后端。

    注意：7z 格式内部的文件名本身就是 UTF-16，py7zr 返回的已是 Unicode 字符串，
    没有可供重新解码的原始字节，因此 --name-encoding 对 7z 不生效（仅对 zip/tar 生效）。
    """

    format_name = "7Z"

    def __init__(self, path, options, log):
        super().__init__(path, options, log)
        try:
            import py7zr  # 延迟导入，未安装时只报错不崩溃
        except ImportError as exc:
            raise MissingDependency(
                "解压 7z 需要 py7zr，请执行：pip install py7zr"
            ) from exc
        self.py7zr = py7zr
        try:
            self.zf = py7zr.SevenZipFile(path, mode="r", password=options.password)
        except py7zr.exceptions.Bad7zFile as exc:
            raise ArchiveError(f"7z 文件损坏或格式不正确：{exc}") from exc
        except py7zr.exceptions.PasswordRequired as exc:
            raise PasswordError("7z 已加密，请用 --password 提供密码") from exc

    def close(self):
        try:
            self.zf.close()
        except Exception:
            pass

    def iter_members(self):
        for entry in self.zf.list():
            name = entry.filename
            is_dir = bool(getattr(entry, "is_directory", False))
            size = None if is_dir else getattr(entry, "uncompressed", None)
            yield Member(name, is_dir=is_dir, size=size, raw=entry)

    def read_member(self, member: Member) -> bytes:
        """按需读取单个 7z 成员（兼容不同版本的 py7zr API）。"""
        if member.is_dir:
            return b""
        targets = [member.name]
        try:
            self.zf.reset()  # 允许重复读取
        except Exception:
            pass
        if hasattr(self.zf, "read"):
            try:
                data = self.zf.read(targets=targets)
            except Exception:
                data = None
            if data:
                for payload in data.values():
                    return payload.read()
        # 回退：解到临时目录后读出，再删除临时文件
        import tempfile

        with tempfile.TemporaryDirectory(prefix="auto_unpack_7z_") as tmp:
            try:
                self.zf.extract(path=tmp, targets=targets)
            except Exception as exc:
                raise ArchiveError(f"读取 7z 成员失败：{member.name}（{exc}）") from exc
            candidate = Path(tmp).joinpath(*PurePosixPath(member.name).parts)
            if candidate.is_file():
                return candidate.read_bytes()
        raise ArchiveError(f"7z 中找不到成员：{member.name}")


class RarExtractor(BaseExtractor):
    """rar 后端；文件名由 rarfile 内部解码，因此 --name-encoding 对 rar 不生效。"""

    format_name = "RAR"

    def __init__(self, path, options, log):
        super().__init__(path, options, log)
        try:
            import rarfile  # 延迟导入
        except ImportError as exc:
            raise MissingDependency(
                "解压 rar 需要 rarfile 及外部 unrar/bsdtar，请执行：pip install rarfile"
            ) from exc
        self.rarfile = rarfile
        try:
            self.rf = rarfile.RarFile(path)
        except rarfile.NeedFirstVolume as exc:
            raise ArchiveError("这是分卷压缩包的非首卷，请从第一个分卷开始解压") from exc
        except rarfile.BadRarFile as exc:
            raise ArchiveError(f"rar 文件损坏或格式不正确：{exc}") from exc
        except rarfile.RarCannotExec as exc:
            raise MissingDependency(
                "rarfile 需要外部 unrar/bsdtar 可执行文件，请先安装（由 rarfile 内部使用）："
                f"{exc}"
            ) from exc
        if options.password:
            self.rf.setpassword(options.password)

    def close(self):
        self.rf.close()

    def iter_members(self):
        for info in self.rf.infolist():
            yield Member(info.filename, is_dir=info.isdir(), size=info.file_size,
                         mode=None, raw=info)

    def read_member(self, member: Member) -> bytes:
        try:
            with self.rf.open(member.raw) as handle:
                return handle.read()
        except self.rarfile.PasswordRequired as exc:
            raise PasswordError("rar 已加密，请用 --password 提供密码") from exc
        except self.rarfile.RarWrongPassword as exc:
            raise PasswordError("rar 密码错误") from exc


# ---------------------------------------------------------------------------
# 7. 格式识别与分发
# ---------------------------------------------------------------------------

def sniff_format(path: Path) -> str | None:
    """用魔数嗅探真实格式（比扩展名更可靠）。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(MAGIC_7Z):
        return "7z"
    if head.startswith(MAGIC_RAR):
        return "rar"
    return None


def detect_format(path: Path) -> str:
    """格式分发：先按（长后缀优先的）扩展名判断，再魔数嗅探，最后尝试 tar 校验。"""
    name = path.name.lower()
    for fmt, suffixes in sorted(FORMAT_SUFFIXES.items(),
                                key=lambda kv: -max(len(s) for s in kv[1])):
        if any(name.endswith(suffix) for suffix in suffixes):
            return fmt
    if name.endswith(".gz"):
        return "tar.gz"
    if name.endswith(".bz2"):
        return "tar.bz2"
    if name.endswith(".xz"):
        return "tar.xz"

    magic = sniff_format(path)
    if magic:
        return magic

    try:
        with tarfile.open(path, mode="r:*"):
            return "tar"
    except Exception:
        pass
    raise ArchiveError("无法识别压缩格式（支持 zip/tar/tar.gz/tar.bz2/tar.xz/7z/rar）")


def make_extractor(fmt: str, path: Path, options, log) -> BaseExtractor:
    """按格式创建对应后端。"""
    if fmt == "zip":
        return ZipExtractor(path, options, log)
    if fmt.startswith("tar"):
        label = {"tar": "TAR", "tar.gz": "TAR.GZ",
                 "tar.bz2": "TAR.BZ2", "tar.xz": "TAR.XZ"}[fmt]
        return TarExtractor(path, options, log, format_name=label)
    if fmt == "7z":
        return SevenZipExtractor(path, options, log)
    if fmt == "rar":
        return RarExtractor(path, options, log)
    raise ArchiveError(f"不支持的格式：{fmt}")


# ---------------------------------------------------------------------------
# 8. 主流程：列出 / 解压
# ---------------------------------------------------------------------------

@dataclass
class Options:
    output: Path | None
    name_encoding: str
    text_encoding: str
    overwrite: bool
    dry_run: bool
    verbose: bool
    quiet: bool
    password: str | None
    unsafe_paths: bool
    batch: bool = False
    recursive: bool = False
    list_only: bool = False


@dataclass
class Stats:
    extracted: int = 0
    skipped: int = 0
    failed: int = 0
    dirs: int = 0
    warnings: int = 0


def default_output_dir(archive: Path) -> Path:
    """默认解压目录：压缩包所在目录下，新建同名文件夹（去掉压缩后缀）。"""
    name = archive.name.lower()
    for suffix in sorted((s for ss in FORMAT_SUFFIXES.values() for s in ss),
                         key=len, reverse=True):
        if name.endswith(suffix):
            stem = archive.name[: -len(suffix)]
            break
    else:
        stem = archive.stem
    return archive.parent / (stem or archive.stem)


def human_size(size: int | None) -> str:
    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def print_encoding_hint(options: Options) -> None:
    """两个编码参数都保持默认值时的提示（仅 verbose）。"""
    if not options.verbose:
        return
    if options.name_encoding == "utf-8" and options.text_encoding == "utf-8":
        print("提示：当前使用默认编码 utf-8。若解压后文件名或文字是乱码，请显式指定编码：")
        print("      日文包用 --name-encoding shift_jis --text-encoding shift_jis")
        print("      中文包用 --name-encoding gbk --text-encoding gbk")


def run_list(extractor: BaseExtractor, options: Options) -> int:
    """--list：只列出内容，不解压，也不读取文件内容。"""
    encoding = options.name_encoding
    print(f"压缩包：{extractor.path}")
    print(f"格式　：{extractor.format_name}")
    print(f"文件名编码：{encoding}")
    print("-" * 72)
    print(f"{'大小':>10}  {'类型':<6} 名称")
    print("-" * 72)
    count = 0
    bad = 0
    for member in extractor.iter_members():
        count += 1
        if member.name is None:
            bad += 1
            print(f"{'':>10}  {'?':<6} 【警告】{member.name_error}", file=sys.stderr)
            continue
        kind = "目录" if member.is_dir else "文件"
        print(f"{human_size(member.size):>10}  {kind:<6} {member.name}")
    print("-" * 72)
    print(f"共 {count} 个条目" + (f"，其中 {bad} 个文件名无法用 {encoding} 解码" if bad else ""))
    return EXIT_OK


def run_extract(extractor: BaseExtractor, options: Options, dest: Path,
                header: bool = True) -> int:
    """解压主循环：文件名按 --name-encoding 解码，文本按 --text-encoding 解码为 UTF-8。"""
    stats = Stats()
    mode_text = "模拟运行" if options.dry_run else "解压"
    if header:
        print(f"压缩包：{extractor.path}")
        print(f"格式　：{extractor.format_name}")
        print(f"文件名编码：{options.name_encoding}　"
              f"文本编码：{options.text_encoding} → 统一写出 UTF-8")
        print(f"目标　：{dest}" + ("　（--dry-run 不会写入磁盘）" if options.dry_run else ""))
        print("-" * 72)

    if not options.dry_run:
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise UnpackError(f"没有权限创建目录：{dest}（{exc}）") from exc
        except OSError as exc:
            raise UnpackError(f"创建目录失败：{dest}（{exc}）") from exc

    pre_existing = existing_names(dest) if not options.overwrite else set()
    written: set[str] = set()  # 本次运行中已写出的文件，用于发现包内重名/大小写变体

    for member in extractor.iter_members():
        # --- 文件名解码失败：跳过并告警，不中断 ---
        if member.name is None:
            stats.failed += 1
            stats.warnings += 1
            print(f"【警告】跳过该文件：{member.name_error}", file=sys.stderr)
            continue

        rel = sanitize_member_path(member.name)
        if rel is None:
            stats.failed += 1
            print(f"【错误】跳过不安全的路径：{member.name!r}（含 .. 或绝对路径）",
                  file=sys.stderr)
            continue

        target = safe_join(dest, rel, check_escape=not options.unsafe_paths)
        if target is None:
            stats.failed += 1
            print(f"【错误】跳过越界路径：{member.name!r}", file=sys.stderr)
            continue

        # --- 目录：已存在则跳过，不报错 ---
        if member.is_dir:
            if target.is_dir():
                stats.skipped += 1
                if options.verbose:
                    print(f"目录已存在，跳过: {rel}")
                continue
            stats.dirs += 1
            if options.verbose:
                print(f"正在创建目录: {rel}")
            if not options.dry_run:
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    stats.failed += 1
                    print(f"【错误】创建目录失败: {rel}（{exc}）", file=sys.stderr)
            continue

        # --- 已存在则默认跳过 ---
        if not options.overwrite and (
            rel.lower() in pre_existing or rel.lower() in written or target.exists()
        ):
            stats.skipped += 1
            print(f"跳过已存在: {rel}")
            continue

        # --- 读取内容 ---
        try:
            data = extractor.read_member(member)
        except PasswordError as exc:
            stats.failed += 1
            print(f"【错误】{exc}", file=sys.stderr)
            continue
        except RuntimeError as exc:
            stats.failed += 1
            if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                print("【错误】该压缩包已加密，请用 --password 提供密码", file=sys.stderr)
            else:
                print(f"【错误】读取失败: {rel}（{exc}）", file=sys.stderr)
            continue
        except UnpackError as exc:
            stats.failed += 1
            print(f"【错误】读取失败: {rel}（{exc}）", file=sys.stderr)
            continue
        except Exception as exc:
            stats.failed += 1
            print(f"【错误】读取失败: {rel}（{type(exc).__name__}: {exc}）", file=sys.stderr)
            continue

        # --- 文本：按 --text-encoding 解码后以 UTF-8 写出；二进制：原样拷贝 ---
        if not looks_like_text(data):
            payload, label = data, "二进制文件，原样拷贝"
        else:
            try:
                payload = decode_text(data, options.text_encoding)
                label = f"编码: {options.text_encoding} → UTF-8"
            except (UnicodeDecodeError, LookupError) as exc:
                # 编码不对：跳过该文件并告警，不中断整体解压
                stats.failed += 1
                stats.warnings += 1
                print(f"【警告】跳过无法解码的文件: {rel}"
                      f"（用 {options.text_encoding} 解码失败：{exc}）", file=sys.stderr)
                continue

        # --- 冲突处理：覆盖 或 自动改名 ---
        write_target = target
        if not options.overwrite:
            if rel.lower() in pre_existing or rel.lower() in written or target.exists():
                write_target = unique_path(target)
        if options.overwrite and target.exists() and not options.dry_run:
            label += "，覆盖已有文件"

        # --- 输出进度 ---
        if not options.quiet:
            print(f"{mode_text}: {rel} ({label})")

        try:
            write_file(write_target, payload, member.mode, options.dry_run)
            stats.extracted += 1
            written.add(os.path.relpath(write_target, dest).replace("\\", "/").lower())
        except PermissionError as exc:
            stats.failed += 1
            print(f"【错误】权限不足，无法写入: {write_target}（{exc}）", file=sys.stderr)
        except OSError as exc:
            stats.failed += 1
            print(f"【错误】写入失败: {write_target}（{exc}）", file=sys.stderr)

    print("-" * 72)
    verb = "将解压" if options.dry_run else "已解压"
    print(f"{verb} {stats.extracted} 个文件，跳过 {stats.skipped} 个，"
          f"失败 {stats.failed} 个，目录 {stats.dirs} 个，警告 {stats.warnings} 个")
    if options.dry_run:
        print("（--dry-run 模式：以上仅为预览，未写入任何文件）")
    return EXIT_ERROR if stats.failed else EXIT_OK


# ---------------------------------------------------------------------------
# 9. 批量处理（--batch / --recursive）
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    archive: Path
    status: str            # ok / failed / skipped
    detail: str = ""


def is_archive_file(path: Path) -> bool:
    """按扩展名（或魔数兜底）判断是否是可处理的压缩包。"""
    name = path.name.lower()
    if any(name.endswith(sfx) for suffixes in FORMAT_SUFFIXES.values() for sfx in suffixes):
        return True
    if name.endswith((".gz", ".bz2", ".xz")):
        return True
    return sniff_format(path) is not None


def scan_archives(directory: Path, recursive: bool) -> list[Path]:
    """扫描目录下的压缩包；--recursive 时递归子目录（路径拼接全部走 os.path.join）。"""
    found: list[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames.sort()  # 稳定顺序，便于复现
            for filename in sorted(filenames):
                full = os.path.join(dirpath, filename)
                if os.path.isfile(full) and is_archive_file(Path(full)):
                    found.append(Path(full))
    else:
        for filename in sorted(os.listdir(directory)):
            full = os.path.join(directory, filename)
            if os.path.isfile(full) and is_archive_file(Path(full)):
                found.append(Path(full))
    return found


def run_batch(directory: Path, options: Options) -> int:
    """批量解压：每个压缩包解压到各自同名的文件夹，单个失败不影响其他。"""
    if not directory.exists():
        print(f"【错误】找不到目录 {directory}", file=sys.stderr)
        return EXIT_ERROR
    if not directory.is_dir():
        print(f"【错误】{directory} 不是一个目录（--batch 需要目录）", file=sys.stderr)
        return EXIT_ERROR

    archives = scan_archives(directory, options.recursive)
    if not archives:
        scope = "及子目录" if options.recursive else ""
        print(f"【警告】目录 {directory}{scope}下没有找到任何压缩包")
        print("批量汇总：成功 0 个，失败 0 个，跳过 0 个")
        return EXIT_OK

    if not options.quiet:
        print(f"批量模式：目录 {directory}{'（含子目录）' if options.recursive else ''}")
        print(f"文件名编码：{options.name_encoding}　文本编码：{options.text_encoding}")
        print(f"共发现 {len(archives)} 个压缩包")
        print("=" * 72)

    results: list[BatchResult] = []
    for index, archive in enumerate(archives, start=1):
        # 每个压缩包解压到各自同名的文件夹
        if options.output:
            dest = Path(os.path.join(str(options.output), default_output_dir(archive).name))
        else:
            dest = default_output_dir(archive)

        # 即使 -q 也打印当前处理的包名，否则批量失败时无从定位
        print(f"[{index}/{len(archives)}] {archive}")
        # --list 只看内容，不落盘，因此不受“目标目录已存在”影响
        if (not options.list_only and dest.is_dir()
                and not options.overwrite and not options.dry_run):
            if not options.quiet:
                print(f"跳过：目标目录已存在 {dest}（如需覆盖请加 --overwrite）")
            results.append(BatchResult(archive, "skipped", f"目标目录已存在：{dest}"))
            continue

        try:
            fmt = detect_format(archive)
            with make_extractor(fmt, archive, options, print) as extractor:
                if options.list_only:
                    run_list(extractor, options)
                    results.append(BatchResult(archive, "ok", "已列出内容"))
                    continue
                rc = run_extract(extractor, options, dest, header=not options.quiet)
            if rc == EXIT_OK:
                results.append(BatchResult(archive, "ok"))
            else:
                results.append(BatchResult(archive, "failed", "存在无法处理的成员"))
        except KeyboardInterrupt:
            print("\n【警告】用户中断批量处理，剩余压缩包未处理。", file=sys.stderr)
            results.append(BatchResult(archive, "failed", "用户中断"))
            break
        except (UnpackError, zipfile.BadZipFile, tarfile.TarError) as exc:
            print(f"【错误】{archive}：{exc}", file=sys.stderr)
            results.append(BatchResult(archive, "failed", str(exc)))
        except PermissionError as exc:
            print(f"【错误】{archive}：权限不足（{exc}）", file=sys.stderr)
            results.append(BatchResult(archive, "failed", f"权限不足：{exc}"))
        except OSError as exc:
            print(f"【错误】{archive}：{exc}", file=sys.stderr)
            results.append(BatchResult(archive, "failed", str(exc)))

        if not options.quiet:
            print("-" * 72)

    # ---- 汇总报告 ----
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    print("=" * 72)
    print(f"批量汇总：成功 {ok} 个，失败 {failed} 个，跳过 {skipped} 个")
    for result in results:
        if result.status == "failed":
            print(f"  【失败】{result.archive}：{result.detail}")
        elif result.status == "skipped":
            print(f"  【跳过】{result.archive}：{result.detail}")
    return EXIT_ERROR if failed else EXIT_OK


# ---------------------------------------------------------------------------
# 10. 命令行入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    encoding_help = "可选值：" + "、".join(ENCODING_CHOICES)
    parser = argparse.ArgumentParser(
        prog="auto_unpack.py",
        description=(
            "命令行压缩包解压工具（编码由用户手动指定，不做自动猜测）\n"
            "支持格式：zip / tar / tar.gz / tar.bz2 / tar.xz / 7z / rar"
        ),
        epilog=(
            "使用示例：\n"
            "  1) 日文压缩包：文件名与文本都按 Shift-JIS 处理\n"
            "       python auto_unpack.py \"ワイト Ver1.00.zip\" "
            "--name-encoding shift_jis --text-encoding shift_jis --verbose\n"
            "\n"
            "  2) 中文老压缩包：按 GBK 处理\n"
            "       python auto_unpack.py 资料打包.zip --name-encoding gbk --text-encoding gbk\n"
            "\n"
            "  3) 现代压缩包（UTF-8）：用默认值即可\n"
            "       python auto_unpack.py 项目备份.zip\n"
            "\n"
            "  4) 批量解压目录下所有日文压缩包\n"
            "       python auto_unpack.py /data/jp --batch "
            "--name-encoding cp932 --text-encoding cp932\n"
            "\n"
            "  5) 递归处理子目录 + 覆盖已有文件 + 静默输出\n"
            "       python auto_unpack.py /data/archives --batch --recursive --overwrite -q "
            "--name-encoding gbk --text-encoding gbk\n"
            "\n"
            "  6) 只看内容 / 先模拟运行 / 指定输出目录与密码\n"
            "       python auto_unpack.py \"ワイト Ver1.00.zip\" --list --name-encoding shift_jis\n"
            "       python auto_unpack.py \"ワイト Ver1.00.zip\" --dry-run --name-encoding shift_jis\n"
            "       python auto_unpack.py 加密包.zip --password 123456 -o /data/out\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "archive", nargs="?",
        help="压缩包路径；配合 --batch 时填目录路径。示例：资料.zip 或 /data/archives")

    group_enc = parser.add_argument_group("编码设置（不做自动检测，全部手动指定）")
    group_enc.add_argument(
        "--name-encoding", metavar="ENC", default="utf-8",
        help=f"压缩包内文件名的原始编码，默认 utf-8。示例：--name-encoding shift_jis。{encoding_help}")
    group_enc.add_argument(
        "--text-encoding", metavar="ENC", default="utf-8",
        help="文本文件内容的编码，默认 utf-8；解码后统一以 UTF-8 写出。"
             f"示例：--text-encoding gbk。" + encoding_help)

    group_out = parser.add_argument_group("输出与冲突处理")
    group_out.add_argument(
        "-o", "--output", metavar="DIR",
        help="指定解压目录（默认：压缩包同目录下的同名文件夹）。示例：-o /data/out")
    group_out.add_argument(
        "--overwrite", action="store_true",
        help="覆盖已存在的同名文件（默认跳过并提示，不中断）。示例：--overwrite")
    group_out.add_argument(
        "--unsafe-paths", action="store_true",
        help="允许写出目标目录之外的路径（默认拦截 ../ 越界，谨慎使用）")

    group_mode = parser.add_argument_group("运行模式")
    group_mode.add_argument(
        "--list", action="store_true",
        help="仅列出压缩包内容，不解压。示例：--list")
    group_mode.add_argument(
        "--dry-run", action="store_true",
        help="模拟运行，只显示会解压哪些文件，不实际写入。示例：--dry-run")
    group_mode.add_argument(
        "--batch", action="store_true",
        help="批量模式：把 archive 参数当作目录，解压其中所有压缩包。示例：--batch")
    group_mode.add_argument(
        "--recursive", action="store_true",
        help="配合 --batch，递归扫描子目录中的压缩包。示例：--batch --recursive")

    group_misc = parser.add_argument_group("日志与其他")
    group_misc.add_argument(
        "--verbose", action="store_true",
        help="输出详细日志（含每个文件的编码处理结果）。示例：--verbose")
    group_misc.add_argument(
        "-q", "--quiet", action="store_true",
        help="静默模式，只输出最终汇总。示例：-q")
    group_misc.add_argument(
        "--password", metavar="PWD",
        help="加密压缩包的密码（zip/7z/rar）。示例：--password 123456")
    group_misc.add_argument(
        "-V", "--version", action="version", version="auto_unpack.py 3.0")
    return parser


def validate_encoding(value: str, option: str) -> str:
    """校验用户传入的编码名，给出清晰的中文错误提示。"""
    if value not in ENCODING_CHOICES:
        raise UnpackError(
            f"{option} 不支持编码 {value!r}；可选值：{'、'.join(ENCODING_CHOICES)}")
    try:
        codecs.lookup(value)
    except LookupError as exc:  # 兜底：环境缺少该编码
        raise UnpackError(f"当前 Python 环境不支持编码 {value!r}：{exc}") from exc
    return value


def build_options(args) -> Options:
    """把命令行参数装配成 Options。"""
    return Options(
        output=Path(args.output).expanduser() if args.output else None,
        name_encoding=validate_encoding(args.name_encoding, "--name-encoding"),
        text_encoding=validate_encoding(args.text_encoding, "--text-encoding"),
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        verbose=args.verbose,
        quiet=args.quiet,
        password=args.password,
        unsafe_paths=args.unsafe_paths,
        batch=args.batch,
        recursive=args.recursive,
        list_only=args.list,
    )


def main(argv=None) -> int:
    setup_console_encoding()  # Windows 终端也能正常显示中日韩文件名
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.archive:
        parser.print_help()
        return EXIT_ERROR

    try:
        options = build_options(args)
    except UnpackError as exc:
        print(f"【错误】{exc}", file=sys.stderr)
        return EXIT_ERROR

    # ---- 参数组合校验 ----
    if args.recursive and not args.batch:
        print("【错误】--recursive 必须配合 --batch 使用（示例：--batch --recursive）",
              file=sys.stderr)
        return EXIT_ERROR

    target = Path(os.path.expanduser(args.archive))

    try:
        # ---- 批量模式 ----
        if options.batch:
            return run_batch(target, options)

        # ---- 单文件模式 ----
        if not target.exists():
            print(f"【错误】找不到文件 {target}", file=sys.stderr)
            return EXIT_ERROR
        if target.is_dir():
            print(f"【错误】{target} 是一个目录，批量解压请加 --batch", file=sys.stderr)
            return EXIT_ERROR
        if not os.access(target, os.R_OK):
            print(f"【错误】没有读取权限 {target}", file=sys.stderr)
            return EXIT_ERROR

        print_encoding_hint(options)
        fmt = detect_format(target)
        with make_extractor(fmt, target, options, print) as extractor:
            if args.list:
                return run_list(extractor, options)
            dest = options.output if options.output else default_output_dir(target)
            return run_extract(extractor, options, dest)

    # ---- 各类异常 -> 统一格式的中文提示 ----
    except KeyboardInterrupt:
        print("\n【错误】已中断，未完成的文件可能不完整。", file=sys.stderr)
        return EXIT_INTERRUPTED
    except MissingDependency as exc:
        print(f"【错误】依赖缺失：{exc}", file=sys.stderr)
        return EXIT_ERROR
    except PasswordError as exc:
        print(f"【错误】加密压缩包：{exc}", file=sys.stderr)
        return EXIT_ERROR
    except ArchiveError as exc:
        print(f"【错误】压缩包错误：{exc}", file=sys.stderr)
        return EXIT_ERROR
    except zipfile.BadZipFile as exc:
        print(f"【错误】zip 文件已损坏或不是有效的 zip（{exc}）", file=sys.stderr)
        return EXIT_ERROR
    except tarfile.TarError as exc:
        print(f"【错误】tar 文件已损坏（{exc}）", file=sys.stderr)
        return EXIT_ERROR
    except UnpackError as exc:
        print(f"【错误】{exc}", file=sys.stderr)
        return EXIT_ERROR
    except PermissionError as exc:
        print(f"【错误】权限不足：{exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"【错误】系统错误：{exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
