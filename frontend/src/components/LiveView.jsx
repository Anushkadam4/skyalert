import { useEffect, useState } from "react";
import { api, BANDS } from "../api.js";
import LightCurve from "./LightCurve.jsx";
import SkyMap from "./SkyMap.jsx";

function formatRaDec(ra, dec) {
  return `${ra.toFixed(4)}, ${dec >= 0 ? "+" : ""}${dec.toFixed(4)}`;
}

export function AlertRow({ a, selected, onSelect }) {
  return (
    <tr className={`${selected ? "selected" : ""} ${a.rb < 0.5 ? "bogus" : ""}`} onClick={() => onSelect(a.object_id)}>
      <td>{a.object_id}</td>
      <td className={`num band-${a.fid}`}>{a.magpsf.toFixed(2)} {BANDS[a.fid]}</td>
      <td className="num">{formatRaDec(a.ra, a.dec)}</td>
      <td className="num">{a.rb.toFixed(2)}</td>
      <td className="num">{a.ndethist}</td>
    </tr>
  );
}

export default function LiveView({ live, rules }) {
  const [selected, setSelected] = useState(null);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    const load = () => api("/stats").then(setStats).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  // Refresh the light curve whenever a new detection of the selected object arrives.
  const selectedDetections = live.alerts.filter((a) => a.object_id === selected).length;

  return (
    <>
      <div className="counters">
        <div><strong>{live.received.toLocaleString()}</strong><span>alerts this session</span></div>
        <div><strong>{stats ? stats.total_alerts.toLocaleString() : "–"}</strong><span>alerts stored</span></div>
        <div><strong>{stats ? stats.my_matches.toLocaleString() : "–"}</strong><span>matches for you</span></div>
        <div><strong>{stats?.stream_length?.toLocaleString() ?? "–"}</strong><span>in the stream buffer</span></div>
      </div>

      <div className="live-grid">
        <section className="panel">
          <div className="panel-head">
            <h2>Live sky</h2>
            <span className="muted small">last {live.alerts.length} alerts · bigger is brighter</span>
          </div>
          <SkyMap alerts={live.alerts} rules={rules} selected={selected} onSelect={setSelected} />
          <div className="legend">
            <span className="band-1">g band</span>
            <span className="band-2">r band</span>
            <span className="band-3">i band</span>
            <span className="cone-key">your watched regions</span>
            <span>faded = likely artefact</span>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Light curve</h2>
          </div>
          <LightCurve objectId={selected} refreshKey={selectedDetections} />
          <div className="panel-head" style={{ marginTop: 20 }}>
            <h2>Your latest matches</h2>
          </div>
          <div className="feed">
            {live.matches.length === 0 && (
              <p className="empty">No matches yet. Add a rule, and alerts that pass it will appear here.</p>
            )}
            {live.matches.slice(0, 20).map((m) => (
              <div className="feed-item" key={m.match_id} onClick={() => setSelected(m.alert.object_id)}>
                <span className="rule">{m.rule_name}</span>
                <span>{m.alert.object_id}</span>
                <span className={`band-${m.alert.fid}`}>{m.alert.magpsf.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-head">
          <h2>Incoming alerts</h2>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Object</th>
                <th className="num">Magnitude</th>
                <th className="num">RA, Dec (deg)</th>
                <th className="num">Real/bogus</th>
                <th className="num">Detections</th>
              </tr>
            </thead>
            <tbody>
              {live.alerts.slice(0, 25).map((a) => (
                <AlertRow key={a.candid} a={a} selected={a.object_id === selected} onSelect={setSelected} />
              ))}
            </tbody>
          </table>
          {live.alerts.length === 0 && (
            <p className="empty">Waiting for alerts. Start the producer to stream simulated alerts.</p>
          )}
        </div>
      </section>
    </>
  );
}
