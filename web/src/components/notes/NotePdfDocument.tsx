import type { JSONContent } from "@tiptap/core";
import {
  Document,
  Font,
  Link,
  Page,
  StyleSheet,
  Text,
  View,
  pdf,
} from "@react-pdf/renderer";
import type { ReactNode } from "react";

import {
  resolvePdfColor,
  spreadsheetRows,
  type NotePdfCitation,
  type NotePdfSnapshot,
  type SpreadsheetData,
} from "@/lib/note-pdf";
import { getActiveSpreadsheetSheet } from "@/lib/spreadsheet-workbook";

export type NotePdfFontSources = {
  sansRegular: string;
  sansBold: string;
  serifRegular: string;
  serifBold: string;
};

export const BROWSER_NOTE_PDF_FONTS: NotePdfFontSources = {
  sansRegular: "/fonts/pdf/NotoSansTC-Regular.otf",
  sansBold: "/fonts/pdf/NotoSansTC-Bold.otf",
  serifRegular: "/fonts/pdf/NotoSerifTC-Regular.otf",
  serifBold: "/fonts/pdf/NotoSerifTC-Bold.otf",
};

const registeredFontSets = new Set<string>();

function registerFonts(sources: NotePdfFontSources) {
  const cacheKey = Object.values(sources).join("|");
  if (registeredFontSets.has(cacheKey)) return;

  Font.register({
    family: "NoteSansTC",
    fonts: [
      { src: sources.sansRegular, fontWeight: 400 },
      { src: sources.sansRegular, fontWeight: 400, fontStyle: "italic" },
      { src: sources.sansBold, fontWeight: 700 },
      { src: sources.sansBold, fontWeight: 700, fontStyle: "italic" },
    ],
  });
  Font.register({
    family: "NoteSerifTC",
    fonts: [
      { src: sources.serifRegular, fontWeight: 400 },
      { src: sources.serifRegular, fontWeight: 400, fontStyle: "italic" },
      { src: sources.serifBold, fontWeight: 700 },
      { src: sources.serifBold, fontWeight: 700, fontStyle: "italic" },
    ],
  });
  registeredFontSets.add(cacheKey);
}

const styles = StyleSheet.create({
  page: {
    backgroundColor: "#ffffff",
    color: "#18181b",
    fontFamily: "NoteSansTC",
    fontSize: 10.5,
    lineHeight: 1.65,
    paddingBottom: 54,
    paddingHorizontal: 50,
    paddingTop: 48,
  },
  title: { fontSize: 24, fontWeight: 700, lineHeight: 1.35, marginBottom: 22 },
  paragraph: { marginBottom: 8 },
  h1: { fontSize: 20, fontWeight: 700, lineHeight: 1.4, marginBottom: 9, marginTop: 15 },
  h2: { fontSize: 17, fontWeight: 700, lineHeight: 1.4, marginBottom: 8, marginTop: 13 },
  h3: { fontSize: 14, fontWeight: 700, lineHeight: 1.45, marginBottom: 7, marginTop: 11 },
  h4: { fontSize: 12, fontWeight: 700, lineHeight: 1.5, marginBottom: 6, marginTop: 9 },
  blockquote: {
    borderLeftColor: "#a1a1aa",
    borderLeftWidth: 2,
    color: "#52525b",
    marginBottom: 9,
    paddingLeft: 11,
  },
  codeBlock: {
    backgroundColor: "#f4f4f5",
    borderRadius: 4,
    fontFamily: "Courier",
    fontSize: 8.5,
    lineHeight: 1.5,
    marginBottom: 10,
    padding: 9,
  },
  list: { marginBottom: 8 },
  listRow: { flexDirection: "row", marginBottom: 3 },
  listMarker: { flexShrink: 0, paddingRight: 5, textAlign: "right", width: 23 },
  listBody: { flexGrow: 1, flexShrink: 1 },
  table: { borderLeftColor: "#a1a1aa", borderLeftWidth: 0.6, marginBottom: 12, marginTop: 5 },
  tableRow: { flexDirection: "row" },
  tableCell: {
    borderBottomColor: "#a1a1aa",
    borderBottomWidth: 0.6,
    borderRightColor: "#a1a1aa",
    borderRightWidth: 0.6,
    flexGrow: 1,
    flexBasis: 0,
    minHeight: 22,
    paddingHorizontal: 5,
    paddingVertical: 4,
  },
  tableHeader: { backgroundColor: "#f4f4f5", fontWeight: 700 },
  spreadsheetLabel: { color: "#047857", fontSize: 8.5, fontWeight: 700, marginBottom: 4 },
  citationSection: { borderTopColor: "#d4d4d8", borderTopWidth: 1, marginTop: 22, paddingTop: 15 },
  citationHeading: { fontSize: 15, fontWeight: 700, marginBottom: 10 },
  citation: { marginBottom: 10 },
  citationLabel: { fontSize: 9.5, fontWeight: 700, marginBottom: 3 },
  citationQuote: { color: "#3f3f46", fontSize: 8.5, lineHeight: 1.55, marginBottom: 2 },
  citationLink: { color: "#2563eb", fontSize: 7.5, textDecoration: "none" },
  empty: { color: "#71717a", fontStyle: "italic" },
  footer: {
    color: "#71717a",
    fontSize: 7.5,
    left: 50,
    position: "absolute",
    right: 50,
    textAlign: "center",
    top: 808,
  },
});

