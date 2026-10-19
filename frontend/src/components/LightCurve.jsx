import { useEffect, useState } from "react";
import { api, BANDS } from "../api.js";

const W = 340;
const H = 200;
const PAD = { l: 36, r: 8, t: 10, b: 26 };
const BAND_VAR = { 1: "var(--band-g)", 2: "var(--band-r)", 3: "var(--band-i)" };

/** Magnitude vs. time for one object. Brighter is up (smaller magnitude). */
export default function LightCurve({ objectId, refreshKey }) {
  const [points, setPoints] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!objectId) return;
    let cancelled = false;
    api(`/objects/${encodeURIComponent(objectId)}/lightcurve`)
      .then((p) => !cancelled && (setPoints(p), setError("")))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [objectId, refreshKey]);

  if (!objectId) return <p className="empty">Click a point on the map or a row in the table to see its light curve.</p>;
  if (error) return <p className="error">{error}</p>;
  if (!points.length) return <p className="empty">Loading {objectId}…</p>;

  const jds = points.map((p) => p.jd);
  const mags = points.flatMap((p) => [p.magpsf - p.sigmapsf, p.magpsf + p.sigmapsf]);
  const [j0, j1] = [Math.min(...jds), Math.max(...jds) || 1];
  const [m0, m1] = [Math.min(...mags) - 0.1, Math.max(...mags) + 0.1];
  const sx = (jd) => PAD.l + ((jd - j0) / Math.max(1e-6, j1 - j0)) * (W - PAD.l - PAD.r);
  const sy = (m) => PAD.t + ((m - m0) / Math.max(1e-6, m1 - m0)) * (H - PAD.t - PAD.b);
  const days = j1 - j0;
  const bands = [...new Set(points.map((p) => p.fid))].sort();

  return (
    <div>
      <p className="small">
        <strong>{objectId}</strong> <span className="muted">· {points.length} detections over {days.toFixed(1)} days</span>
      </p>
      <svg className="lightcurve" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Light curve of ${objectId}`}>
        <g className="axis">
          <line x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={H - PAD.b} />
          <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} />
        </g>
        <text x={PAD.l - 4} y={PAD.t + 8} textAnchor="end">{m0.toFixed(1)}</text>
        <text x={PAD.l - 4} y={H - PAD.b} textAnchor="end">{m1.toFixed(1)}</text>
        <text x={PAD.l} y={H - 8}>0 d</text>
        <text x={W - PAD.r} y={H - 8} textAnchor="end">{days.toFixed(1)} d</text>
        {points.map((p) => (
          <g key={p.candid} stroke={BAND_VAR[p.fid]} fill={BAND_VAR[p.fid]}>
            <line x1={sx(p.jd)} x2={sx(p.jd)} y1={sy(p.magpsf - p.sigmapsf)} y2={sy(p.magpsf + p.sigmapsf)} />
            <circle cx={sx(p.jd)} cy={sy(p.magpsf)} r={2.5} stroke="none" />
          </g>
        ))}
      </svg>
      <div className="legend">
        {bands.map((b) => (
          <span key={b} className={`band-${b}`}>{BANDS[b]} band</span>
        ))}
      </div>
    </div>
  );
}
