"""Fixed Bot identity image storage and validation."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import time

from .errors import IdentityRequiredError
from .models import ImageReference, ReferenceRole


def _detect_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class IdentityStore:
    def __init__(self, data_dir: Path, filename: str, max_bytes: int) -> None:
        self._data_dir = Path(data_dir)
        self._filename = str(filename)
        self._max_bytes = int(max_bytes)

    @property
    def identity_dir(self) -> Path:
        return self._data_dir / "identity"

    @property
    def path(self) -> Path:
        candidate = Path(self._filename)
        if candidate.is_absolute() or candidate.name != self._filename or ".." in candidate.parts:
            raise IdentityRequiredError("Bot 身份图配置无效，请检查 reference_filename。")
        return self.identity_dir / candidate.name

    def load(self) -> ImageReference:
        try:
            path = self.path
            if not path.is_file():
                raise IdentityRequiredError()
            size = path.stat().st_size
            if size <= 0 or size > self._max_bytes:
                raise IdentityRequiredError("Bot 身份图大小无效或超过限制。")
            data = path.read_bytes()
        except IdentityRequiredError:
            raise
        except OSError as exc:
            raise IdentityRequiredError("Bot 身份图无法读取，请检查文件权限。") from exc

        mime_type = _detect_mime(data)
        if mime_type is None:
            raise IdentityRequiredError("Bot 身份图必须是 PNG、JPEG 或 WebP。")

        return ImageReference(
            image_id=sha256(data).hexdigest(),
            data=data,
            mime_type=mime_type,
            source="identity:bot",
            role=ReferenceRole.IDENTITY,
            timestamp=time(),
        )

    def status(self) -> dict[str, object]:
        try:
            reference = self.load()
        except IdentityRequiredError as exc:
            return {"configured": False, "message": exc.public_message}
        return {
            "configured": True,
            "image_id": reference.image_id,
            "mime_type": reference.mime_type,
        }

