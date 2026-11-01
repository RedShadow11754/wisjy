import { useEffect, useMemo, useState } from 'react'

type Cutoff = '02:30' | '04:30' | '07:30' | '09:30' | '11:30' | '15:30' | '18:30' | '22:30'

type ForecastSlot = {
  target_hour_utc: string
  latest_forecast_mw: number
  p10_safe_floor_mw: number
  p50_ml_forecast_mw: number
  p90_upper_bound_mw: number
  expected_accuracy_pct: number
  actual_wind_mw?: number | null
}

type ForecastResponse = {
  daily_kpis: {
    total_daily_ml_mwh: number
    total_daily_safe_firm_mwh: number
    expected_accuracy_pct: number
    expected_mae_mw: number
    realized_mae_reduction_pct?: number | null
    mean_mw_error_saved_per_hour?: number | null
  }
  hourly_slots: ForecastSlot[]
}

type CoverageResponse = {
  windfor_stream: {
    min_delivery_date: string
    max_delivery_date: string
  }
}

type SeriesKey = 'actual' | 'optimized' | 'raw'

const cutoffs: Cutoff[] = ['02:30', '04:30', '07:30', '09:30', '11:30', '15:30', '18:30', '22:30']
const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Forecast unavailable'
}

const seriesConfig: Record<SeriesKey, { label: string; color: string; dash?: string }> = {
  actual: { label: 'Actual', color: '#d29a45' },
  optimized: { label: 'Optimized', color: '#176a5b' },
  raw: { label: 'Raw prediction', color: '#78827b', dash: '6 7' },
}

function formatNumber(value: number, maximumFractionDigits = 0) {
  return new Intl.NumberFormat('en-GB', { maximumFractionDigits }).format(value)
}

function formatFixed(value: number | null | undefined, fractionDigits = 1) {
  if (value == null || Number.isNaN(value)) return '—'
  return new Intl.NumberFormat('en-GB', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value)
}

function formatDate(value: string) {
  const date = new Date(`${value}T12:00:00Z`)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date)
}

function WindMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 32 32" className="h-8 w-8">
      <path d="M4 9.5h15.5c3.2 0 4.8-4.2 2-6.1-1.9-1.3-4.2.1-4.2 2" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.2" />
      <path d="M4 15.8h21c3.6 0 4.6 4.9 1.2 6.3-2.1.9-4.1-.8-3.7-2.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.2" />
      <path d="M4 22.2h11.2" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.2" />
    </svg>
  )
}

function CalendarIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4">
      <path d="M5.2 2.7v2.6M14.8 2.7v2.6M3.3 7.1h13.4M4.4 4.3h11.2c.8 0 1.4.6 1.4 1.4v10c0 .8-.6 1.4-1.4 1.4H4.4c-.8 0-1.4-.6-1.4-1.4v-10c0-.8.6-1.4 1.4-1.4Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.4" />
    </svg>
  )
}

function ClockIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4">
      <circle cx="10" cy="10" r="7.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10 5.8v4.5l3.1 1.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.4" />
    </svg>
  )
}

function RefreshIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className={`h-4 w-4 ${spinning ? 'animate-spin' : ''}`}>
      <path d="M15.7 7.2A6.2 6.2 0 1 0 16 12M15.7 3.9v3.3h-3.3" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  )
}

