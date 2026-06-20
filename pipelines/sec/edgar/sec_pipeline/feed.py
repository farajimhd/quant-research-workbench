from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib import parse
from xml.etree import ElementTree

from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient


ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


@dataclass(frozen=True, slots=True)
class SecFeedItem:
    accession_number: str
    accession_number_compact: str
    cik: str
    form_type: str
    title: str
    filing_detail_url: str
    primary_document_url: str
    updated_at_utc: datetime | None


class SecCurrentFeedClient:
    def __init__(self, *, feed_url: str, http: SecHttpClient) -> None:
        self.feed_url = feed_url
        self.http = http

    def fetch(self) -> list[SecFeedItem]:
        response = self.http.get(self.feed_url)
        root = ElementTree.fromstring(response.body)
        items: list[SecFeedItem] = []
        for entry in root.findall("a:entry", ATOM_NS):
            title = text(entry.find("a:title", ATOM_NS))
            updated = parse_atom_datetime(text(entry.find("a:updated", ATOM_NS)))
            categories = [node.attrib for node in entry.findall("a:category", ATOM_NS)]
            link = ""
            for link_node in entry.findall("a:link", ATOM_NS):
                href = link_node.attrib.get("href", "")
                if href:
                    link = href
                    break
            accession = accession_from_text(title + " " + link)
            cik = cik_from_text(link)
            form_type = form_from_categories(categories) or form_from_link(link) or form_from_title(title)
            if not accession or not cik:
                continue
            compact = accession.replace("-", "")
            filing_detail_url = link
            primary_document_url = ""
            items.append(
                SecFeedItem(
                    accession_number=accession,
                    accession_number_compact=compact,
                    cik=cik.zfill(10),
                    form_type=form_type,
                    title=title,
                    filing_detail_url=filing_detail_url,
                    primary_document_url=primary_document_url,
                    updated_at_utc=updated,
                )
            )
        return items


def accession_text_url(cik: str, accession_number: str) -> str:
    cik_int = str(int(cik))
    compact = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{compact}/{accession_number}.txt"


def text(node: ElementTree.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_atom_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def accession_from_text(value: str) -> str:
    match = re.search(r"\b\d{10}-\d{2}-\d{6}\b", value)
    return match.group(0) if match else ""


def cik_from_text(value: str) -> str:
    match = re.search(r"/data/(\d+)/", value)
    return match.group(1) if match else ""


def form_from_title(value: str) -> str:
    prefix = re.match(r"\s*([A-Z0-9][A-Z0-9/\-. ]{0,24}?)\s+-\s+", value, flags=re.IGNORECASE)
    if prefix:
        return " ".join(prefix.group(1).strip().upper().split())
    matches = [match.group(1).strip().upper() for match in re.finditer(r"\(([^()]+)\)", value)]
    for item in reversed(matches):
        if not item.isdigit():
            return item
    return matches[-1] if matches else ""


def form_from_categories(categories: list[dict[str, str]]) -> str:
    for category in categories:
        label = category.get("label", "").strip().lower()
        term = category.get("term", "").strip().upper()
        if label == "form type" and term:
            return term
    return ""


def form_from_link(value: str) -> str:
    query = parse.parse_qs(parse.urlparse(value).query)
    form_values = query.get("type") or query.get("form_type")
    return form_values[0].strip().upper() if form_values else ""
