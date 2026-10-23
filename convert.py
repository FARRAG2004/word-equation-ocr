"""CLI fallback: python convert.py input.docx output.docx"""
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

sys.path.insert(0, str(Path(__file__).parent / "server"))
import core  # noqa: E402


def paragraphs(doc):
    yield from doc.paragraphs
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def main(src, dst):
    doc = Document(src)
    counts = {"converted": 0, "skipped": 0, "flagged": 0}
    for i, p in enumerate(paragraphs(doc)):
        for run in list(p._p.iter(qn("w:r"))):
            blip = run.find(f".//{qn('wp:inline')}//{qn('a:blip')}")
            if blip is None:
                continue
            part = doc.part.related_parts[blip.get(qn("r:embed"))]
            r = core.process(part.blob)
            counts[r["status"]] += 1
            if r["status"] == "flagged":
                print(f"FLAGGED para {i} conf={r['confidence']}: {r['latex']}")
            if r["status"] == "converted":
                run.addprevious(etree.fromstring(r["omml"]))
                run.getparent().remove(run)
    doc.save(dst)
    print(counts)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
