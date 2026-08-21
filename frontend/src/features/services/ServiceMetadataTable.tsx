export type ServiceMetadataRow = { key: string; value: string };

export function ServiceMetadataTable({ rows }: { rows: ServiceMetadataRow[] }) {
  const visibleRows = rows.length ? rows : [{ key: "metadata", value: "No complete database row has been loaded yet." }];
  return (
    <div className="news-full-metadata-wrap">
      <table className="news-full-metadata-table">
        <thead><tr><th>Field</th><th>Value</th></tr></thead>
        <tbody>
          {visibleRows.map((row) => (
            <tr key={row.key}>
              <td><code>{row.key}</code></td>
              <td><pre>{row.value}</pre></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
