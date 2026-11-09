"""Input pre-processing: file extraction, truncation, PII redaction and prompt-injection heuristics."""
import hashlib
import io
import re

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")


class InputError(ValueError):
    """User-facing input problem (message is safe to show in the UI)."""


# ---------- file extraction ----------

def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if len(data) > MAX_UPLOAD_BYTES:
        raise InputError(f"File '{filename}' is larger than 5 MB.")
    if not name.endswith(ALLOWED_EXTENSIONS):
        raise InputError(f"Unsupported file type for '{filename}'. Use PDF, DOCX or TXT.")
    try:
        if name.endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        elif name.endswith(".docx"):
            from docx import Document

            doc = Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            text = data.decode("utf-8", errors="replace")
    except InputError:
        raise
    except Exception as exc:  # corrupt / encrypted files
        raise InputError(f"Could not read '{filename}'. Is the file corrupt or password-protected?") from exc
    text = normalize(text)
    if not text:
        raise InputError(f"No text found in '{filename}'. Scanned/image PDFs are not supported; paste the text instead.")
    return text


# ---------- normalisation & truncation ----------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize(text: str) -> str:
    text = _CONTROL_CHARS.sub("", text.replace("\r\n", "\n"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Cut to max_chars, preferring a paragraph/line boundary so we don't split mid-sentence."""
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    boundary = max(cut.rfind("\n\n"), cut.rfind("\n"))
    if boundary > max_chars * 0.8:
        cut = cut[:boundary]
    return cut.rstrip() + "\n[...truncated...]", True


# ---------- PII redaction ----------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{8,}\d(?![\w])")
_URL_PROFILE = re.compile(r"https?://(?:www\.)?(?:linkedin|facebook|instagram|twitter|x)\.com/\S+", re.I)


def redact_pii(text: str, candidate_name: str | None = None) -> str:
    """Strip direct identifiers the model does not need to judge skills."""
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub(lambda m: "[PHONE]" if sum(c.isdigit() for c in m.group()) >= 10 else m.group(), text)
    text = _URL_PROFILE.sub("[PROFILE_URL]", text)
    if candidate_name:
        parts = [candidate_name.strip()] + [p for p in candidate_name.split() if len(p) >= 3]
        for part in sorted(set(parts), key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(part)}\b", "[CANDIDATE]", text, flags=re.I)
    return text


# ---------- prompt-injection heuristics ----------

INJECTION_PATTERNS: dict[str, re.Pattern] = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|system)\b.{0,40}"
        r"\b(instructions?|prompts?|rules?|directions?)\b",
        re.I | re.S,
    ),
    "score_manipulation": re.compile(
        r"\b(give|assign|rate|score|award|output|return)\b.{0,40}\b(100|hundred|perfect|maximum|max)\b"
        r"|\bscore\s*[:=]\s*100\b",
        re.I | re.S,
    ),
    "role_hijack": re.compile(
        r"\byou are (now|no longer)\b|\bact as\b|\bnew instructions?\b|\bsystem prompt\b|\bdeveloper mode\b", re.I
    ),
    "delimiter_escape": re.compile(r"</?\s*(resume|job_description|system|instructions?)\s*>", re.I),
}


def detect_injection(text: str) -> list[str]:
    return [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(text)]


def neutralize_delimiters(text: str) -> str:
    """Stop user text from closing our XML-style data tags."""
    return re.sub(r"<(/?)\s*(resume|job_description)\s*>", r"(\1\2)", text, flags=re.I)


# ---------- misc ----------

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def excerpt(text: str, length: int = 120) -> str:
    first = " ".join(text.split())
    return first if len(first) <= length else first[: length - 1].rstrip() + "…"
