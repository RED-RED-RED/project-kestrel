// src/components/RadioStatus.jsx
// Per-radio live health from node heartbeats (radio_status). The sidebar shows
// one chip per band; the System view shows the full table. Nodes that predate
// radio_status fall back to the old coarse WiFi / BT5 / SDR chips.

const STATE = {
  running:  { color: 'var(--threat-low)',  border: 'var(--threat-low-border)',  bg: 'var(--threat-low-bg)',  text: 'running' },
  degraded: { color: 'var(--threat-med)',  border: 'var(--threat-med-border)',  bg: 'var(--threat-med-bg)',  text: 'degraded' },
  down:     { color: 'var(--threat-high)', border: 'var(--threat-high-border)', bg: 'var(--threat-high-bg)', text: 'down' },
  starting: { color: 'var(--muted)', border: 'var(--bg-border)', bg: 'transparent', text: 'starting' },
  off:      { color: 'var(--muted)', border: 'var(--bg-border)', bg: 'transparent', text: 'off' },
}
const stateOf = s => STATE[s] || STATE.starting

const SDR_BANDS = { '433': '433', '900': '915', '1g2': '1.2G', '2g4': '2.4G', '5g8': '5.8G' }

// Short chip label per radio entry. One SDR shows "SDR"; several show their serial.
function chipLabel(r, sdrCount) {
  switch (r.id) {
    case 'wifi_2g': return '2.4'
    case 'wifi_5g': return '5'
    case 'bt':      return 'BT'
    case 'adsb':    return 'ADS-B'
    case 'gps':     return 'GPS'
  }
  if (r.kind === 'sdr') {
    if (sdrCount <= 1) return 'SDR'
    return r.detail?.serial || r.detail?.backend || 'SDR'
  }
  return r.label || r.id
}

// Bluetooth Long Range gets its own chip: it's a separate capability that can
// be off while BT4 still works.
function lrChip(bt) {
  if (!bt || bt.state === 'off') return null
  const lr = bt.detail?.long_range
  const state = lr ? bt.state : (lr === false ? 'degraded' : 'starting')
  const why = lr ? 'BT5 Long Range (Coded PHY) on' : 'BT5 Long Range off: ' + (bt.error || 'not supported')
  return { key: 'bt_lr', label: 'LR', state, title: why }
}

export function RadioChips({ node }) {
  const status = node.radio_status
  const offline = node.status !== 'online'

  if (!Array.isArray(status) || status.length === 0) {
    const radios = node.radios?.length ? node.radios : ['wifi', 'bt']
    return (
      <div className="rs-chips">
        {radios.includes('wifi') && <span className="sb-radio r-wifi">WiFi</span>}
        {radios.includes('bt')   && <span className="sb-radio r-bt">BT5</span>}
        {radios.includes('sdr')  && <span className="sb-radio r-sdr">SDR</span>}
      </div>
    )
  }

  const sdrCount = status.filter(r => r.kind === 'sdr').length
  const chips = []
  for (const r of status) {
    if (r.state === 'off') continue                       // disabled in config: no chip
    chips.push({ key: r.id, label: chipLabel(r, sdrCount), state: r.state,
                 title: `${r.label}: ${stateOf(r.state).text}${r.error ? ' (' + r.error + ')' : ''}` })
    if (r.id === 'bt') {
      const lr = lrChip(r)
      if (lr) chips.push(lr)
    }
  }
  // GPS last, so the bands read left to right
  chips.sort((a, b) => (a.key === 'gps') - (b.key === 'gps'))

  return (
    <div className="rs-chips">
      {chips.map(c => {
        const s = offline ? STATE.off : stateOf(c.state)
        return (
          <span key={c.key} className="rs-chip" title={offline ? `${c.title} (last report, node offline)` : c.title}
                style={{ color: s.color, borderColor: s.border, background: s.bg }}>
            {c.label}
          </span>
        )
      })}
    </div>
  )
}

const n = v => (v == null ? '—' : Number(v).toLocaleString())

