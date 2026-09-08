import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = process.cwd();
const dataPath = `${root}/research/seo/vpn-20260908/vpn_keywords_dataset.json`;
const outputDir = `${root}/outputs/vpn-seo-keywords-20260908`;
const previewDir = "/tmp/kuaishou-seo-workbook/previews";
const dataset = JSON.parse(await fs.readFile(dataPath, "utf8"));

const workbook = Workbook.create();
const fontFamily = "Arial";
const colors = {
  navy: "#18324A",
  blue: "#2F75B5",
  lightBlue: "#D9EAF7",
  lightGray: "#F3F5F7",
  border: "#D9E0E6",
  text: "#20262D",
  muted: "#66717D",
  green: "#DDF2E5",
  greenText: "#176B3A",
  amber: "#FFF0CC",
  amberText: "#8A5700",
  red: "#FCE1E3",
  redText: "#A12732",
};

function writeTitle(sheet, title, context, width) {
  sheet.showGridLines = false;
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = {
    name: fontFamily,
    size: 15,
    bold: true,
    color: colors.navy,
  };
  sheet.getRange(`A2:${width}2`).format.borders = {
    bottom: { style: "thin", color: colors.blue },
  };
  sheet.getRange("A3").values = [[context]];
  sheet.getRange("A3").format.font = {
    name: fontFamily,
    size: 10,
    italic: true,
    color: colors.muted,
  };
}

function styleHeader(range) {
  range.format = {
    fill: colors.navy,
    font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
  };
  range.format.rowHeight = 30;
}

function styleBody(range) {
  range.format.font = { name: fontFamily, size: 10, color: colors.text };
  range.format.verticalAlignment = "center";
  range.format.rowHeight = 20;
}

// ---------------------------------------------------------------------------
// Refined keyword library
// ---------------------------------------------------------------------------

const refinedSheet = workbook.worksheets.add("提纯词库");
writeTitle(
  refinedSheet,
  "VPN关键词及长尾词（提纯）",
  `数据时间：${dataset.generated_at}。来源：Google Web、Google/YouTube、Bing中文、百度联想、快手视频样本词根、5118公开索引。`,
  "P",
);

const refinedHeaders = [
  "关键词",
  "主题",
  "搜索意图",
  "优先级",
  "规则相关度",
  "长尾词",
  "平台敏感",
  "建议快手采集",
  "来源数",
  "来源",
  "关联种子",
  "种子类别",
  "最佳来源排名",
  "原始出现次数",
  "原始变体",
  "备注",
];
const refinedRows = dataset.refined.map((row) => [
  row.keyword,
  row.topic,
  row.intent,
  row.priority,
  row.rule_relevance,
  row.is_long_tail,
  row.platform_sensitive,
  row.recommended_for_kuaishou,
  row.source_count,
  row.sources,
  row.seed_terms,
  row.seed_categories,
  row.best_source_rank,
  row.raw_occurrences,
  row.variants,
  row.exclude_reason || "",
]);
const refinedHeaderRow = 7;
const refinedStartRow = refinedHeaderRow + 1;
const refinedEndRow = refinedHeaderRow + refinedRows.length;

const recommendedCount = dataset.refined.filter((row) => row.recommended_for_kuaishou).length;
const acceleratorCount = dataset.refined.filter((row) => row.keyword.includes("加速器")).length;
const longTailCount = dataset.refined.filter((row) => row.is_long_tail).length;
refinedSheet.getRange("A4:H4").values = [[
  "提纯词数",
  refinedRows.length,
  "建议执行",
  recommendedCount,
  "加速器词",
  acceleratorCount,
  "长尾词",
  longTailCount,
]];
refinedSheet.getRange("A4:H4").format = {
  fill: colors.lightBlue,
  font: { name: fontFamily, size: 10, bold: true, color: colors.navy },
  verticalAlignment: "center",
  borders: { preset: "outside", style: "thin", color: colors.border },
};
refinedSheet.getRange("A4:H4").format.rowHeight = 24;
refinedSheet.getRange("A5").values = [[
  "规则相关度用于去重和筛选，不代表搜索量。加速器相关词全部保留；可通过“建议快手采集”和“优先级”筛选执行范围。",
]];
refinedSheet.getRange("A5").format.font = {
  name: fontFamily,
  size: 10,
  italic: true,
  color: colors.muted,
};
refinedSheet.getRange(`A${refinedHeaderRow}:P${refinedHeaderRow}`).values = [refinedHeaders];
refinedSheet.getRangeByIndexes(
  refinedStartRow - 1,
  0,
  refinedRows.length,
  refinedHeaders.length,
).values = refinedRows;
styleHeader(refinedSheet.getRange(`A${refinedHeaderRow}:P${refinedHeaderRow}`));
styleBody(refinedSheet.getRange(`A${refinedStartRow}:P${refinedEndRow}`));
refinedSheet.getRange(`E${refinedStartRow}:E${refinedEndRow}`).setNumberFormat("0");
refinedSheet.getRange(`I${refinedStartRow}:I${refinedEndRow}`).setNumberFormat("0");
refinedSheet.getRange(`M${refinedStartRow}:N${refinedEndRow}`).setNumberFormat("0");
refinedSheet.getRange(`D${refinedStartRow}:I${refinedEndRow}`).format.horizontalAlignment = "center";
const refinedTable = refinedSheet.tables.add(
  `A${refinedHeaderRow}:P${refinedEndRow}`,
  true,
  "RefinedKeywordsTable",
);
refinedTable.style = "TableStyleMedium2";
refinedSheet.freezePanes.freezeRows(refinedHeaderRow);
refinedSheet.freezePanes.freezeColumns(1);

