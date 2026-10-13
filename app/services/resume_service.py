from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from pypdf import PdfReader

from app.services.errors import ServiceError


def inspect_resume(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if not content:
        raise ServiceError("简历文件为空。", 400)
    try:
        if extension == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise ValueError("invalid PDF header")
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise ServiceError("请先解除 PDF 密码保护后再上传。", 400)
            if not reader.pages or len(reader.pages) > 100:
                raise ServiceError("请上传 1–100 页的简历。", 400)
            return "\n".join(page.extract_text() or "" for page in reader.pages)[:60000]
        if extension == ".docx":
            with ZipFile(BytesIO(content)) as archive:
                if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
                    raise ServiceError("DOCX 解压后过大，请精简文件。", 400)
                xml = archive.read("word/document.xml")
                if len(xml) > 5 * 1024 * 1024:
                    raise ServiceError("DOCX 内容过大，请精简文件。", 400)
            tree = ElementTree.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            return "\n".join(
                "".join(node.itertext()) for node in tree.findall(".//w:p", ns)
            )[:60000]
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("简历无法读取，请确认文件是有效的 PDF 或 DOCX。", 400) from exc
    raise ServiceError("Phase 1 仅支持 PDF 和 DOCX，请将 DOC 转存为 DOCX。", 400)