function markAttrs(mark: NonNullable<JSONContent["marks"]>[number]): Record<string, unknown> {
  return (mark.attrs ?? {}) as Record<string, unknown>;
}

function inlineNode(node: JSONContent, key: string): ReactNode {
  if (node.type === "hardBreak") return "\n";
  if (node.type !== "text") return (node.content ?? []).map((child, index) => inlineNode(child, `${key}-${index}`));

  const inlineStyle: Record<string, string | number> = {};
  let monospace = false;
  for (const mark of node.marks ?? []) {
    const attrs = markAttrs(mark);
    if (mark.type === "bold") inlineStyle.fontWeight = 700;
    if (mark.type === "italic") {
      inlineStyle.fontStyle = "italic";
      inlineStyle.transform = "skewX(-9deg)";
    }
    if (mark.type === "underline") inlineStyle.textDecoration = "underline";
    if (mark.type === "strike") {
      inlineStyle.textDecoration = inlineStyle.textDecoration === "underline" ? "underline line-through" : "line-through";
    }
    if (mark.type === "highlight") inlineStyle.backgroundColor = resolvePdfColor(attrs.color, "#fef08a");
    if (mark.type === "textStyle") {
      if (attrs.color) inlineStyle.color = resolvePdfColor(attrs.color, "#18181b");
      if (typeof attrs.fontSize === "string") {
        const size = Number.parseFloat(attrs.fontSize);
        if (Number.isFinite(size)) inlineStyle.fontSize = Math.min(32, Math.max(7, size)) * 0.75;
      }
      if (typeof attrs.fontFamily === "string") {
        if (attrs.fontFamily.includes("Serif") || attrs.fontFamily.includes("Songti") || attrs.fontFamily.includes("PMingLiU")) {
          inlineStyle.fontFamily = "NoteSerifTC";
        } else if (attrs.fontFamily.includes("mono")) {
          monospace = true;
        }
      }
    }
  }
  const text = node.text ?? "";
  if (!monospace) return <Text key={key} style={inlineStyle}>{text}</Text>;

  // Courier preserves the editor's monospace setting for Latin text while the
  // embedded CJK font supplies full-width Traditional Chinese glyphs.
  return (
    <Text key={key} style={inlineStyle}>
      {text.split(/([\u0000-\u007f]+)/).filter(Boolean).map((part, index) => (
        <Text key={`${key}-mono-${index}`} style={{ fontFamily: /^[\u0000-\u007f]+$/.test(part) ? "Courier" : "NoteSansTC" }}>
          {part}
        </Text>
      ))}
    </Text>
  );
}

function inlineContent(node: JSONContent, key: string): ReactNode[] {
  return (node.content ?? []).map((child, index) => inlineNode(child, `${key}-${index}`));
}

function plainText(node: JSONContent): string {
  if (node.type === "text") return node.text ?? "";
  if (node.type === "hardBreak") return "\n";
  return (node.content ?? []).map(plainText).join("");
}