refinedSheet.getRange(`D${refinedStartRow}:D${refinedEndRow}`).conditionalFormats.addCustom(
  `=$D${refinedStartRow}="A"`,
  { fill: colors.green, font: { bold: true, color: colors.greenText } },
);
refinedSheet.getRange(`D${refinedStartRow}:D${refinedEndRow}`).conditionalFormats.addCustom(
  `=$D${refinedStartRow}="B"`,
  { fill: colors.amber, font: { bold: true, color: colors.amberText } },
);
refinedSheet.getRange(`G${refinedStartRow}:G${refinedEndRow}`).conditionalFormats.addCustom(
  `=$G${refinedStartRow}=TRUE`,
  { fill: colors.red, font: { color: colors.redText } },
);
refinedSheet.getRange(`H${refinedStartRow}:H${refinedEndRow}`).conditionalFormats.addCustom(
  `=$H${refinedStartRow}=TRUE`,
  { fill: colors.green, font: { color: colors.greenText } },
);

const refinedWidths = [28, 20, 16, 9, 12, 10, 12, 15, 10, 34, 38, 24, 14, 14, 38, 26];
refinedWidths.forEach((width, index) => {
  refinedSheet.getRangeByIndexes(0, index, refinedEndRow, 1).format.columnWidth = width;
});

// ---------------------------------------------------------------------------
// Raw source rows
// ---------------------------------------------------------------------------

const rawSheet = workbook.worksheets.add("原始词库");
writeTitle(
  rawSheet,
  "VPN关键词原始词库",
  "每行保留采集源、关联种子、源内排名和查询地址。相同词可能因多个来源或种子重复出现。",
  "J",
);
const rawHeaders = [
  "采集阶段",
  "来源代码",
  "来源名称",
  "来源查询地址",
  "种子词",
  "种子类别",
  "源内排名",
  "原始关键词",
  "规范词",
  "采集时间",
];
const rawRows = [];
const rawCsv = await fs.readFile(
  `${root}/research/seo/vpn-20260908/vpn_keywords_raw.csv`,
  "utf8",
);
const rawLines = rawCsv.replace(/^\uFEFF/, "").split(/\r?\n/).filter(Boolean);
function parseCsvLine(line) {
  const values = [];
  let value = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (char === '"') {
      if (quoted && line[i + 1] === '"') {
        value += '"';
        i += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === "," && !quoted) {
      values.push(value);
      value = "";
    } else {
      value += char;
    }
  }
  values.push(value);
  return values;
}
const rawColumnNames = parseCsvLine(rawLines[0]);
for (const line of rawLines.slice(1)) {
  const values = parseCsvLine(line);
  const row = Object.fromEntries(rawColumnNames.map((name, index) => [name, values[index] ?? ""]));
  rawRows.push([
    row.phase,
    row.source,
    row.source_name,
    row.source_url,
    row.seed,
    row.seed_category,
    Number(row.rank || 0),
    row.keyword,
    row.normalized,
    row.collected_at ? new Date(row.collected_at) : null,
  ]);
}
const rawHeaderRow = 5;
const rawStartRow = rawHeaderRow + 1;
const rawEndRow = rawHeaderRow + rawRows.length;
rawSheet.getRange(`A${rawHeaderRow}:J${rawHeaderRow}`).values = [rawHeaders];
rawSheet.getRangeByIndexes(rawStartRow - 1, 0, rawRows.length, rawHeaders.length).values = rawRows;
styleHeader(rawSheet.getRange(`A${rawHeaderRow}:J${rawHeaderRow}`));
styleBody(rawSheet.getRange(`A${rawStartRow}:J${rawEndRow}`));
rawSheet.getRange(`G${rawStartRow}:G${rawEndRow}`).setNumberFormat("0");
rawSheet.getRange(`J${rawStartRow}:J${rawEndRow}`).setNumberFormat("yyyy-mm-dd hh:mm");
rawSheet.getRange(`G${rawStartRow}:G${rawEndRow}`).format.horizontalAlignment = "center";
const rawTable = rawSheet.tables.add(
  `A${rawHeaderRow}:J${rawEndRow}`,
  true,
  "RawKeywordsTable",
);
rawTable.style = "TableStyleMedium2";
rawSheet.freezePanes.freezeRows(rawHeaderRow);
rawSheet.freezePanes.freezeColumns(1);
[14, 17, 24, 52, 26, 20, 11, 34, 34, 20].forEach((width, index) => {
  rawSheet.getRangeByIndexes(0, index, rawEndRow, 1).format.columnWidth = width;
});

