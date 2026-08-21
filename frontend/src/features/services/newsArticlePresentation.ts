export type NewsArticleBlock =
  | { items: string[]; kind: "list"; text?: never }
  | { items?: never; kind: "lead" | "paragraph" | "subhead"; text: string };

export function cleanNewsArticleText(value: string) {
  const normalizedMarkup = value
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<\/\s*p\s*>/gi, "\n\n")
    .replace(/<\s*li\s*>/gi, "\n- ")
    .replace(/<\/\s*li\s*>/gi, "\n")
    .replace(/<[^>]+>/g, " ");
  return decodeNewsHtmlEntities(normalizedMarkup)
    .replace(/\r\n/g, "\n")
    .replace(/\t/g, " ")
    .replace(/[ \u00a0]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function newsArticleBlocks(value: string, title = "", teaser = ""): NewsArticleBlock[] {
  const cleaned = dedupeNewsBodySentences(stripNewsBodyLeadNoise(cleanNewsArticleText(value), title, teaser));
  if (!cleaned) return [{ kind: "paragraph", text: "No readable body text was returned for this news row." }];
  const paragraphBlocks = cleaned.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);
  const blocks = paragraphBlocks.length > 1 ? paragraphBlocks : splitLongNewsParagraph(cleaned);
  return blocks.slice(0, 48).map((block, index) => {
    const listItems = newsListItems(block);
    if (listItems.length >= 2) return { items: listItems, kind: "list" };
    if (index === 0 && block.length > 80) return { kind: "lead", text: block };
    if (isNewsSubhead(block)) return { kind: "subhead", text: block.replace(/:$/, "") };
    return { kind: "paragraph", text: block };
  });
}

function stripNewsBodyLeadNoise(value: string, title: string, teaser: string) {
  let stripped = value.trim();
  for (const candidate of [title, teaser].map(cleanNewsArticleText).filter((item) => item.length > 8).sort((a, b) => b.length - a.length)) {
    stripped = stripped.replace(new RegExp(`^${escapeRegExp(candidate)}[\\s:.-]*`, "i"), "").trim();
  }
  return stripped;
}

function dedupeNewsBodySentences(value: string) {
  return value
    .split(/\n{2,}/)
    .map((paragraph) => {
      const seen = new Set<string>();
      const sentences = paragraph.split(/(?<=[.!?])\s+(?=[A-Z0-9"'])/).map((item) => item.trim()).filter(Boolean);
      return sentences.filter((sentence) => {
        const key = sentence.toLowerCase().replace(/[^a-z0-9]+/g, "");
        if (key.length < 48) return true;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).join(" ");
    })
    .join("\n\n")
    .trim();
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function splitLongNewsParagraph(value: string) {
  const sentences = value.split(/(?<=[.!?])\s+(?=[A-Z0-9"'])/).map((item) => item.trim()).filter(Boolean);
  if (sentences.length <= 1) return [value];
  const chunks: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if (current && `${current} ${sentence}`.length > 720) {
      chunks.push(current);
      current = sentence;
    } else {
      current = current ? `${current} ${sentence}` : sentence;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

function newsListItems(value: string) {
  const lines = value.split("\n").map((line) => line.trim()).filter(Boolean);
  const items = lines.map((line) => line.match(/^[-*]\s+(.+)$/)?.[1]?.trim() ?? "").filter(Boolean);
  return items.length === lines.length ? items : [];
}

function isNewsSubhead(value: string) {
  const trimmed = value.trim();
  if (trimmed.length > 96) return false;
  if (trimmed.endsWith(":")) return true;
  const letters = trimmed.replace(/[^A-Za-z]/g, "");
  if (letters.length < 6) return false;
  return letters.replace(/[^A-Z]/g, "").length / letters.length > 0.72;
}

function decodeNewsHtmlEntities(value: string) {
  if (!value.includes("&")) return value;
  const named: Record<string, string> = {
    amp: "&", apos: "'", gt: ">", ldquo: "\"", lsquo: "'", lt: "<", mdash: "-",
    nbsp: " ", ndash: "-", quot: "\"", rdquo: "\"", rsquo: "'",
  };
  return value
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCharCode(Number.parseInt(code, 16)))
    .replace(/&([a-z]+);/gi, (match, name) => named[String(name).toLowerCase()] ?? match);
}