function renderTable(node: JSONContent, key: string): ReactNode {
  return (
    <View key={key} style={styles.table}>
      {(node.content ?? []).map((row, rowIndex) => (
        <View key={`${key}-row-${rowIndex}`} style={styles.tableRow} wrap={false}>
          {(row.content ?? []).map((cell, cellIndex) => (
            <View
              key={`${key}-cell-${rowIndex}-${cellIndex}`}
              style={[styles.tableCell, cell.type === "tableHeader" ? styles.tableHeader : {}]}
            >
              {(cell.content ?? []).map((child, index) => renderNode(child, `${key}-cell-content-${rowIndex}-${cellIndex}-${index}`, 0))}
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}

function renderSpreadsheet(node: JSONContent, key: string): ReactNode {
  const data = node.attrs?.data as SpreadsheetData | undefined;
  if (!data) return <Text key={key} style={styles.empty}>[無法讀取 Spreadsheet]</Text>;
  const sheet = getActiveSpreadsheetSheet(data);
  const rows = spreadsheetRows(data);
  const fontSize = sheet.cols > 8 ? 5.5 : sheet.cols > 5 ? 6.5 : 8;

  return (
    <View key={key} minPresenceAhead={60}>
      <Text style={styles.spreadsheetLabel}>Spreadsheet · {sheet.name} · {sheet.rows} 列 × {sheet.cols} 欄</Text>
      <View style={styles.table}>
        {rows.map((row, rowIndex) => (
          <View key={`${key}-row-${rowIndex}`} style={styles.tableRow} wrap={false}>
            {row.map((value, cellIndex) => (
              <Text
                key={`${key}-cell-${rowIndex}-${cellIndex}`}
                style={[styles.tableCell, { fontSize }, rowIndex === 0 ? styles.tableHeader : {}]}
              >
                {value || " "}
              </Text>
            ))}
          </View>
        ))}
      </View>
    </View>
  );
}

function renderList(node: JSONContent, key: string, depth: number): ReactNode {
  const ordered = node.type === "orderedList";
  const taskList = node.type === "taskList";
  const start = typeof node.attrs?.start === "number" ? node.attrs.start : 1;

  return (
    <View key={key} style={[styles.list, { marginLeft: depth * 12 }]}>
      {(node.content ?? []).map((item, index) => {
        const firstParagraph = item.content?.find((child) => child.type === "paragraph");
        const nested = (item.content ?? []).filter((child) => child !== firstParagraph);
        const marker = taskList ? (item.attrs?.checked ? "[x]" : "[ ]") : ordered ? `${start + index}.` : "•";
        return (
          <View key={`${key}-item-${index}`}>
            <View style={styles.listRow} wrap={false}>
              <Text style={styles.listMarker}>{marker}</Text>
              <Text style={styles.listBody}>{firstParagraph ? inlineContent(firstParagraph, `${key}-text-${index}`) : " "}</Text>
            </View>
            {nested.map((child, nestedIndex) => renderNode(child, `${key}-nested-${index}-${nestedIndex}`, depth + 1))}
          </View>
        );
      })}
    </View>
  );
}

function renderNode(node: JSONContent, key: string, depth: number): ReactNode {
  switch (node.type) {
    case "paragraph": {
      const children = inlineContent(node, key);
      return <Text key={key} style={styles.paragraph}>{children.length ? children : " "}</Text>;
    }
    case "heading": {
      const level = Math.min(4, Math.max(1, Number(node.attrs?.level) || 1)) as 1 | 2 | 3 | 4;
      return <Text key={key} style={styles[`h${level}`]} minPresenceAhead={32}>{inlineContent(node, key)}</Text>;
    }
    case "bulletList":
    case "orderedList":
    case "taskList":
      return renderList(node, key, depth);
    case "blockquote":
      return <View key={key} style={styles.blockquote}>{(node.content ?? []).map((child, index) => renderNode(child, `${key}-${index}`, depth))}</View>;
    case "codeBlock":
      return <Text key={key} style={styles.codeBlock}>{plainText(node)}</Text>;
    case "horizontalRule":
      return <View key={key} style={{ borderTopColor: "#d4d4d8", borderTopWidth: 0.8, marginBottom: 10, marginTop: 7 }} />;
    case "table":
      return renderTable(node, key);
    case "spreadsheetBlock":
      return renderSpreadsheet(node, key);
    default:
      return (node.content ?? []).map((child, index) => renderNode(child, `${key}-${index}`, depth));
  }
}

function citationLocator(citation: NotePdfCitation): string | null {
  const locator = citation.locator_json;
  if (!locator) return null;
  const candidates = [locator.page, locator.page_number, locator.timestamp, locator.section, locator.source_type];
  const value = candidates.find((candidate) => typeof candidate === "string" || typeof candidate === "number");
  return value === undefined ? null : String(value);
}

function CitationSection({ citations }: { citations: NotePdfCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <View style={styles.citationSection} minPresenceAhead={80}>
      <Text style={styles.citationHeading}>引用資訊</Text>
      {citations.map((citation, index) => {
        const locator = citationLocator(citation);
        return (
          <View key={citation.id} style={styles.citation} wrap={false}>
            <Text style={styles.citationLabel}>
              [{index + 1}] {citation.source_label || "未命名來源"}{locator ? ` · ${locator}` : ""}
            </Text>
            {citation.supporting_quote ? <Text style={styles.citationQuote}>{citation.supporting_quote}</Text> : null}
            {citation.source_url ? <Link src={citation.source_url} style={styles.citationLink}>{citation.source_url}</Link> : null}
          </View>
        );
      })}
    </View>
  );
}

export function NotePdfDocument({ snapshot }: { snapshot: NotePdfSnapshot }) {
  const body = snapshot.content.content ?? [];
  return (
    <Document title={snapshot.title} author="Personal Brain Notes" subject="Personal note export">
      <Page size="A4" style={styles.page} wrap>
        <Text style={styles.title} minPresenceAhead={50}>{snapshot.title}</Text>
        {body.length > 0
          ? body.map((node, index) => renderNode(node, `node-${index}`, 0))
          : <Text style={styles.empty}>（筆記內容空白）</Text>}
        <CitationSection citations={snapshot.citations} />
        <Text
          fixed
          style={styles.footer}
          render={({ pageNumber }) => `— ${pageNumber} —`}
        />
      </Page>
    </Document>
  );
}

export async function generateNotePdfBlob(
  snapshot: NotePdfSnapshot,
  fontSources: NotePdfFontSources = BROWSER_NOTE_PDF_FONTS,
): Promise<Blob> {
  registerFonts(fontSources);
  return pdf(<NotePdfDocument snapshot={snapshot} />).toBlob();
}
