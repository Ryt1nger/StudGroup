"""Strict PNG multipart transport for RANEPA's two-image RuCaptcha rotate task.

The pinned 2captcha-python async HTTP client reads file bytes and lets httpx
assign the generic filename "upload" and application/octet-stream type.
The provider returned ERROR_ZERO_CAPTCHA_FILESIZE on that request. Keep the
SDK's task creation, polling and response parsing, but make the two uploaded
image parts unambiguous without patching process-global SDK behavior.

No page data, credentials, browser cookies or full screenshots are sent.
"""

import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from twocaptcha.async_api import AsyncApiClient
from twocaptcha.exceptions.api import ApiException, NetworkException
from twocaptcha.exceptions.solver import ValidationException

LOGGER = logging.getLogger(__name__)
_MAX_PNG_BYTES = 600 * 1024
_MIN_PNG_BYTES = 100


def _validated_png_part(path: str, filename: str) -> tuple[str, bytes, str]:
    """Validate both actual PNG bytes and dimensions before any HTTP request."""
    try:
        target = Path(path)
        if target.suffix.lower() != ".png":
            raise ValueError("image extension is not png")
        payload = target.read_bytes()
        if not _MIN_PNG_BYTES <= len(payload) <= _MAX_PNG_BYTES:
            raise ValueError("image byte length outside permitted range")
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG signature")
        with Image.open(BytesIO(payload)) as image:
            if image.format != "PNG":
                raise ValueError("decoded image is not PNG")
            if not (
                100 <= image.width <= 600
                and 100 <= image.height <= 600
                and image.width == image.height
            ):
                raise ValueError("image dimensions outside permitted range")
            image.load()
            if image.getbands()[-1] == "A" and image.getchannel("A").getextrema()[1] == 0:
                raise ValueError("image is fully transparent")
        return filename, payload, "image/png"
    except (OSError, ValueError, TypeError, Image.DecompressionBombError) as exc:
        raise ValidationException("invalid rotate PNG image") from None


class TypedRotateApiClient(AsyncApiClient):
    """Keep SDK polling, but POST two explicitly named image/png parts."""

    def __init__(self, post_url: str = "rucaptcha.com", *, client_factory=None):
        super().__init__(post_url=post_url)
        self._client_factory = client_factory or httpx.AsyncClient

    async def in_(self, files=None, **kwargs):
        """Submit exactly one two-image rotate task; never retry or log a key."""
        if (
            self.post_url != "rucaptcha.com"
            or kwargs.get("method") != "rotatecaptcha"
            or not isinstance(files, dict)
            or list(files) != ["file_1", "file_2"]
        ):
            raise ValidationException("unexpected rotate upload shape")

        parts = {
            "file_1": _validated_png_part(files["file_1"], "left.png"),
            "file_2": _validated_png_part(files["file_2"], "right.png"),
        }
        LOGGER.warning("RANEPA_CAPTCHA_STAGE rotate_upload_png_parts_verified")
        try:
            async with self._client_factory(timeout=25, follow_redirects=False) as http:
                response = await http.post(
                    "https://rucaptcha.com/in.php",
                    data=kwargs,
                    files=parts,
                )
        except httpx.RequestError:
            raise NetworkException("RuCaptcha rotate upload failed") from None
        if response.status_code != 200:
            raise NetworkException("RuCaptcha rotate HTTP error")
        LOGGER.warning("RANEPA_CAPTCHA_STAGE rotate_upload_http_response_received")
        try:
            reply = response.content.decode("utf-8")
        except UnicodeDecodeError:
            raise ApiException("RuCaptcha rotate returned non-UTF8 data") from None
        if "ERROR" in reply:
            # Caller only logs a fixed allow-listed error category, never
            # the raw response or API credentials.
            raise ApiException(reply)
        return reply
