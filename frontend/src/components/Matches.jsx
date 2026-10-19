import { useCallback, useEffect, useState } from "react";
import { api, BANDS } from "../api.js";
import LightCurve from "./LightCurve.jsx";

export default function Matches({ live, rules }) {
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [ruleId, setRuleId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const { markSeen, unseenMatches } = live;

  const load = useCallback(
    async (from) => {
      setLoading(true);
      try {
        const qs = new URLSearchParams({ limit: "50" });
        if (from) qs.set("cursor", from);
        if (ruleId) qs.set("rule_id", ruleId);
        const page = await api(`/matches?${qs}`);
        setItems((prev) => (from ? [...prev, ...page.items] : page.items));
        setCursor(page.next_cursor);
        setError("");
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [ruleId],
  );

  useEffect(() => {
    load(null);
    markSeen();
  }, [load, markSeen]);

  return (
    <div className="live-grid">
      <section className="panel">
        <div className="panel-head">
          <h2>My matches</h2>
          <div className="row" style={{ flex: "0 1 auto" }}>
            <select value={ruleId} onChange={(e) => setRuleId(e.target.value)} aria-label="Filter by rule">
              <option value="">All rules</option>
              {rules.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
            <button onClick={() => (load(null), markSeen())} disabled={loading}>
              {unseenMatches > 0 ? `Show ${unseenMatches} new` : "Refresh"}
            </button>
          </div>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rule</th>
                <th>Object</th>
                <th className="num">Magnitude</th>
                <th className="num">Real/bogus</th>
                <th>Matched at</th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id} className={m.alert.object_id === selected ? "selected" : ""}
                  onClick={() => setSelected(m.alert.object_id)}>
                  <td>{m.rule_name}</td>
                  <td>{m.alert.object_id}</td>
                  <td className={`num band-${m.alert.fid}`}>{m.alert.magpsf.toFixed(2)} {BANDS[m.alert.fid]}</td>
                  <td className="num">{m.alert.rb.toFixed(2)}</td>
                  <td>{new Date(m.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && items.length === 0 && (
          <p className="empty">Nothing has matched yet. Rules only catch alerts that arrive after they are created.</p>
        )}
        {cursor && (
          <button onClick={() => load(cursor)} disabled={loading} style={{ marginTop: 12 }}>
            Load older matches
          </button>
        )}
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>Light curve</h2>
        </div>
        <LightCurve objectId={selected} />
      </section>
    </div>
  );
}
