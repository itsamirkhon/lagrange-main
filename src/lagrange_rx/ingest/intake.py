"""Content-addressed, bounded storage of original client documents."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import io
import zipfile

MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200
MAX_ARCHIVE_EXPANDED = 75 * 1024 * 1024
TYPES = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.csv': 'text/csv', '.eml': 'message/rfc822', '.txt': 'text/plain'}

class IntakeError(ValueError):
    pass

@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    filename: str
    media_type: str
    path: str
    duplicate: bool = False

class IntakeStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def add(self, name, content):
        suffix = Path(name).suffix.lower()
        if suffix not in TYPES:
            raise IntakeError('Unsupported or dangerous file type')
        if len(content) > MAX_FILE_SIZE:
            raise IntakeError('File exceeds 25 MB')
        if suffix == '.pdf':
            try:
                import fitz
                with fitz.open(stream=content, filetype='pdf') as pdf:
                    if len(pdf) > 50:
                        raise IntakeError('PDF exceeds 50 pages')
            except IntakeError:
                raise
            except Exception as exc:
                raise IntakeError('Invalid PDF') from exc
        if suffix == '.xlsx':
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    if any('vbaproject' in n.lower() for n in archive.namelist()):
                        raise IntakeError('Macros are forbidden')
                    if sum(i.file_size for i in archive.infolist()) > MAX_FILE_SIZE * 10:
                        raise IntakeError('Expanded spreadsheet is too large')
            except zipfile.BadZipFile as exc:
                raise IntakeError('Invalid spreadsheet') from exc
        digest = sha256(content).hexdigest()
        dest = self.root / digest
        duplicate = dest.exists()
        if not duplicate:
            try:
                with dest.open('xb') as file:
                    file.write(content)
            except FileExistsError:
                duplicate = True
        return SourceDocument(digest, Path(name).name, TYPES[suffix], str(dest), duplicate)

    def add_archive(self, content):
        if len(content) > MAX_FILE_SIZE:
            raise IntakeError('Archive exceeds 25 MB')
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if len(members) > MAX_ARCHIVE_MEMBERS or sum(item.file_size for item in members) > MAX_ARCHIVE_EXPANDED:
                    raise IntakeError('Expanded archive is too large')
                for item in members:
                    path = Path(item.filename.replace('\\', '/'))
                    if path.is_absolute() or '..' in path.parts or path.suffix.lower() not in TYPES or item.file_size > MAX_FILE_SIZE:
                        raise IntakeError('Unsafe archive member')
                return [self.add(item.filename, archive.read(item)) for item in members]
        except zipfile.BadZipFile as exc:
            raise IntakeError('Invalid archive') from exc
