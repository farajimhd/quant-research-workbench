import { displayName, formatCompactNumber } from "../../app/format";
import { numericMetric, stringMetric, uniqueStringSample } from "./metrics";

export function secReadableTextRows(rows: Record<string, unknown>[]) {
  return [...rows].sort((left, right) => {
    const leftPrimary = /primary|main|document/.test(stringMetric(left, ["text_kind", "kind"]).toLowerCase()) ? 1 : 0;
    const rightPrimary = /primary|main|document/.test(stringMetric(right, ["text_kind", "kind"]).toLowerCase()) ? 1 : 0;
    return rightPrimary - leftPrimary || secTextCharCount(right) - secTextCharCount(left);
  })
    .map((row, index) => {
      const value = secTextValue(row);
      return {
        archiveMember: stringMetric(row, ["source_archive_member"]),
        blocks: secReadableTextBlocks(value),
        charCount: secTextCharCount(row) || value.length,
        documentId: stringMetric(row, ["document_id", "filing_document_id"]),
        label: displayName(stringMetric(row, ["text_kind", "kind", "source_kind"]) || `Text part ${index + 1}`),
        sha256: stringMetric(row, ["text_sha256"]),
      };
    })
    .filter((row) => row.blocks.length > 0);
}

export function secReadableDocumentRows(documentRows: Record<string, unknown>[], textRows: Record<string, unknown>[]) {
  const textRowsByDocumentId = new Map<string, Record<string, unknown>[]>();
  for (const textRow of textRows) {
    const documentId = stringMetric(textRow, ["document_id", "filing_document_id"]);
    if (!documentId) continue;
    textRowsByDocumentId.set(documentId, [...(textRowsByDocumentId.get(documentId) ?? []), textRow]);
  }

  return [...documentRows]
    .sort((left, right) => numericMetric(left, ["sequence_number", "sequence"]) - numericMetric(right, ["sequence_number", "sequence"]))
    .map((row, index) => {
      const documentId = stringMetric(row, ["document_id", "filing_document_id"]);
      const linkedTextRows = textRowsByDocumentId.get(documentId) ?? [];
      const sequence = numericMetric(row, ["sequence_number", "sequence"]);
      const documentName = stringMetric(row, ["document_name", "name", "filename"]);
      const description = stringMetric(row, ["description"]);
      const documentType = stringMetric(row, ["document_type", "type"]);
      const documentRole = stringMetric(row, ["document_role", "role"]);
      const extractionStatus = stringMetric(row, ["extraction_status"]);
      const hasNormalizedText = numericMetric(row, ["has_normalized_text"]) > 0 || linkedTextRows.length > 0;
      const textCharCount = linkedTextRows.reduce((total, item) => total + secTextCharCount(item), 0);
      const documentUrl = stringMetric(row, ["document_url", "url"]);
      const textKinds = uniqueStringSample(linkedTextRows.map((item) => stringMetric(item, ["text_kind", "kind"])), 8);
      const badges = uniqueStringSample([documentRole, documentType, stringMetric(row, ["content_format"]), stringMetric(row, ["file_extension"])], 5).map(displayName);
      const facts = [
        { label: "Document ID", value: documentId || "-" },
        { label: "Filing ID", value: stringMetric(row, ["filing_id"]) || "-" },
        { label: "Sequence", value: sequence ? formatCompactNumber(sequence) : "-" },
        { label: "Document name", value: documentName || "-" },
        { label: "Document type", value: documentType || "-" },
        { label: "Document role", value: documentRole || "-" },
        { label: "Content format", value: stringMetric(row, ["content_format"]) || "-" },
        { label: "MIME type", value: stringMetric(row, ["mime_type"]) || "-" },
        { label: "File extension", value: stringMetric(row, ["file_extension"]) || "-" },
        { label: "Byte size", value: formatByteCount(numericMetric(row, ["byte_size"])) },
        { label: "Payload chars", value: formatCompactNumber(numericMetric(row, ["payload_char_count"])) },
        { label: "Has normalized text", value: hasNormalizedText ? "Yes" : "No" },
        { label: "Linked text kinds", value: textKinds.length ? textKinds.map(displayName).join(", ") : "-" },
        { label: "Linked text chars", value: textCharCount ? `${formatCompactNumber(textCharCount)} chars` : "-" },
        { label: "Extraction status", value: extractionStatus || "-" },
        { label: "Extraction error", value: stringMetric(row, ["extraction_error"]) || "-", wide: true },
        { label: "Normalizer", value: stringMetric(row, ["normalizer_version"]) || "-" },
        { label: "Source archive date", value: stringMetric(row, ["source_archive_date"]) || "-" },
        { label: "Source archive member", value: stringMetric(row, ["source_archive_member"]) || "-", wide: true },
        { label: "Source archive path", value: stringMetric(row, ["source_archive_path"]) || "-", wide: true },
        { label: "Document URL", value: documentUrl || "-", wide: true },
        { label: "Content SHA256", value: stringMetric(row, ["content_sha256"]) || "-", wide: true },
        { label: "Text SHA256", value: stringMetric(row, ["text_sha256"]) || "-", wide: true },
        { label: "Source run", value: stringMetric(row, ["source_run_id"]) || "-", wide: true },
        { label: "Inserted", value: stringMetric(row, ["inserted_at"]) || "-" },
      ];
      return {
        badges: badges.length ? badges : ["Document"], description, documentUrl, facts,
        key: documentId || `${documentName}-${index}`, linkedTextRows: linkedTextRows.length,
        sequenceLabel: sequence ? formatCompactNumber(sequence) : formatCompactNumber(index + 1),
        textStatusClass: hasNormalizedText ? "has-text" : extractionStatus.toLowerCase().includes("skip") || extractionStatus.toLowerCase().includes("error") ? "no-text warn" : "no-text",
        textStatusLabel: hasNormalizedText ? "Text linked" : extractionStatus || "No text",
        title: documentName || description || `Document ${index + 1}`,
      };
    });
}

export function secTextCharCount(row: Record<string, unknown>) {
  return numericMetric(row, ["text_char_count", "char_count", "text_chars", "content_chars"]) || secTextValue(row).length;
}

export function secTextMetadataRow(row: Record<string, unknown>) {
  const metadata = { ...row };
  if ("text" in metadata) {
    const text = stringMetric(metadata, ["text"]);
    metadata.text_preview = text.length > 400 ? `${text.slice(0, 400)}...` : text;
    delete metadata.text;
  }
  return metadata;
}

function secTextValue(row: Record<string, unknown>) {
  return stringMetric(row, ["text", "clean_text", "normalized_text", "body_text", "content", "text_preview"]);
}

function secReadableTextBlocks(value: string) {
  const normalized = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/[ \t]+\n/g, "\n").replace(/\n{4,}/g, "\n\n\n").trim();
  if (!normalized) return [];
  const blocks = normalized.split(/\n{2,}/).map((block) => block.replace(/[ \t]{2,}/g, " ").trim()).filter(Boolean);
  return blocks.length ? blocks : [normalized];
}

function formatByteCount(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "-";
  if (value < 1024) return `${formatCompactNumber(value)} B`;
  if (value < 1024 * 1024) return `${formatCompactNumber(value / 1024)} KiB`;
  if (value < 1024 * 1024 * 1024) return `${formatCompactNumber(value / (1024 * 1024))} MiB`;
  return `${formatCompactNumber(value / (1024 * 1024 * 1024))} GiB`;
}