function detailText(r) {
  const d = r.detail || {}
  // A down radio has no live rates worth showing: identify it, the reason follows
  if (r.state === 'down') {
    return [d.iface, d.hci, d.serial, d.port].filter(Boolean).join(' · ')
  }
  switch (r.kind) {
    case 'wifi': {
      const parts = [d.iface, `${n(d.frames_per_min)} frames/min`, `${n(d.rid_10m)} RID /10 min`]
      if (d.hop_errors_per_min) parts.push(`${d.hop_errors_per_min} channel errors/min`)
      return parts.filter(Boolean).join(' · ')
    }
    case 'bt':
      return [d.hci, d.long_range ? 'Long Range on' : 'Long Range off',
              `${n(d.adverts_per_min)} adverts/min`, `${n(d.rid_10m)} RID /10 min`]
        .filter(Boolean).join(' · ')
    case 'sdr': {
      const bands = (d.bands || []).map(b => SDR_BANDS[b] || b).join(' ')
      return [bands || null, `${n(d.captures_per_min)} captures/min`, `${n(d.links_10m)} links /10 min`]
        .filter(Boolean).join(' · ')
    }
    case 'adsb': {
      const sn = d.signal_db != null && d.noise_db != null
        ? `signal ${d.signal_db.toFixed(1)} / noise ${d.noise_db.toFixed(1)} dB` : null
      return [`${n(d.aircraft)} aircraft`, `${n(d.messages_per_min)} msgs/min`, sn]
        .filter(Boolean).join(' · ')
    }
    case 'gps': {
      // "no fix" shows as the reason line when it applies
      const parts = [`${n(d.sats)} sats`, ...(d.fix ? ['fix'] : [])]
      if (d.ublox) {
        parts.push(`jamming ${d.jamming || '—'}`, `spoofing ${d.spoofing || '—'}`,
                   d.survey ? 'survey done' : 'survey pending')
      }
      return parts.join(' · ')
    }
  }
  return ''
}

// "updated 40 s ago" for the last full status report
function ago(iso) {
  if (!iso) return null
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 90) return `${s} s ago`
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)} h ago`
}

export function RadioTable({ node }) {
  const status = node?.radio_status
  if (!Array.isArray(status) || status.length === 0) {
    return <div className="rs-none">Per-radio status needs a node update (scripts/update.sh node).</div>
  }
  const offline = node.status !== 'online'
  // Problems first, then in the node's own order
  const rank = { down: 0, degraded: 1, starting: 2, running: 3, off: 4 }
  const rows = [...status].sort((a, b) => (rank[a.state] ?? 2) - (rank[b.state] ?? 2))
  return (
    <div className="rs-table">
      {rows.map(r => {
        const s = offline ? STATE.off : stateOf(r.state)
        return (
          <div key={r.id} className={`rs-row ${r.state === 'off' ? 'rs-off' : ''}`}>
            <span className="rs-dot" style={{ background: s.color }} />
            <div className="rs-main">
              <div className="rs-line">
                <span className="rs-name">{r.label}</span>
                <span className="rs-state" style={{ color: s.color }}>{offline ? 'last: ' : ''}{stateOf(r.state).text}</span>
              </div>
              {r.state === 'off'
                ? <div className="rs-detail">off in config</div>
                : <div className="rs-detail">{detailText(r)}</div>}
              {r.error && r.state !== 'running' && <div className="rs-error" style={{ color: s.color }}>{r.error}</div>}
            </div>
          </div>
        )
      })}
      {node.radio_status_at && <div className="rs-ago">updated {ago(node.radio_status_at)}</div>}
    </div>
  )
}

export function radioSummary(node) {
  const status = (node?.radio_status || []).filter(r => r.state !== 'off')
  if (!status.length) return null
  const bad = status.filter(r => r.state === 'down').length
  const warn = status.filter(r => r.state === 'degraded').length
  const ok = status.length - bad - warn
  return { ok, warn, bad, total: status.length }
}

export const radioCss = `
.rs-chips { display:flex; flex-wrap:wrap; gap:3px; margin-top:4px; }
.rs-chip { font-size:7px; font-family:var(--mono); letter-spacing:0.5px; padding:1px 4px;
  border-radius:2px; border:1px solid; text-transform:uppercase; cursor:default; }
.rs-table { display:flex; flex-direction:column; gap:6px; margin-top:8px; }
.rs-row { display:flex; gap:7px; align-items:flex-start; }
.rs-row.rs-off { opacity:0.55; }
.rs-dot { width:7px; height:7px; border-radius:50%; margin-top:4px; flex-shrink:0; }
.rs-main { min-width:0; flex:1; }
.rs-line { display:flex; justify-content:space-between; gap:8px; }
.rs-name { font-family:var(--mono); font-size:11px; color:var(--text); }
.rs-state { font-family:var(--mono); font-size:10px; text-transform:uppercase; letter-spacing:0.5px; }
.rs-detail { font-family:var(--mono); font-size:10px; color:var(--text-dim); overflow-wrap:anywhere; }
.rs-error { font-family:var(--mono); font-size:10px; overflow-wrap:anywhere; }
.rs-none { font-family:var(--mono); font-size:10px; color:var(--muted); margin-top:8px; }
.rs-ago { font-family:var(--mono); font-size:9px; color:var(--muted); }
.rs-toggle { background:none; border:none; padding:0; cursor:pointer; font-family:var(--mono);
  font-size:10px; color:var(--text-secondary); display:flex; gap:6px; align-items:center; margin-top:8px; }
.rs-toggle:hover { color:var(--text); }
.rs-toggle:focus-visible { outline:1px solid var(--steel-blue); outline-offset:2px; }
`
