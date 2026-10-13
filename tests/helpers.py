from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


SAMPLE_LINES = [
    "虚构验收样本，不是真实简历。",
    "姓名：演示候选人",
    "教育：网络空间安全博士",
    "研究方向：加密流量分析",
    "技能：Python、Suricata、PCAP、TLS、IAT、PyTorch",
    "项目：加密恶意流量检测与规则生成",
    "个人职责：负责 PCAP 数据分析、包长和 IAT 特征提取，以及 Suricata 规则验证。",
    "成果：在虚构测试环境验证检测规则，结果需要在面试中具体说明。",
    "论文：加密流量分析相关论文（虚构样本）",
    "实习：无",
]


def sample_docx(lines=None):
    paragraphs = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in (lines or SAMPLE_LINES))
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paragraphs}</w:body></w:document>'
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", xml)
    return output.getvalue()


def sample_pdf(encrypted=False, blank=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    if not blank:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 250 Td (Sample resume: Python, PCAP, Suricata) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("sample-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def create_session(client, duration=20):
    uploaded = client.post("/api/resumes", files={"file": ("resume.docx", sample_docx())})
    assert uploaded.status_code == 201, uploaded.text
    resume_id = uploaded.json()["id"]
    parsed = client.post(f"/api/resumes/{resume_id}/parse")
    assert parsed.status_code == 200, parsed.text
    created = client.post("/api/interviews", json={
        "resume_id": resume_id, "company": "中国银行", "position": "信息科技岗",
        "interview_type": "半结构化面试", "duration_minutes": duration,
    })
    assert created.status_code == 201, created.text
    return created.json()["id"]


def start_session(client, duration=20):
    identifier = create_session(client, duration)
    prepared = client.post(f"/api/interviews/{identifier}/prepare")
    assert prepared.status_code == 200, prepared.text
    started = client.post(f"/api/interviews/{identifier}/start")
    assert started.status_code == 200, started.text
    return identifier, started.json()
