export function fmtMW(v: number | null | undefined, decimals = 0): string {
  if (v == null) return '—';
  return v.toLocaleString('en-GB', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtPct(v: number | null | undefined, decimals = 1): string {
  if (v == null) return '—';
  return `${v.toFixed(decimals)}%`;
}

export function fmtMWh(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${fmtMW(v)} MWh`;
}

export function fmtDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z');
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function fmtShortDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z');
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

export function fmtHour(h: string): string {
  return h.slice(0, 5);
}

export function confidenceColor(tier: string): string {
  switch (tier.toLowerCase()) {
    case 'high': return 'var(--color-success)';
    case 'medium': return 'var(--color-warning)';
    case 'low': return 'var(--color-error)';
    default: return 'var(--color-text-muted)';
  }
}

export function downloadCSV(rows: Record<string, unknown>[], filename: string) {
  if (!rows.length) return;
  const headers = Object.keys(rows[0]);
  const csv = [
    headers.join(','),
    ...rows.map(r => headers.map(h => JSON.stringify(r[h] ?? '')).join(',')),
  ].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