function ForecastChart({
  slots,
  visible,
  selectedIndex,
  onSelect,
}: {
  slots: ForecastSlot[]
  visible: Record<SeriesKey, boolean>
  selectedIndex: number
  onSelect: (index: number) => void
}) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const width = 1120
  const height = 500
  const margin = { top: 48, right: 28, bottom: 56, left: 72 }
  const plotWidth = width - margin.left - margin.right
  const plotHeight = height - margin.top - margin.bottom
  const values = slots.flatMap((slot) => [
    slot.p10_safe_floor_mw,
    slot.p90_upper_bound_mw,
    slot.p50_ml_forecast_mw,
    slot.latest_forecast_mw,
    slot.actual_wind_mw ?? slot.p50_ml_forecast_mw,
  ])
  const minValue = Math.floor(Math.max(0, Math.min(...values) - 600) / 2000) * 2000
  const maxValue = Math.ceil((Math.max(...values) + 600) / 2000) * 2000
  const x = (index: number) => margin.left + (index / Math.max(slots.length - 1, 1)) * plotWidth
  const y = (value: number) => margin.top + ((maxValue - value) / (maxValue - minValue)) * plotHeight
  const curvePath = (getter: (slot: ForecastSlot) => number | null | undefined) => {
    const points = slots.flatMap((slot, index) => {
      const value = getter(slot)
      return value == null ? [] : [{ x: x(index), y: y(value) }]
    })
    if (points.length < 2) return ''

    return points.slice(0, -1).reduce((path, point, index) => {
      const previous = points[Math.max(0, index - 1)]
      const next = points[index + 1]
      const afterNext = points[Math.min(points.length - 1, index + 2)]
      const controlOneX = point.x + (next.x - previous.x) / 6
      const controlOneY = point.y + (next.y - previous.y) / 6
      const controlTwoX = next.x - (afterNext.x - point.x) / 6
      const controlTwoY = next.y - (afterNext.y - point.y) / 6
      return `${path} C ${controlOneX.toFixed(1)} ${controlOneY.toFixed(1)}, ${controlTwoX.toFixed(1)} ${controlTwoY.toFixed(1)}, ${next.x.toFixed(1)} ${next.y.toFixed(1)}`
    }, `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`)
  }
  const upper = slots.map((slot, index) => `${index === 0 ? 'M' : 'L'} ${x(index).toFixed(1)} ${y(slot.p90_upper_bound_mw).toFixed(1)}`).join(' ')
  const lower = [...slots]
    .reverse()
    .map((slot, reverseIndex) => {
      const index = slots.length - 1 - reverseIndex
      return `L ${x(index).toFixed(1)} ${y(slot.p10_safe_floor_mw).toFixed(1)}`
    })
    .join(' ')
  const bandPath = `${upper} ${lower} Z`
  const yTicks = Array.from({ length: 5 }, (_, index) => minValue + ((maxValue - minValue) / 4) * index)
  const safeIndex = Math.min(selectedIndex, slots.length - 1)
  const selected = slots[safeIndex]
  const selectedX = x(safeIndex)
  const displayIndex = Math.min(hoveredIndex ?? safeIndex, slots.length - 1)
  const displaySlot = slots[displayIndex]
  const displayX = x(displayIndex)
  const tooltipWidth = 250
  const tooltipHeight = 198
  const tooltipX = displayX > width - margin.right - tooltipWidth - 18 ? displayX - tooltipWidth - 18 : displayX + 18
  const tooltipY = 66
  const optimizedPath = curvePath((slot) => slot.p50_ml_forecast_mw)
  const optimizedAreaPath = `${optimizedPath} L ${x(slots.length - 1).toFixed(1)} ${(height - margin.bottom).toFixed(1)} L ${x(0).toFixed(1)} ${(height - margin.bottom).toFixed(1)} Z`
  const differenceFromRaw = displaySlot.p50_ml_forecast_mw - displaySlot.latest_forecast_mw
  const dayParts = [
    { label: 'Overnight', start: 0, end: 6, fill: '#f3f6f3' },
    { label: 'Morning', start: 6, end: 12, fill: '#faf8ef' },
    { label: 'Afternoon', start: 12, end: 18, fill: '#f6f8f3' },
    { label: 'Evening', start: 18, end: 24, fill: '#f3f4f1' },
  ]

  return (
    <div className="chart-wrap" onMouseLeave={() => setHoveredIndex(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="24-hour wind outlook in megawatts" className="chart">
        <defs>
          <linearGradient id="confidenceBand" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#78ad9d" stopOpacity="0.23" />
            <stop offset="48%" stopColor="#a9c9bd" stopOpacity="0.12" />
            <stop offset="100%" stopColor="#78ad9d" stopOpacity="0.025" />
          </linearGradient>
          <linearGradient id="optimizedArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2b8170" stopOpacity="0.18" />
            <stop offset="55%" stopColor="#6aa897" stopOpacity="0.06" />
            <stop offset="100%" stopColor="#9fc4b9" stopOpacity="0" />
          </linearGradient>
          <filter id="lineGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="4" stdDeviation="4" floodColor="#176a5b" floodOpacity="0.22" />
          </filter>
          <filter id="tooltipShadow" x="-30%" y="-30%" width="160%" height="170%">
            <feDropShadow dx="0" dy="10" stdDeviation="12" floodColor="#17211d" floodOpacity="0.2" />
          </filter>
        </defs>

        {dayParts.map((part) => {
          const startX = margin.left + (part.start / 24) * plotWidth
          const endX = margin.left + (part.end / 24) * plotWidth
          return (
            <g key={part.label}>
              <rect x={startX} y={margin.top} width={endX - startX} height={plotHeight} fill={part.fill} opacity="0.74" />
              <text x={(startX + endX) / 2} y={margin.top - 19} textAnchor="middle" className="chart-period">{part.label}</text>
              {part.start > 0 && <line x1={startX} x2={startX} y1={margin.top} y2={height - margin.bottom} stroke="#dfe3dc" strokeDasharray="3 7" />}
            </g>
          )
        })}

        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} stroke="#d7ddd7" strokeDasharray="2 7" />
            <text x={margin.left - 16} y={y(tick) + 4} textAnchor="end" className="chart-axis">{formatNumber(tick / 1000, 1)}k</text>
          </g>
        ))}
        <text x={margin.left - 16} y={margin.top - 12} textAnchor="end" className="chart-unit">MW</text>

        {slots.map((slot, index) =>
          index % 3 === 0 || index === slots.length - 1 ? (
            <text key={slot.target_hour_utc} x={x(index)} y={height - 18} textAnchor="middle" className="chart-axis">
              {slot.target_hour_utc}
            </text>
          ) : null,
        )}

        <path d={bandPath} fill="url(#confidenceBand)" />
        {visible.optimized && <path d={optimizedAreaPath} fill="url(#optimizedArea)" />}
        <path d={curvePath((slot) => slot.p10_safe_floor_mw)} fill="none" stroke="#73a294" strokeDasharray="2 5" strokeOpacity="0.58" strokeWidth="1.2" />
        <path d={curvePath((slot) => slot.p90_upper_bound_mw)} fill="none" stroke="#73a294" strokeDasharray="2 5" strokeOpacity="0.58" strokeWidth="1.2" />

        {visible.raw && <path d={curvePath((slot) => slot.latest_forecast_mw)} className="chart-series" fill="none" stroke={seriesConfig.raw.color} strokeDasharray={seriesConfig.raw.dash} strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.2" />}
        {visible.actual && <path d={curvePath((slot) => slot.actual_wind_mw)} className="chart-series" fill="none" stroke={seriesConfig.actual.color} strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.7" />}
        {visible.optimized && <path d={optimizedPath} className="chart-series" fill="none" filter="url(#lineGlow)" stroke={seriesConfig.optimized.color} strokeLinecap="round" strokeLinejoin="round" strokeWidth="4.2" />}

        {visible.optimized && slots.map((slot, index) => (
          <circle key={`optimized-${slot.target_hour_utc}`} cx={x(index)} cy={y(slot.p50_ml_forecast_mw)} r="2.25" fill="#fcfbf7" stroke="#176a5b" strokeOpacity="0.72" strokeWidth="1.5" />
        ))}

        <line x1={selectedX} x2={selectedX} y1={margin.top} y2={height - margin.bottom} stroke="#1e2925" strokeDasharray="3 6" strokeOpacity="0.14" />
        {visible.optimized && <circle cx={selectedX} cy={y(selected.p50_ml_forecast_mw)} r="6" fill="#fcfbf7" stroke="#176a5b" strokeWidth="3" />}
        {visible.actual && selected.actual_wind_mw != null && <circle cx={selectedX} cy={y(selected.actual_wind_mw)} r="4.5" fill="#fcfbf7" stroke="#d29a45" strokeWidth="2.5" />}
        {visible.raw && <circle cx={selectedX} cy={y(selected.latest_forecast_mw)} r="4.5" fill="#fcfbf7" stroke="#78827b" strokeWidth="2.5" />}

        {slots.map((slot, index) => (
          <rect
            key={`hit-${slot.target_hour_utc}`}
            x={Math.max(margin.left, x(index) - plotWidth / slots.length / 2)}
            y={margin.top}
            width={plotWidth / slots.length}
            height={plotHeight}
            fill="transparent"
            className="cursor-crosshair"
            onMouseEnter={() => setHoveredIndex(index)}
            onClick={() => onSelect(index)}
          />
        ))}

        {hoveredIndex != null && (
          <g className="chart-tooltip" pointerEvents="none">
            <rect x={displayX - 18} y={margin.top} width="36" height={plotHeight} rx="12" fill="#176a5b" opacity="0.045" />
            <line x1={displayX} x2={displayX} y1={margin.top} y2={height - margin.bottom} stroke="#176a5b" strokeDasharray="3 5" strokeOpacity="0.55" />
            <circle cx={displayX} cy={y(displaySlot.p50_ml_forecast_mw)} r="11" fill="#176a5b" opacity="0.12" className="chart-pulse" />
            <circle cx={displayX} cy={y(displaySlot.p50_ml_forecast_mw)} r="6.5" fill="#fcfbf7" stroke="#176a5b" strokeWidth="3" />

            <g filter="url(#tooltipShadow)">
              <rect x={tooltipX} y={tooltipY} width={tooltipWidth} height={tooltipHeight} rx="15" fill="#1e2925" />
              <rect x={tooltipX + 1} y={tooltipY + 1} width={tooltipWidth - 2} height={tooltipHeight - 2} rx="14" fill="none" stroke="#ffffff" strokeOpacity="0.08" />
            </g>
            <text x={tooltipX + 18} y={tooltipY + 25} className="tooltip-kicker">DELIVERY HOUR</text>
            <text x={tooltipX + 18} y={tooltipY + 48} className="tooltip-time">{displaySlot.target_hour_utc} UTC</text>
            <rect x={tooltipX + 173} y={tooltipY + 18} width="59" height="23" rx="11.5" fill="#345047" />
            <text x={tooltipX + 202.5} y={tooltipY + 33} textAnchor="middle" className="tooltip-confidence">{formatNumber(displaySlot.expected_accuracy_pct)}%</text>

            <line x1={tooltipX + 18} x2={tooltipX + tooltipWidth - 18} y1={tooltipY + 62} y2={tooltipY + 62} stroke="#ffffff" strokeOpacity="0.1" />
            <circle cx={tooltipX + 22} cy={tooltipY + 83} r="4" fill="#67b49f" />
            <text x={tooltipX + 34} y={tooltipY + 87} className="tooltip-label">Optimized prediction</text>
            <text x={tooltipX + tooltipWidth - 18} y={tooltipY + 87} textAnchor="end" className="tooltip-value">{formatNumber(displaySlot.p50_ml_forecast_mw)} MW</text>

            <circle cx={tooltipX + 22} cy={tooltipY + 107} r="4" fill="#d29a45" />
            <text x={tooltipX + 34} y={tooltipY + 111} className="tooltip-label">Actual wind</text>
            <text x={tooltipX + tooltipWidth - 18} y={tooltipY + 111} textAnchor="end" className="tooltip-value">{displaySlot.actual_wind_mw == null ? 'Pending' : `${formatNumber(displaySlot.actual_wind_mw)} MW`}</text>

            <circle cx={tooltipX + 22} cy={tooltipY + 131} r="4" fill="#929c96" />
            <text x={tooltipX + 34} y={tooltipY + 135} className="tooltip-label">Raw prediction</text>
            <text x={tooltipX + tooltipWidth - 18} y={tooltipY + 135} textAnchor="end" className="tooltip-value">{formatNumber(displaySlot.latest_forecast_mw)} MW</text>

            <line x1={tooltipX + 18} x2={tooltipX + tooltipWidth - 18} y1={tooltipY + 149} y2={tooltipY + 149} stroke="#ffffff" strokeOpacity="0.1" />
            <text x={tooltipX + 18} y={tooltipY + 170} className="tooltip-foot-label">Planning range</text>
            <text x={tooltipX + tooltipWidth - 18} y={tooltipY + 170} textAnchor="end" className="tooltip-foot-value">{formatNumber(displaySlot.p10_safe_floor_mw)}–{formatNumber(displaySlot.p90_upper_bound_mw)} MW</text>
            <text x={tooltipX + 18} y={tooltipY + 188} className="tooltip-delta">
              {differenceFromRaw <= 0 ? `${formatNumber(Math.abs(differenceFromRaw))} MW below raw` : `${formatNumber(differenceFromRaw)} MW above raw`}
            </text>
          </g>
        )}
      </svg>
    </div>
  )
}