// ---------------------------------------------------------------------------
// Excluded unique terms
// ---------------------------------------------------------------------------

const excludedSheet = workbook.worksheets.add("剔除记录");
writeTitle(
  excludedSheet,
  "剔除关键词记录",
  "保留剔除原因以便复核规则。含“加速器”的词不会进入本表。",
  "H",
);
const excludedHeaders = [
  "关键词",
  "主题",
  "搜索意图",
  "规则相关度",
  "来源数",
  "来源",
  "关联种子",
  "剔除原因",
];
const excludedRows = dataset.excluded.map((row) => [
  row.keyword,
  row.topic,
  row.intent,
  row.rule_relevance,
  row.source_count,
  row.sources,
  row.seed_terms,
  row.exclude_reason || "相关度低于提纯阈值",
]);
const excludedHeaderRow = 5;
const excludedStartRow = excludedHeaderRow + 1;
const excludedEndRow = excludedHeaderRow + excludedRows.length;
excludedSheet.getRange(`A${excludedHeaderRow}:H${excludedHeaderRow}`).values = [excludedHeaders];
excludedSheet.getRangeByIndexes(
  excludedStartRow - 1,
  0,
  excludedRows.length,
  excludedHeaders.length,
).values = excludedRows;
styleHeader(excludedSheet.getRange(`A${excludedHeaderRow}:H${excludedHeaderRow}`));
styleBody(excludedSheet.getRange(`A${excludedStartRow}:H${excludedEndRow}`));
excludedSheet.getRange(`D${excludedStartRow}:E${excludedEndRow}`).setNumberFormat("0");
excludedSheet.getRange(`D${excludedStartRow}:E${excludedEndRow}`).format.horizontalAlignment = "center";
excludedSheet.getRange(`H${excludedStartRow}:H${excludedEndRow}`).format.fill = colors.red;
excludedSheet.getRange(`H${excludedStartRow}:H${excludedEndRow}`).format.font = {
  name: fontFamily,
  size: 10,
  color: colors.redText,
};
const excludedTable = excludedSheet.tables.add(
  `A${excludedHeaderRow}:H${excludedEndRow}`,
  true,
  "ExcludedKeywordsTable",
);
excludedTable.style = "TableStyleMedium2";
excludedSheet.freezePanes.freezeRows(excludedHeaderRow);
[34, 22, 18, 14, 10, 36, 38, 32].forEach((width, index) => {
  excludedSheet.getRangeByIndexes(0, index, excludedEndRow, 1).format.columnWidth = width;
});

workbook.recalculate();
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const inspections = {};
inspections.refined = (
  await workbook.inspect({
    kind: "table",
    range: "提纯词库!A2:P20",
    include: "values,formulas",
    tableMaxRows: 20,
    tableMaxCols: 16,
    maxChars: 12000,
  })
).ndjson;
inspections.raw = (
  await workbook.inspect({
    kind: "table",
    range: "原始词库!A2:J15",
    include: "values,formulas",
    tableMaxRows: 15,
    tableMaxCols: 10,
    maxChars: 10000,
  })
).ndjson;
inspections.excluded = (
  await workbook.inspect({
    kind: "table",
    range: "剔除记录!A2:H15",
    include: "values,formulas",
    tableMaxRows: 15,
    tableMaxCols: 8,
    maxChars: 8000,
  })
).ndjson;
inspections.errors = (
  await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: "final formula error scan",
  })
).ndjson;
await fs.writeFile(
  "/tmp/kuaishou-seo-workbook/inspection.json",
  JSON.stringify(inspections, null, 2),
);

for (const [sheetName, range] of [
  ["提纯词库", "A1:P25"],
  ["原始词库", "A1:J22"],
  ["剔除记录", "A1:H22"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(`${previewDir}/${sheetName}.png`, bytes);
}

const output = await SpreadsheetFile.exportXlsx(workbook);
const outputPath = `${outputDir}/vpn_seo_keywords_20260908.xlsx`;
await output.save(outputPath);

const savedBlob = await FileBlob.load(outputPath);
const savedWorkbook = await SpreadsheetFile.importXlsx(savedBlob);
const savedSummary = await savedWorkbook.inspect({
  kind: "table",
  range: "提纯词库!A2:H8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 8,
  maxChars: 5000,
});
const savedSheets = await savedWorkbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 3000,
});
await fs.writeFile(
  "/tmp/kuaishou-seo-workbook/saved-verification.json",
  JSON.stringify(
    { summary: savedSummary.ndjson, sheets: savedSheets.ndjson },
    null,
    2,
  ),
);
await fs.rm(`${outputPath}.inspect.ndjson`, { force: true });

console.log(
  JSON.stringify(
    {
      outputPath,
      refinedRows: refinedRows.length,
      rawRows: rawRows.length,
      excludedRows: excludedRows.length,
      recommendedRows: recommendedCount,
      acceleratorRows: acceleratorCount,
      previewDir,
    },
    null,
    2,
  ),
);
