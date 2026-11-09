"""``ListenerStatusStore`` kept in a small JSON file in the data volume."""

import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from healer.domain.errors import StatusError
from healer.domain.listener import ListenerStatus


class JsonListenerStatus:
    """The file is replaced whole on every save, so a reader never sees half of it."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, status: ListenerStatus) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as file:
                    file.write(status.model_dump_json())
                os.replace(temporary, self._path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise StatusError(f"Cannot write the listener status at {self._path}: {exc}") from exc

    def load(self) -> ListenerStatus | None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StatusError(f"Cannot read the listener status at {self._path}: {exc}") from exc
        try:
            return ListenerStatus.model_validate_json(text)
        except ValidationError as exc:
            raise StatusError(f"The listener status at {self._path} is corrupt") from exc
