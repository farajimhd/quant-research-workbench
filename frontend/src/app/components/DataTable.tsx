import { formatCell } from "../format";

type DataTableProps = {
  rows: Record<string, unknown>[];
  columns?: string[];
  empty?: string;
};

export function DataTable({ rows, columns, empty = "No rows." }: DataTableProps) {
  const resolvedColumns = columns ?? Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  const sortedRows = [...rows].sort((left, right) => {
    const primary = resolvedColumns[0];
    return String(left[primary] ?? "").localeCompare(String(right[primary] ?? ""), undefined, { numeric: true });
  });
  if (!rows.length) return <div className="empty-state">{empty}</div>;
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {resolvedColumns.map((column) => (
              <th key={column}>{formatHeader(column)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, index) => (
            <tr key={index}>
              {resolvedColumns.map((column) => (
                <td key={column} title={String(row[column] ?? "")}>
                  {formatCell(column, row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatHeader(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}