export default function App() {
  const [deliveryDate, setDeliveryDate] = useState('2026-01-15')
  const [cutoff, setCutoff] = useState<Cutoff>('15:30')
  const [data, setData] = useState<ForecastResponse | null>(null)
  const [coverage, setCoverage] = useState<{ min: string; max: string } | null>(null)
  const [selectedIndex, setSelectedIndex] = useState(15)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [processTimeMs, setProcessTimeMs] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const [visible, setVisible] = useState<Record<SeriesKey, boolean>>({
    actual: false,
    optimized: true,
    raw: false,
  })

  useEffect(() => {
    const controller = new AbortController()

    fetch(`${API_BASE}/api/v1/ingestion/coverage`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error('Coverage unavailable'))))
      .then((payload: CoverageResponse) => {
        setCoverage({
          min: payload.windfor_stream.min_delivery_date,
          max: payload.windfor_stream.max_delivery_date,
        })
      })
      .catch(() => undefined)

    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!coverage) return
    if (deliveryDate < coverage.min || deliveryDate > coverage.max) {
      setDeliveryDate(coverage.max)
    }
  }, [coverage, deliveryDate])

  useEffect(() => {
    const controller = new AbortController()

    async function loadForecast() {
      setLoading(true)
      setError(null)
      try {
        const params = new URLSearchParams({
          delivery_date: deliveryDate,
          cutoff_time: cutoff,
          auto_ingest_if_missing: 'true',
        })
        const response = await fetch(`${API_BASE}/api/v1/forecast/day-ahead?${params}`, { signal: controller.signal })
        if (!response.ok) {
          const body = await response.json().catch(() => ({}))
          throw new Error(body.detail || body.error || `Forecast unavailable (${response.status})`)
        }
        const nextData = (await response.json()) as ForecastResponse
        setData(nextData)
        setProcessTimeMs(Number.parseFloat(response.headers.get('X-Process-Time-Ms') || '0'))
        setSelectedIndex((current) => Math.min(current, Math.max(0, nextData.hourly_slots.length - 1)))
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === 'AbortError') return
        setError(errorMessage(caught))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    loadForecast()
    return () => controller.abort()
  }, [deliveryDate, cutoff, refreshKey])

  const slots = data?.hourly_slots ?? []
  const kpis = data?.daily_kpis ?? null
  const selected = slots[selectedIndex] ?? slots[0] ?? null
  const rawDifference = selected ? selected.latest_forecast_mw - selected.p50_ml_forecast_mw : 0
  const actualDifference =
    selected && selected.actual_wind_mw != null
      ? selected.p50_ml_forecast_mw - selected.actual_wind_mw
      : null
  const hasActuals = useMemo(() => slots.some((slot) => slot.actual_wind_mw != null), [slots])
  const rangeSpan = selected ? Math.max(1, selected.p90_upper_bound_mw - selected.p10_safe_floor_mw) : 1
  const rangeMarkerPct = selected
    ? Math.min(
        100,
        Math.max(0, 22 + ((selected.p50_ml_forecast_mw - selected.p10_safe_floor_mw) / rangeSpan) * 56),
      )
    : 49
  const minDate = coverage?.min ?? '2025-08-01'
  const maxDate = coverage?.max ?? '2026-01-31'
  const statusLabel = loading && !data ? 'Loading outlook' : error ? 'API offline' : 'Live outlook'

  useEffect(() => {
    if (!hasActuals) {
      setVisible((current) => (current.actual ? { ...current, actual: false } : current))
    }
  }, [hasActuals])

  function toggleSeries(key: SeriesKey) {
    if (key === 'actual' && !hasActuals) return
    setVisible((current) => ({ ...current, [key]: !current[key] }))
  }

  return (
    <div className="min-h-dvh bg-[#f4f3ee] text-[#202824]">
      <header className="border-b border-white/10 bg-[#1e2925] text-white">
        <div className="mx-auto flex h-[72px] max-w-[1600px] items-center justify-between px-5 md:px-8 xl:px-12">
          <div className="flex items-center gap-3.5">
            <span className="text-[#c8ded5]"><WindMark /></span>
            <span className="font-display text-[27px] tracking-[-0.02em]">Windward</span>
            <span className="hidden h-5 w-px bg-white/20 sm:block" />
            <span className="hidden text-[10px] font-semibold uppercase tracking-[0.16em] text-[#b6c2bb] sm:block">UK Wind Procurement</span>
          </div>
          <div className="flex items-center gap-5">
            <div className="hidden items-center gap-2 text-xs text-[#c6d0ca] md:flex">
              <span className={`h-1.5 w-1.5 rounded-full ${error || (loading && !data) ? 'bg-[#d2a35e]' : 'bg-[#70bb93]'}`} />
              {statusLabel}
            </div>
            <button className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-xs font-semibold hover:bg-white/15" aria-label="Open account menu">RK</button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-6 md:px-8 md:py-8 xl:px-12">
        {error && (
          <div className="mb-5 flex flex-col gap-2 rounded-xl border border-[#e7c4c0] bg-[#fbeceb] px-4 py-3 text-[13px] text-[#8a3a32] sm:flex-row sm:items-center sm:justify-between">
            <span>
              Backend error: {error}
              {data ? ' Showing the last successful forecast.' : ''}
            </span>
            <button
              onClick={() => setRefreshKey((key) => key + 1)}
              className="self-start rounded-md border border-[#d8b3ae] px-3 py-1 text-[12px] font-semibold hover:bg-white/60 sm:self-auto"
            >
              Retry
            </button>
          </div>
        )}
        <section className="mb-6 flex flex-col justify-between gap-5 lg:flex-row lg:items-end">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span className="rounded-full bg-[#deece6] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#176a5b]">Optimized prediction</span>
              <span className="text-xs text-[#777d77]">Updated for {cutoff} UTC decision</span>
            </div>
            <h1 className="font-display text-[34px] leading-tight tracking-[-0.035em] text-[#1e2925] md:text-[42px]">Day-ahead wind outlook</h1>
            <p className="mt-2 text-sm text-[#6e756f]">Plan tomorrow’s hourly procurement with a clear view of expected wind generation.</p>
          </div>

          <div className="flex flex-wrap items-end gap-2.5">
            <label className="control-label">
              <span>Delivery date</span>
              <span className="control-shell">
                <CalendarIcon />
                <input
                  type="date"
                  min={minDate}
                  max={maxDate}
                  value={deliveryDate}
                  onChange={(event) => {
                    if (event.target.value) setDeliveryDate(event.target.value)
                  }}
                  className="control-input"
                />
              </span>
            </label>
            <label className="control-label">
              <span>Decision time</span>
              <span className="control-shell">
                <ClockIcon />
                <select value={cutoff} onChange={(event) => setCutoff(event.target.value as Cutoff)} className="control-input appearance-none pr-5">
                  {cutoffs.map((time) => <option key={time} value={time}>{time} UTC</option>)}
                </select>
              </span>
            </label>
            <button onClick={() => setRefreshKey((key) => key + 1)} className="refresh-button" aria-label="Refresh outlook" title="Refresh outlook">
              <RefreshIcon spinning={loading} />
            </button>
          </div>
        </section>

        <section className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <article className="metric-card metric-card-primary">
            <p className="metric-label">Optimized volume</p>
            <p className="metric-value">{formatFixed(kpis ? kpis.total_daily_ml_mwh / 1000 : null, 1)} <span>GWh</span></p>
            <p className="metric-note">Across 24 delivery hours</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Expected accuracy</p>
            <p className="metric-value">{formatFixed(kpis?.expected_accuracy_pct, 1)}<span>%</span></p>
            <p className="metric-note">For the selected decision time</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Safe commitment</p>
            <p className="metric-value">{formatFixed(kpis ? kpis.total_daily_safe_firm_mwh / 1000 : null, 1)} <span>GWh</span></p>
            <p className="metric-note">Lower-risk daily volume</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Improvement vs raw</p>
            <p className="metric-value">{formatFixed(kpis?.realized_mae_reduction_pct, 1)}<span>%</span></p>
            <p className="metric-note">
              {kpis?.mean_mw_error_saved_per_hour == null
                ? 'Pending settlement'
                : `${formatNumber(kpis.mean_mw_error_saved_per_hour)} MW closer per hour`}
            </p>
          </article>
        </section>

        <section className="overflow-hidden rounded-2xl border border-[#dfe2da] bg-[#fcfbf7] shadow-[0_18px_50px_rgba(34,47,40,0.06)]">
          <div className="flex flex-col gap-4 border-b border-[#e5e7e1] px-5 py-5 md:px-7 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-display text-[24px] tracking-[-0.025em] text-[#25302b]">24-hour wind outlook</h2>
                {loading && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#176a5b]" />}
              </div>
              <p className="mt-1 text-xs text-[#777d77]">{formatDate(deliveryDate)} · Hourly generation in MW · Shaded area shows the expected range</p>
            </div>

            <div className="flex flex-wrap gap-2" aria-label="Chart series">
              {(Object.keys(seriesConfig) as SeriesKey[]).map((key) => {
                const config = seriesConfig[key]
                const disabled = key === 'actual' && !hasActuals
                return (
                  <button
                    key={key}
                    onClick={() => toggleSeries(key)}
                    aria-pressed={visible[key]}
                    disabled={disabled}
                    className={`series-toggle ${visible[key] ? 'series-toggle-active' : ''}`}
                  >
                    <span
                      className="series-swatch"
                      style={{ backgroundColor: visible[key] ? config.color : 'transparent', borderColor: config.color }}
                    />
                    {config.label}
                  </button>
                )
              })}
            </div>
          </div>

          {selected ? (
            <div className="grid lg:grid-cols-[minmax(0,1fr)_260px]">
              <div className="min-w-0 border-b border-[#e5e7e1] px-3 py-5 md:px-6 lg:border-b-0 lg:border-r">
                <ForecastChart slots={slots} visible={visible} selectedIndex={selectedIndex} onSelect={setSelectedIndex} />
              </div>

              <aside className="bg-[#f8f8f3] p-5 md:p-6">
                <div className="mb-6 flex items-start justify-between">
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#838983]">Selected hour</p>
                    <p className="mt-1 font-display text-[30px] text-[#25302b]">{selected.target_hour_utc}</p>
                  </div>
                  <span className="rounded-full border border-[#d8e4dd] bg-white px-2.5 py-1 text-[10px] font-semibold text-[#176a5b]">{formatNumber(selected.expected_accuracy_pct, 0)}% confidence</span>
                </div>

                <div className="space-y-1.5">
                  <div className="hour-value hour-value-highlight">
                    <span><i className="bg-[#176a5b]" />Optimized</span>
                    <strong>{formatNumber(selected.p50_ml_forecast_mw)} <small>MW</small></strong>
                  </div>
                  <div className="hour-value">
                    <span><i className="bg-[#78827b]" />Raw prediction</span>
                    <strong>{formatNumber(selected.latest_forecast_mw)} <small>MW</small></strong>
                  </div>
                  {selected.actual_wind_mw != null && (
                    <div className="hour-value">
                      <span><i className="bg-[#d29a45]" />Actual</span>
                      <strong>{formatNumber(selected.actual_wind_mw)} <small>MW</small></strong>
                    </div>
                  )}
                </div>

                <div className="my-6 h-px bg-[#dfe2dc]" />

                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#838983]">Planning range</p>
                  <div className="mt-3 flex items-end justify-between">
                    <div>
                      <p className="text-[11px] text-[#838983]">Safe level</p>
                      <p className="mt-1 text-sm font-semibold">{formatNumber(selected.p10_safe_floor_mw)} MW</p>
                    </div>
                    <div className="text-right">
                      <p className="text-[11px] text-[#838983]">Upper range</p>
                      <p className="mt-1 text-sm font-semibold">{formatNumber(selected.p90_upper_bound_mw)} MW</p>
                    </div>
                  </div>
                  <div className="range-track mt-3">
                    <span style={{ left: '22%', width: '56%' }} />
                    <i style={{ left: `${rangeMarkerPct}%` }} />
                  </div>
                </div>

                <div className="mt-6 rounded-xl border border-[#dce5df] bg-[#edf4f0] p-4">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#557168]">Hourly guidance</p>
                  <p className="mt-2 text-[13px] leading-5 text-[#35463f]">
                    Plan around <strong>{formatNumber(selected.p50_ml_forecast_mw)} MW</strong>
                    {rawDifference > 0 ? `, ${formatNumber(rawDifference)} MW below the raw prediction` : ''}.
                    {actualDifference != null && ` The optimized view is ${formatNumber(Math.abs(actualDifference))} MW from actual.`}
                  </p>
                </div>
              </aside>
            </div>
          ) : (
            <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 px-6 py-12 text-center">
              <p className="text-sm text-[#6e756f]">
                {loading ? 'Loading the latest forecast…' : 'No forecast is available for this date.'}
              </p>
              {!loading && (
                <button
                  onClick={() => setRefreshKey((key) => key + 1)}
                  className="rounded-md border border-[#d8b3ae] px-3 py-1 text-[12px] font-semibold text-[#8a3a32] hover:bg-white/60"
                >
                  Retry
                </button>
              )}
            </div>
          )}

          <div className="flex flex-col gap-2 border-t border-[#e5e7e1] bg-white/50 px-5 py-3 text-[11px] text-[#7c827d] sm:flex-row sm:items-center sm:justify-between md:px-7">
            <span>
              Decision view: {cutoff} UTC on the day before delivery
              {processTimeMs > 0 ? ` · ${processTimeMs.toFixed(0)} ms` : ''}
            </span>
            <span>Move across the chart to inspect each hour</span>
          </div>
        </section>
      </main>
    </div>
  )
}
